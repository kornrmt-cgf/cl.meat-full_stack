"""
Tests for the resolution apply workflow.

Proves:
  - BATCH_MISSING_SUPPLIER is MANUAL_REVIEW (not AUTO_FIX_SAFE)
  - ResolutionApplier.preview() produces audit trail
  - ResolutionApplier.apply() mutates candidates correctly
  - Running apply twice is idempotent (no duplicate mutations)
  - Product#10→CHICKEN unblocks the chain
  - pending→PACKED unblocks packages
  - Duplicate SKU decisions are recorded without mutation
  - Legacy database remains unchanged
"""
import os
import tempfile

from django.test import TestCase

from inventory.migration_engine import (
    DryRunEngine, Status, Severity, FindingCode, file_hash,
)
from inventory.resolution import (
    classify_findings, CLASSIFICATION_RULES, Resolution,
    ResolutionApplier, AuditTrail, APPROVED_RULES,
)


# ============================================================
# HELPERS
# ============================================================

def _create_test_db(tables_and_data):
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


def _make_resolution_db():
    """
    DB with:
    - Product #10: no category (PRODUCT_CATEGORY_MISSING)
    - Product #21: category="test" (PRODUCT_CATEGORY_INVALID)
    - Batch #19: references Product #19→meat_parts #10 (orphan)
    - Package #100, #101: reference Product #19→meat_parts #10 (orphan)
    - Package #200: references Product #21→meat_parts #21 (test, skipped)
    - Package #300: storage_status="pending" (unknown)
    - Product #3: duplicate SKU MP-8001
    - Product #4: duplicate SKU MP-8001
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
            ('3', 'Pork_A', '8001', '185', '31.7', '6.2', '1'),  # duplicate SKU with #4
            ('4', 'Pork_B', '8001', '190', '30', '7', '1'),     # duplicate SKU with #3
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
            ('300', '1', '1-1-8001-0002', '400', '40', 'pending', '0', '', '', '', '0', '2024-01-15'),
        ]),
    ])


def _get_findings_for(classification, entity, legacy_id):
    return [f for f in classification['findings']
            if f.entity == entity and int(f.legacy_id) == int(legacy_id)]


# ============================================================
# TEST: BATCH_MISSING_SUPPLIER is MANUAL_REVIEW
# ============================================================

class TestBatchMissingSupplierReclassification(TestCase):
    def test_batch_missing_supplier_is_manual_review(self):
        self.assertEqual(CLASSIFICATION_RULES[FindingCode.BATCH_MISSING_SUPPLIER],
                        Resolution.MANUAL_REVIEW,
                        'BATCH_MISSING_SUPPLIER must be MANUAL_REVIEW, not AUTO_FIX_SAFE')


# ============================================================
# TEST: ResolutionApplier preview produces audit trail
# ============================================================

class TestResolutionPreview(TestCase):
    def test_preview_produces_entries(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            self.assertGreater(len(trail.entries), 0, 'Preview should produce audit entries')
        finally:
            os.unlink(db_path)

    def test_preview_does_not_mutate(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            # Snapshot statuses before
            statuses_before = {c.legacy_id: c.status for c in engine.results['products']}
            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            # Verify no mutation
            for c in engine.results['products']:
                self.assertEqual(c.status, statuses_before[c.legacy_id],
                    f'Preview must not mutate Product #{c.legacy_id}')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: ResolutionApplier apply mutates correctly
# ============================================================

class TestResolutionApply(TestCase):
    def test_product_10_category_applied(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            applied = applier.apply(engine.results, trail)
            self.assertGreater(applied, 0, 'Should apply at least one resolution')

            # Product #10 should now have CHICKEN category
            prod_10 = [c for c in engine.results['products']
                      if int(c.legacy_id) == 10]
            self.assertEqual(len(prod_10), 1)
            self.assertEqual(prod_10[0].data.get('category_code'), 'CHICKEN')
        finally:
            os.unlink(db_path)

    def test_pending_to_packed_applied(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            applier.apply(engine.results, trail)

            # Package #300 should now be PACKED
            pkg_300 = [c for c in engine.results['packages']
                      if int(c.legacy_id) == 300]
            self.assertEqual(len(pkg_300), 1)
            self.assertEqual(pkg_300[0].data.get('canonical_state'), 'PACKED')
        finally:
            os.unlink(db_path)

    def test_duplicate_sku_recorded_no_mutation(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            applier.apply(engine.results, trail)

            # Duplicate SKU products should still have WARNING status (no mutation)
            prod_3 = [c for c in engine.results['products'] if int(c.legacy_id) == 3]
            prod_4 = [c for c in engine.results['products'] if int(c.legacy_id) == 4]
            self.assertEqual(prod_3[0].status, Status.WARNING)
            self.assertEqual(prod_4[0].status, Status.WARNING)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: Idempotency
# ============================================================

class TestResolutionIdempotency(TestCase):
    def test_apply_twice_same_result(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            applier.apply(engine.results, trail)

            # Snapshot after first apply
            prod_10_cat = [c for c in engine.results['products']
                          if int(c.legacy_id) == 10][0].data.get('category_code')
            pkg_300_state = [c for c in engine.results['packages']
                            if int(c.legacy_id) == 300][0].data.get('canonical_state')

            # Apply again
            trail2 = applier.preview(engine.results)
            applier.apply(engine.results, trail2)

            # Same result
            prod_10_cat_2 = [c for c in engine.results['products']
                            if int(c.legacy_id) == 10][0].data.get('category_code')
            pkg_300_state_2 = [c for c in engine.results['packages']
                              if int(c.legacy_id) == 300][0].data.get('canonical_state')

            self.assertEqual(prod_10_cat, prod_10_cat_2)
            self.assertEqual(pkg_300_state, pkg_300_state_2)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: Source database unchanged
# ============================================================

class TestSourceDatabaseUnchanged(TestCase):
    def test_legacy_db_unchanged_after_apply(self):
        db_path = _make_resolution_db()
        try:
            hash_before = file_hash(db_path)
            engine = DryRunEngine(db_path)
            engine.run()
            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            applier.apply(engine.results, trail)
            hash_after = file_hash(db_path)
            self.assertEqual(hash_before, hash_after,
                'Legacy database must not be modified by resolution apply')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: Findings delta after resolution
# ============================================================

class TestFindingsDelta(TestCase):
    def test_product_10_chain_resolved(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            before = classify_findings(engine.results)

            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            applier.apply(engine.results, trail)

            after = classify_findings(engine.results)

            # Product #10 should no longer have PRODUCT_CATEGORY_MISSING
            prod_findings_before = _get_findings_for(before, 'Product', 10)
            prod_findings_after = _get_findings_for(after, 'Product', 10)
            missing_before = [f for f in prod_findings_before
                             if f.finding_code == FindingCode.PRODUCT_CATEGORY_MISSING]
            missing_after = [f for f in prod_findings_after
                            if f.finding_code == FindingCode.PRODUCT_CATEGORY_MISSING]
            self.assertGreater(len(missing_before), 0, 'Before: should have PRODUCT_CATEGORY_MISSING')
            self.assertEqual(len(missing_after), 0, 'After: PRODUCT_CATEGORY_MISSING should be resolved')
        finally:
            os.unlink(db_path)

    def test_pending_packages_resolved(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            before = classify_findings(engine.results)

            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            applier.apply(engine.results, trail)

            after = classify_findings(engine.results)

            # Package #300 should no longer have PACKAGE_UNKNOWN_STORAGE_STATUS
            pkg_before = _get_findings_for(before, 'Package', 300)
            pkg_after = _get_findings_for(after, 'Package', 300)
            unknown_before = [f for f in pkg_before
                             if f.finding_code == FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS]
            unknown_after = [f for f in pkg_after
                            if f.finding_code == FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS]
            self.assertGreater(len(unknown_before), 0, 'Before: should have PACKAGE_UNKNOWN_STORAGE_STATUS')
            self.assertEqual(len(unknown_after), 0, 'After: PACKAGE_UNKNOWN_STORAGE_STATUS should be resolved')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: Audit trail completeness
# ============================================================

class TestAuditTrail(TestCase):
    def test_product_10_entries_have_required_fields(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            applier = ResolutionApplier()
            trail = applier.preview(engine.results)

            product_entries = [e for e in trail.entries
                              if e.entity == 'Product' and int(e.legacy_id) == 10]
            self.assertGreater(len(product_entries), 0,
                'Should have audit entries for Product #10')
            for e in product_entries:
                self.assertEqual(e.rule_id, 'RESOLVE_PRODUCT_10_CATEGORY')
                self.assertFalse(e.requires_approval)
                if e.field == 'category_code':
                    self.assertEqual(e.new_value, 'CHICKEN')
                elif e.field == 'category_legacy_id':
                    self.assertEqual(e.new_value, '2')
        finally:
            os.unlink(db_path)

    def test_pending_entries_have_required_fields(self):
        db_path = _make_resolution_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            applier = ResolutionApplier()
            trail = applier.preview(engine.results)

            pending_entries = [e for e in trail.entries
                              if e.rule_id == 'RESOLVE_PENDING_TO_PACKED']
            self.assertGreater(len(pending_entries), 0,
                'Should have audit entries for pending→PACKED')
            for e in pending_entries:
                self.assertFalse(e.requires_approval)
                self.assertEqual(e.new_value, 'PACKED')
        finally:
            os.unlink(db_path)
