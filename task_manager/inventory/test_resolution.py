"""
Tests for the resolution engine hardened classification.

Proves:
  - finding_code is the source of truth (message wording doesn't change classification)
  - root cause detection uses structured entity references, not message text
  - dependency graph produces correct root/dependent relationships
  - provisional mappings are not auto-applied
  - resolution output is deterministic
"""
import os
import tempfile

from django.test import TestCase

from inventory.migration_engine import (
    DryRunEngine, Status, Severity, FindingCode,
)
from inventory.resolution import (
    classify_findings, CLASSIFICATION_RULES, DEPENDENCY_GRAPH,
    PROVISIONAL_MAPPINGS, Resolution, Finding, RULE_DESCRIPTIONS,
)


# ============================================================
# HELPERS
# ============================================================

def _create_test_db(tables_and_data):
    """Create a temporary SQLite database with specified tables and data."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    import sqlite3
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    for table_name, columns, rows in tables_and_data:
        col_defs = ', '.join(f'{c} TEXT' for c in columns)
        cur.execute(f'CREATE TABLE {table_name} ({col_defs})')
        for row in rows:
            placeholders = ', '.join(['?'] * len(row))
            cur.execute(f'INSERT INTO {table_name} VALUES ({placeholders})', row)
    conn.commit()
    conn.close()
    return path


def _make_db_with_orphan_product():
    """
    Create DB where Product #10 has no category -> Batch #19 orphaned -> 2 packages orphaned.
    Product #21 references category #3 ("test").
    """
    return _create_test_db([
        ('stock_meat_category', ['ids', 'name_type'], [
            ('1', 'PORK'),
            ('2', 'CHICKEN'),
            ('3', 'test'),
        ]),
        ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [
            ('1', 'S1', ''),
        ]),
        ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
            ('1', 'Pork', '8001', '185', '31.7', '6.2', '1'),
            ('10', 'Chicken_Mid_Wing', '1002', '200', '18', '12', None),  # NO category
            ('21', 'Test_Product', '9001', '0', '0', '0', '3'),  # category = "test"
        ]),
        ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
            ('1', '1', '1', '1', '1', '0.0', '82', '97', '2024-01-15'),
            ('19', '10', '1', '1', '1', '0.0', '50', '60', '2024-01-15'),
            ('21', '21', '1', '1', '1', '0.0', '10', '12', '2024-01-15'),
        ]),
        ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
            ('1', '1', '1-1-8001-0001', '850', '82', 'frozen', '0', '', '', '', '0', '2024-01-15'),
            ('100', '19', '1-1-1002-0001', '500', '50', 'frozen', '0', '', '', '', '0', '2024-01-15'),
            ('101', '19', '1-1-1002-0002', '600', '60', 'frozen', '0', '', '', '', '0', '2024-01-15'),
            ('200', '21', '1-1-9001-0001', '300', '30', 'frozen', '0', '', '', '', '0', '2024-01-15'),
        ]),
    ])


def _get_findings_for(classification, entity, legacy_id):
    """Get findings for entity+id, handling string/int legacy_id from SQLite."""
    return [f for f in classification['findings']
            if f.entity == entity and int(f.legacy_id) == int(legacy_id)]


# ============================================================
# TEST 1: Changing issue message doesn't change classification
# ============================================================

class TestMessageIndependence(TestCase):
    """finding_code determines classification, NOT message wording."""

    def test_changed_message_same_finding_code(self):
        f1 = Finding(
            finding_code=FindingCode.CATEGORY_EMPTY,
            entity='Category', legacy_id=1,
            resolution=CLASSIFICATION_RULES[FindingCode.CATEGORY_EMPTY],
            rule=RULE_DESCRIPTIONS[FindingCode.CATEGORY_EMPTY],
            message='Category name is empty',
        )
        f2 = Finding(
            finding_code=FindingCode.CATEGORY_EMPTY,
            entity='Category', legacy_id=2,
            resolution=CLASSIFICATION_RULES[FindingCode.CATEGORY_EMPTY],
            rule=RULE_DESCRIPTIONS[FindingCode.CATEGORY_EMPTY],
            message='Name field is blank and cannot be used',
        )
        self.assertEqual(f1.resolution, f2.resolution,
            'Same finding_code must produce same resolution regardless of message')
        self.assertEqual(f1.finding_code, f2.finding_code)

    def test_all_finding_codes_have_classification(self):
        all_codes = [
            FindingCode.CATEGORY_EMPTY, FindingCode.CATEGORY_TEST_DATA, FindingCode.CATEGORY_DUPLICATE,
            FindingCode.SUPPLIER_EMPTY, FindingCode.SUPPLIER_DUPLICATE,
            FindingCode.PRODUCT_CATEGORY_MISSING, FindingCode.PRODUCT_CATEGORY_INVALID,
            FindingCode.PRODUCT_DUPLICATE_SKU, FindingCode.PRODUCT_NAME_EMPTY,
            FindingCode.BATCH_INVALID_PRODUCT, FindingCode.BATCH_WEIGHT_ZERO,
            FindingCode.BATCH_MISSING_SUPPLIER, FindingCode.BATCH_INVALID_LOT,
            FindingCode.PACKAGE_ORPHAN_PRODUCT, FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS,
            FindingCode.PACKAGE_STATE_CONFLICT, FindingCode.PACKAGE_DUPLICATE_BARCODE,
            FindingCode.PACKAGE_EMPTY_BARCODE, FindingCode.PACKAGE_INVALID_WEIGHT,
            FindingCode.PACKAGE_NEGATIVE_PRICE, FindingCode.PACKAGE_DUPLICATE_LOYVERSE_SKU,
        ]
        for code in all_codes:
            self.assertIn(code, CLASSIFICATION_RULES,
                f'FindingCode.{code} missing from CLASSIFICATION_RULES')


# ============================================================
# TEST 2: Root cause detection uses structured entity references
# ============================================================

class TestRootCauseDetection(TestCase):
    """Root cause detection must NOT depend on message wording."""

    def test_root_cause_by_entity_not_message(self):
        db_path = _make_db_with_orphan_product()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            classification = classify_findings(engine.results)

            batch_findings = _get_findings_for(classification, 'Batch', 19)
            self.assertTrue(len(batch_findings) > 0, 'Should have finding for Batch #19')

            batch_f = batch_findings[0]
            self.assertEqual(batch_f.root_cause_entity, 'Product',
                'Root cause must reference Product entity, not message text')
            self.assertEqual(int(batch_f.root_cause_legacy_id), 10,
                'Root cause must reference Product #10 (the actual root, not the batch product_info id)')
            self.assertIn(FindingCode.PRODUCT_CATEGORY_MISSING, batch_f.depends_on_codes,
                'Batch must depend on PRODUCT_CATEGORY_MISSING')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 3: CATEGORY_DUPLICATE has its own finding code
# ============================================================

class TestCategoryDuplicateCode(TestCase):
    """CATEGORY_DUPLICATE must not reuse CATEGORY_EMPTY."""

    def test_category_duplicate_code_is_distinct(self):
        self.assertNotEqual(FindingCode.CATEGORY_DUPLICATE, FindingCode.CATEGORY_EMPTY,
            'CATEGORY_DUPLICATE must have its own code')
        self.assertIn(FindingCode.CATEGORY_DUPLICATE, CLASSIFICATION_RULES)

    def test_category_duplicate_resolution(self):
        self.assertEqual(CLASSIFICATION_RULES[FindingCode.CATEGORY_DUPLICATE], Resolution.MANUAL_REVIEW)

    def test_category_test_data_is_exception(self):
        self.assertEqual(CLASSIFICATION_RULES[FindingCode.CATEGORY_TEST_DATA], Resolution.ACCEPTED_EXCEPTION)


# ============================================================
# TEST 4: Product #21 has exactly one resolution category
# ============================================================

class TestProduct21Classification(TestCase):
    """Product #21 (test data) must have exactly one resolution: ACCEPTED_EXCEPTION."""

    def test_product_21_one_resolution(self):
        db_path = _make_db_with_orphan_product()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            classification = classify_findings(engine.results)

            product_21_findings = _get_findings_for(classification, 'Product', 21)
            self.assertTrue(len(product_21_findings) > 0,
                'Should have at least one finding for Product #21')

            resolutions = set(f.resolution for f in product_21_findings)
            self.assertEqual(len(resolutions), 1,
                f'Product #21 should have exactly one resolution, got: {resolutions}')

            self.assertEqual(product_21_findings[0].resolution, Resolution.ACCEPTED_EXCEPTION,
                'Product #21 should be ACCEPTED_EXCEPTION (skip test data), not MIGRATION_BLOCKER')

            self.assertEqual(product_21_findings[0].finding_code, FindingCode.PRODUCT_CATEGORY_INVALID)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 5: Product #10 generates dependent Batch/Package findings
# ============================================================

class TestProduct10DependencyChain(TestCase):
    """Product #10 (no category) must generate dependent Batch and Package findings."""

    def test_product_10_generates_downstream(self):
        db_path = _make_db_with_orphan_product()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            classification = classify_findings(engine.results)

            # Product #10 finding
            prod_findings = _get_findings_for(classification, 'Product', 10)
            self.assertTrue(len(prod_findings) > 0)
            self.assertEqual(prod_findings[0].finding_code, FindingCode.PRODUCT_CATEGORY_MISSING)
            self.assertEqual(prod_findings[0].resolution, Resolution.MIGRATION_BLOCKER)

            # Batch #19 (depends on Product via meat_parts #10)
            batch_findings = _get_findings_for(classification, 'Batch', 19)
            self.assertTrue(len(batch_findings) > 0)
            self.assertEqual(batch_findings[0].finding_code, FindingCode.BATCH_INVALID_PRODUCT)
            self.assertEqual(batch_findings[0].root_cause_entity, 'Product')

            # Packages #100, #101
            pkg_100 = _get_findings_for(classification, 'Package', 100)
            pkg_101 = _get_findings_for(classification, 'Package', 101)
            self.assertEqual(len(pkg_100), 1)
            self.assertEqual(len(pkg_101), 1)
            for pf in pkg_100 + pkg_101:
                self.assertEqual(pf.finding_code, FindingCode.PACKAGE_ORPHAN_PRODUCT)
                self.assertEqual(pf.root_cause_entity, 'Product')
                self.assertIn(FindingCode.PRODUCT_CATEGORY_MISSING, pf.depends_on_codes)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 6: Dependency graph produces correct root/dependent relationships
# ============================================================

class TestDependencyGraph(TestCase):
    """DEPENDENCY_GRAPH must correctly identify root causes and their dependents."""

    def test_product_category_missing_resolves_batch_and_package(self):
        dep = DEPENDENCY_GRAPH[FindingCode.PRODUCT_CATEGORY_MISSING]
        self.assertIn(FindingCode.BATCH_INVALID_PRODUCT, dep['resolves'])
        self.assertIn(FindingCode.PACKAGE_ORPHAN_PRODUCT, dep['resolves'])

    def test_leaf_nodes_have_empty_resolves(self):
        for code in [FindingCode.BATCH_INVALID_PRODUCT, FindingCode.PACKAGE_ORPHAN_PRODUCT,
                     FindingCode.CATEGORY_EMPTY, FindingCode.SUPPLIER_EMPTY,
                     FindingCode.PACKAGE_EMPTY_BARCODE]:
            dep = DEPENDENCY_GRAPH.get(code, {})
            self.assertEqual(dep.get('resolves', []), [],
                f'{code} should be a leaf node with empty resolves')

    def test_classification_has_root_causes_and_dependents(self):
        db_path = _make_db_with_orphan_product()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            classification = classify_findings(engine.results)
            self.assertGreater(classification['summary']['root_causes'], 0,
                'Should have at least one root cause')
            self.assertGreater(classification['summary']['dependent_findings'], 0,
                'Should have at least one dependent finding')
            self.assertGreater(len(classification['dependency_chains']), 0,
                'Should have dependency chains')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 7: Pending mapping is marked provisional
# ============================================================

class TestProvisionalMapping(TestCase):
    """Provisional mappings must not be auto-applied."""

    def test_pending_mapping_is_approved(self):
        pm = PROVISIONAL_MAPPINGS['pending_to_packed']
        self.assertEqual(pm['status'], 'APPROVED')
        self.assertFalse(pm['requires_business_confirmation'])
        self.assertEqual(pm['confidence'], 'HIGH')

    def test_approved_mappings_do_not_require_confirmation(self):
        for key in ['pending_to_packed', 'product_10_to_chicken']:
            pm = PROVISIONAL_MAPPINGS[key]
            self.assertFalse(pm['requires_business_confirmation'],
                f'{key} should not require confirmation (approved)')
        # Duplicate SKU still requires confirmation
        pm = PROVISIONAL_MAPPINGS['assign_new_skus']
        self.assertTrue(pm['requires_business_confirmation'])

    def test_product_10_mapping_is_approved(self):
        pm = PROVISIONAL_MAPPINGS['product_10_to_chicken']
        self.assertEqual(pm['status'], 'APPROVED')
        self.assertFalse(pm['requires_business_confirmation'])


# ============================================================
# TEST 8: Duplicate SKU resolution doesn't invent final SKU
# ============================================================

class TestDuplicateSkuResolution(TestCase):
    """Duplicate SKU resolution must be MANUAL_REVIEW, not AUTO_FIX_SAFE."""

    def test_duplicate_sku_is_manual_review(self):
        self.assertEqual(CLASSIFICATION_RULES[FindingCode.PRODUCT_DUPLICATE_SKU],
                        Resolution.MANUAL_REVIEW)

    def test_assign_new_sku_pending(self):
        pm = PROVISIONAL_MAPPINGS['assign_new_skus']
        self.assertEqual(pm['status'], 'PENDING_FINAL_SKU')
        self.assertTrue(pm['requires_business_confirmation'])


# ============================================================
# TEST 9: Resolution output is deterministic
# ============================================================

class TestDeterministicResolution(TestCase):
    """Same data -> same classification every time."""

    def test_same_results_twice(self):
        db_path = _make_db_with_orphan_product()
        try:
            engine1 = DryRunEngine(db_path)
            engine1.run()
            c1 = classify_findings(engine1.results)

            engine2 = DryRunEngine(db_path)
            engine2.run()
            c2 = classify_findings(engine2.results)

            self.assertEqual(len(c1['findings']), len(c2['findings']))
            codes1 = [f.finding_code for f in c1['findings']]
            codes2 = [f.finding_code for f in c2['findings']]
            self.assertEqual(codes1, codes2)
            res1 = [f.resolution for f in c1['findings']]
            res2 = [f.resolution for f in c2['findings']]
            self.assertEqual(res1, res2)
            self.assertEqual(c1['summary'], c2['summary'])
        finally:
            os.unlink(db_path)
