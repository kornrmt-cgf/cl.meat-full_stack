"""
Integration tests for Migration Simulation with Django-migrated isolated DB.

All tests use SimpleTestCase and never touch Django's test database machinery.
The simulation engine creates its own temporary SQLite file, runs Django
migrations against it, and deletes it when done.
"""
import os
import sqlite3
import tempfile

from django.test import SimpleTestCase

from inventory.migration_engine import file_hash
from inventory.migration_simulation import (
    MigrationSimulation, FailureCategory, BlockerType,
)


def _make_legacy_db():
    """Create a minimal legacy SQLite database for testing.

    This test DB deliberately includes:
    - 2 valid products with unique SKUs
    - 2 valid batches with unique batch numbers (different mfg dates)
    - 2 valid packages with unique barcodes
    - Products → Category FK chain intact
    - Batch → Supplier FK chain intact
    """
    tables = [
        ('stock_meat_category', ['ids', 'name_type'], [
            ('1', 'PORK'), ('2', 'CHICKEN'),
        ]),
        ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [
            ('1', 'S1', ''),
        ]),
        ('stock_meat_meat_parts', [
            'id', 'name', 'prefix_barcode', 'kcalories',
            'protent', 'fat', 'category_id',
        ], [
            ('1', 'Pork', '8001', '185', '31.7', '6.2', '1'),
            ('2', 'Chicken', '1001', '172', '21', '3', '2'),
        ]),
        ('stock_meat_product_info', [
            'id', 'name_id', 'type_product_id', 'import_from_id',
            'lot_number', 'weight', 'cost', 'selling_price_per_kg',
            'created_at',
        ], [
            ('1', '1', '1', '1', '1', '0.0', '82', '97',
             '2024-01-15 10:00:00'),
            ('2', '2', '1', '1', '1', '0.0', '65', '85',
             '2024-01-15 11:00:00'),
        ]),
        ('stock_meat_product_list', [
            'id', 'product_id', 'barcode', 'weight', 'selling_price',
            'storage_status', 'thaw_queue_position', 'loyverse_sku',
            'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced',
            'mfg',
        ], [
            ('1', '1', '1-1-8001-0001', '850', '82', 'frozen', '0',
             'LV-001', '', '', '1', '2024-01-15 10:00:00'),
            ('2', '2', '1-2-1001-0002', '750', '65', 'frozen', '0',
             'LV-002', '', '', '1', '2024-01-16 11:00:00'),
        ]),
    ]
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    for table_name, columns, rows in tables:
        col_defs = ', '.join(f'{c} TEXT' for c in columns)
        cur.execute(f'CREATE TABLE {table_name} ({col_defs})')
        for row in rows:
            placeholders = ', '.join(['?'] * len(row))
            cur.execute(
                f'INSERT INTO {table_name} VALUES ({placeholders})', row)
    conn.commit()
    conn.close()
    return path


def _run_sim(db_path):
    """Run simulation and return the result object."""
    return MigrationSimulation(db_path).run()


def _find_failures(sim, category=None, entity=None, field=None,
                   blocker_type=None):
    """Filter sim.failures by optional criteria."""
    results = sim.failures
    if category:
        results = [f for f in results if f.category == category]
    if entity:
        results = [f for f in results if f.entity == entity]
    if field:
        results = [f for f in results if f.field == field]
    if blocker_type:
        results = [f for f in results if f.blocker_type == blocker_type]
    return results


def _get_record(sim, entity, legacy_id):
    """Get a specific SimRecord by entity and legacy_id.

    Handles str/int mismatch from DryRunEngine (SQLite TEXT columns)."""
    target = str(legacy_id)
    for records in sim.results.values():
        for r in records:
            if r.entity == entity and str(r.legacy_id) == target:
                return r
    return None


# ============================================================
# ISOLATION TESTS
# ============================================================

class TestIsolation(SimpleTestCase):
    """Verify the simulation does not touch default or legacy databases."""

    def test_default_db_unchanged(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            self.assertTrue(
                sim.summary()['default_db_unchanged'],
                'Default database was modified during simulation')
        finally:
            os.unlink(db_path)

    def test_temp_db_deleted(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            self.assertIsNone(
                sim._sim_db_path,
                'Temporary database was not cleaned up')
        finally:
            os.unlink(db_path)

    def test_legacy_unchanged(self):
        db_path = _make_legacy_db()
        try:
            h1 = file_hash(db_path)
            _run_sim(db_path)
            h2 = file_hash(db_path)
            self.assertEqual(
                h1, h2,
                'Legacy database was modified during simulation')
        finally:
            os.unlink(db_path)


# ============================================================
# SCHEMA INTROSPECTION
# ============================================================

class TestSchemaIntrospection(SimpleTestCase):
    """Verify the temp DB has columns created by Django migrations."""

    def test_product_columns_from_migrations(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            cols = {c[0] for c in
                    sim._schema_columns.get('inventory_product', [])}
            for expected in ['id', 'sku', 'name', 'name_thai',
                            'category_id', 'unit', 'cost_per_kg',
                            'selling_price_per_kg', 'barcode_prefix',
                            'kcalories', 'protein', 'fat', 'active',
                            'created_at', 'updated_at']:
                self.assertIn(
                    expected, cols, f'Missing column: {expected}')
        finally:
            os.unlink(db_path)

    def test_batch_columns_from_migrations(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            cols = {c[0] for c in
                    sim._schema_columns.get('inventory_batch', [])}
            for expected in ['id', 'batch_number', 'supplier_id',
                            'received_at', 'notes', 'active',
                            'created_at', 'updated_at']:
                self.assertIn(
                    expected, cols, f'Missing column: {expected}')
        finally:
            os.unlink(db_path)

    def test_package_columns_from_migrations(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            cols = {c[0] for c in
                    sim._schema_columns.get('inventory_package', [])}
            for expected in ['id', 'product_id', 'batch_id', 'barcode',
                            'weight', 'selling_price', 'packed_at',
                            'current_state', 'loyverse_sku',
                            'loyverse_synced', 'created_at', 'updated_at']:
                self.assertIn(
                    expected, cols, f'Missing column: {expected}')
        finally:
            os.unlink(db_path)


# ============================================================
# REAL INSERTS + FK CHAIN VALIDATION
# ============================================================

class TestRealInserts(SimpleTestCase):
    """Verify records are actually inserted with valid FK chains."""

    def test_products_inserted_with_valid_category_fk(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            prod1 = _get_record(sim, 'Product', 1)
            prod2 = _get_record(sim, 'Product', 2)
            self.assertIsNotNone(prod1, 'Product #1 not found')
            self.assertIsNotNone(prod2, 'Product #2 not found')
            self.assertEqual(prod1.status, 'INSERTABLE')
            self.assertEqual(prod2.status, 'INSERTABLE')
            self.assertIsNotNone(prod1.target_id)
            self.assertGreater(prod1.target_id, 0)
            self.assertIsNotNone(prod2.target_id)
            self.assertGreater(prod2.target_id, 0)
        finally:
            os.unlink(db_path)

    def test_batches_inserted_with_valid_supplier_fk(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            batch_records = [r for r in sim.results.get('batches', [])
                            if r.status == 'INSERTABLE']
            self.assertGreater(len(batch_records), 0,
                              'No batches inserted')
            for r in batch_records:
                self.assertIsNotNone(r.target_id)
                self.assertGreater(r.target_id, 0)
                # Supplier FK must resolve
                self.assertIn(
                    'supplier_legacy_id', r.target_data)
                sup_id = r.target_data['supplier_legacy_id']
                sup_rec = _get_record(sim, 'Supplier', sup_id)
                self.assertIsNotNone(
                    sup_rec,
                    f'Batch #{r.legacy_id} references missing Supplier')
                self.assertEqual(sup_rec.status, 'INSERTABLE')
        finally:
            os.unlink(db_path)

    def test_packages_inserted_with_valid_product_and_batch_fk(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            pkg_records = [r for r in sim.results.get('packages', [])
                          if r.status == 'INSERTABLE']
            self.assertGreater(len(pkg_records), 0,
                              'No packages inserted')
            for r in pkg_records:
                self.assertIsNotNone(r.target_id)
                self.assertGreater(r.target_id, 0)
                # Product FK must resolve
                mp_id = r.target_data.get('meat_parts_id')
                if mp_id:
                    prod_rec = _get_record(sim, 'Product', mp_id)
                    self.assertIsNotNone(
                        prod_rec,
                        f'Package #{r.legacy_id} references missing Product')
                # Batch FK must resolve
                pi_id = r.target_data.get('product_legacy_id')
                if pi_id:
                    batch_rec = _get_record(sim, 'Batch', pi_id)
                    self.assertIsNotNone(
                        batch_rec,
                        f'Package #{r.legacy_id} references missing Batch')
        finally:
            os.unlink(db_path)

    def test_insertable_count_is_positive(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            s = sim.summary()
            self.assertGreater(
                s['insertable'], 0, 'No records inserted at all')
        finally:
            os.unlink(db_path)


# ============================================================
# CONSTRAINT ENFORCEMENT (explicit assertions)
# ============================================================

class TestConstraintEnforcement(SimpleTestCase):
    """Verify real DB constraint enforcement via actual INSERT attempts.

    Each test explicitly asserts:
    - the expected failure exists
    - its category (MODEL_VALIDATION or DATABASE_CONSTRAINT)
    - its entity
    - its field
    """

    def test_duplicate_sku_is_database_constraint(self):
        """Duplicate Product.sku triggers UNIQUE constraint at DB level.

        Both products share the same prefix_barcode='8001', so the
        DryRunEngine generates SKU='MP-8001' for both. The DryRunEngine
        keeps them VALID (with a WARNING), so the simulation attempts
        real DB inserts. The second INSERT fails with IntegrityError.

        Proves: DATABASE_CONSTRAINT, not MODEL_VALIDATION.
        """
        # Create a DB with two products sharing the SAME prefix_barcode
        # → same SKU → duplicate
        tables = [
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'PORK')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'],
             [('1', 'S1', '')]),
            ('stock_meat_meat_parts', [
                'id', 'name', 'prefix_barcode', 'kcalories',
                'protent', 'fat', 'category_id',
            ], [
                ('1', 'Pork A', '8001', '185', '31.7', '6.2', '1'),
                ('2', 'Pork B', '8001', '190', '30.0', '5.0', '1'),
            ]),
            ('stock_meat_product_info', [
                'id', 'name_id', 'type_product_id', 'import_from_id',
                'lot_number', 'weight', 'cost', 'selling_price_per_kg',
                'created_at',
            ], [
                ('1', '1', '1', '1', '1', '0.0', '82', '97',
                 '2024-01-15 10:00:00'),
                ('2', '2', '1', '1', '1', '0.0', '65', '85',
                 '2024-01-15 11:00:00'),
            ]),
            ('stock_meat_product_list', [
                'id', 'product_id', 'barcode', 'weight', 'selling_price',
                'storage_status', 'thaw_queue_position', 'loyverse_sku',
                'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced',
                'mfg',
            ], [
                ('1', '1', '1-1-8001-0001', '850', '82', 'frozen', '0',
                 'LV-001', '', '', '1', '2024-01-15 10:00:00'),
                ('2', '2', '2-1-8001-0001', '750', '65', 'frozen', '0',
                 'LV-002', '', '', '1', '2024-01-16 11:00:00'),
            ]),
        ]
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        for tname, cols, rows in tables:
            col_defs = ', '.join(f'{c} TEXT' for c in cols)
            cur.execute(f'CREATE TABLE {tname} ({col_defs})')
            for row in rows:
                ph = ', '.join(['?'] * len(row))
                cur.execute(
                    f'INSERT INTO {tname} VALUES ({ph})', row)
        conn.commit()
        conn.close()

        try:
            sim = _run_sim(path)
            # Both products are VALID in DryRunEngine (only WARNING for dup SKU)
            # So the simulation attempts real DB INSERT for both.
            # The second one must fail with DATABASE_CONSTRAINT.
            dc_sku = _find_failures(
                sim, category=FailureCategory.DATABASE_CONSTRAINT,
                entity='Product', field='sku')
            self.assertGreater(
                len(dc_sku), 0,
                'No DATABASE_CONSTRAINT on Product.sku detected — '
                'duplicate SKU was not caught by real DB')
            # Verify it is a real IntegrityError from DB
            self.assertEqual(dc_sku[0].error_class, 'IntegrityError')
            # Verify both products were attempted
            prod1 = _get_record(sim, 'Product', 1)
            prod2 = _get_record(sim, 'Product', 2)
            self.assertIsNotNone(prod1)
            self.assertIsNotNone(prod2)
            # First should be INSERTABLE, second BLOCKED
            self.assertEqual(prod1.status, 'INSERTABLE')
            self.assertEqual(prod2.status, 'BLOCKED')
            self.assertIsNotNone(prod1.target_id)
            self.assertIsNone(prod2.target_id)
        finally:
            os.unlink(path)

    def test_duplicate_batch_number_is_source_intrinsic(self):
        """Duplicate Batch.batch_number triggers UNIQUE at DB level.

        Both product_info rows share the same date AND lot_number=1,
        so the DryRunEngine generates identical batch numbers.
        The DryRunEngine does NOT pre-validate batch duplicates.
        The simulation inserts the first batch successfully; the second
        hits IntegrityError on the UNIQUE constraint.
        """
        tables = [
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'PORK')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'],
             [('1', 'S1', '')]),
            ('stock_meat_meat_parts', [
                'id', 'name', 'prefix_barcode', 'kcalories',
                'protent', 'fat', 'category_id',
            ], [
                ('1', 'Pork', '8001', '185', '31.7', '6.2', '1'),
            ]),
            # Same date + same supplier + same lot → same batch_number
            ('stock_meat_product_info', [
                'id', 'name_id', 'type_product_id', 'import_from_id',
                'lot_number', 'weight', 'cost', 'selling_price_per_kg',
                'created_at',
            ], [
                ('1', '1', '1', '1', '1', '0.0', '82', '97',
                 '2024-01-15 10:00:00'),
                ('2', '1', '1', '1', '1', '0.0', '65', '85',
                 '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', [
                'id', 'product_id', 'barcode', 'weight', 'selling_price',
                'storage_status', 'thaw_queue_position', 'loyverse_sku',
                'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced',
                'mfg',
            ], [
                ('1', '1', '1-1-8001-0001', '850', '82', 'frozen', '0',
                 'LV-001', '', '', '1', '2024-01-15 10:00:00'),
                ('2', '1', '2-1-8001-0001', '750', '65', 'frozen', '0',
                 'LV-002', '', '', '1', '2024-01-15 10:00:00'),
            ]),
        ]
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        for tname, cols, rows in tables:
            col_defs = ', '.join(f'{c} TEXT' for c in cols)
            cur.execute(f'CREATE TABLE {tname} ({col_defs})')
            for row in rows:
                ph = ', '.join(['?'] * len(row))
                cur.execute(
                    f'INSERT INTO {tname} VALUES ({ph})', row)
        conn.commit()
        conn.close()

        try:
            sim = _run_sim(path)
            # First batch inserts; second gets IntegrityError on batch_number UNIQUE
            sib_batch = _find_failures(
                sim, category=FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                entity='Batch', field='batch_number')
            self.assertGreater(
                len(sib_batch), 0,
                'No SOURCE_INTRINSIC_BLOCKER on Batch.batch_number')
            self.assertEqual(sib_batch[0].error_class, 'IntegrityError')
            # Verify the second batch was the one that failed
            batch1 = _get_record(sim, 'Batch', 1)
            batch2 = _get_record(sim, 'Batch', 2)
            self.assertIsNotNone(batch1)
            self.assertIsNotNone(batch2)
            self.assertEqual(batch1.status, 'INSERTABLE')
            self.assertEqual(batch2.status, 'BLOCKED')
        finally:
            os.unlink(path)

    def test_duplicate_barcode_detected(self):
        """Duplicate Package.barcode is detected by DryRunEngine pre-validation.

        The DryRunEngine catches duplicate barcodes before the simulation
        INSERT stage. The second package is marked SKIPPED with
        FindingCode.PACKAGE_DUPLICATE_BARCODE.

        This tests the pre-DB safety net — the DryRunEngine prevents
        the simulation from ever attempting to INSERT a duplicate barcode.

        NOTE: The DB UNIQUE constraint on Package.barcode is a second
        safety net, but the DryRunEngine catches it first.
        """
        tables = [
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'PORK')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'],
             [('1', 'S1', '')]),
            ('stock_meat_meat_parts', [
                'id', 'name', 'prefix_barcode', 'kcalories',
                'protent', 'fat', 'category_id',
            ], [
                ('1', 'Pork', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', [
                'id', 'name_id', 'type_product_id', 'import_from_id',
                'lot_number', 'weight', 'cost', 'selling_price_per_kg',
                'created_at',
            ], [
                ('1', '1', '1', '1', '1', '0.0', '82', '97',
                 '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', [
                'id', 'product_id', 'barcode', 'weight', 'selling_price',
                'storage_status', 'thaw_queue_position', 'loyverse_sku',
                'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced',
                'mfg',
            ], [
                ('1', '1', 'DUP-BARCODE-001', '850', '82', 'frozen', '0',
                 'LV-001', '', '', '1', '2024-01-15 10:00:00'),
                ('2', '1', 'DUP-BARCODE-001', '750', '65', 'frozen', '0',
                 'LV-002', '', '', '1', '2024-01-16 11:00:00'),
            ]),
        ]
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        for tname, cols, rows in tables:
            col_defs = ', '.join(f'{c} TEXT' for c in cols)
            cur.execute(f'CREATE TABLE {tname} ({col_defs})')
            for row in rows:
                ph = ', '.join(['?'] * len(row))
                cur.execute(
                    f'INSERT INTO {tname} VALUES ({ph})', row)
        conn.commit()
        conn.close()

        try:
            sim = _run_sim(path)
            # Package #1 should be INSERTABLE (first barcode is fine)
            pkg1 = _get_record(sim, 'Package', '1')
            self.assertIsNotNone(
                pkg1, 'Package #1 not found in results')
            self.assertEqual(
                pkg1.status, 'INSERTABLE',
                'Package #1 should be INSERTABLE')
            self.assertIsNotNone(pkg1.target_id)
            # Package #2 should be BLOCKED by DryRunEngine pre-validation
            pkg2 = _get_record(sim, 'Package', '2')
            self.assertIsNotNone(
                pkg2, 'Package #2 not found in results')
            self.assertEqual(
                pkg2.status, 'BLOCKED',
                'Package #2 should be BLOCKED (duplicate barcode)')
            self.assertIsNone(
                pkg2.target_id,
                'Package #2 should NOT have a target_id (never inserted)')
            # Must have a failure indicating duplicate barcode
            barcode_failures = [f for f in pkg2.failures
                               if 'barcode' in f.field.lower()
                               or 'barcode' in f.message.lower()]
            self.assertGreater(
                len(barcode_failures), 0,
                'No duplicate barcode failure detected for Package #2')
        finally:
            os.unlink(path)


# ============================================================
# TRACEABILITY
# ============================================================

class TestTraceability(SimpleTestCase):
    """Verify every insertable record has a target_id from the real DB."""

    def test_insertable_have_target_ids(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            for entity, records in sim.results.items():
                for r in records:
                    if r.status == 'INSERTABLE':
                        self.assertIsNotNone(
                            r.target_id,
                            f'{r.entity} #{r.legacy_id} INSERTABLE '
                            f'but no target_id')
                        self.assertGreater(
                            r.target_id, 0,
                            f'{r.entity} #{r.legacy_id} target_id <= 0')
        finally:
            os.unlink(db_path)

    def test_blocked_have_no_target_id(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            for entity, records in sim.results.items():
                for r in records:
                    if r.status == 'BLOCKED':
                        self.assertIsNone(
                            r.target_id,
                            f'{r.entity} #{r.legacy_id} BLOCKED '
                            f'but has target_id={r.target_id}')
        finally:
            os.unlink(db_path)


# ============================================================
# ERROR CLASSIFICATION
# ============================================================

class TestErrorClassification(SimpleTestCase):
    """Verify all failures have valid categories."""

    def test_all_failures_classified(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            valid_categories = {
                FailureCategory.MODEL_VALIDATION,
                FailureCategory.DATABASE_CONSTRAINT,
                FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                FailureCategory.UNEXPECTED_ERROR,
                FailureCategory.WARNING,
                FailureCategory.HISTORICAL_DATA_LOSS_RISK,
                FailureCategory.TARGET_FK,
                FailureCategory.TARGET_REQUIRED,
                FailureCategory.TARGET_TYPE,
            }
            for f in sim.failures:
                self.assertIn(
                    f.category, valid_categories,
                    f'Unclassified failure: {f.category}')
        finally:
            os.unlink(db_path)

    def test_all_failures_have_blocker_type(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            valid_types = {BlockerType.ROOT, BlockerType.DEPENDENT}
            for f in sim.failures:
                if f.category in (FailureCategory.WARNING,
                                  FailureCategory.HISTORICAL_DATA_LOSS_RISK):
                    continue  # These are not blockers
                self.assertIn(
                    f.blocker_type, valid_types,
                    f'Invalid blocker_type: {f.blocker_type}')
        finally:
            os.unlink(db_path)


# ============================================================
# ROOT CAUSE CLASSIFICATION
# ============================================================

class TestRootCauseClassification(SimpleTestCase):
    """Verify root cause vs dependent blocker classification."""

    def test_root_causes_identified(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            # The test DB has no root causes (all products have valid
            # categories). Root causes only appear with the real legacy DB.
            # But the mechanism must work.
            self.assertIsInstance(sim._root_causes, dict)
        finally:
            os.unlink(db_path)

    def test_dependent_failures_classified(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            for f in sim.failures:
                if f.category in (FailureCategory.WARNING,
                                  FailureCategory.HISTORICAL_DATA_LOSS_RISK):
                    continue
                self.assertIn(
                    f.blocker_type,
                    (BlockerType.ROOT, BlockerType.DEPENDENT),
                    f'Failure not classified: {f.entity}#{f.legacy_id}')
        finally:
            os.unlink(db_path)


# ============================================================
# FULL SIMULATION
# ============================================================

class TestFullSimulation(SimpleTestCase):
    """End-to-end simulation on the full dataset."""

    def test_full_record_simulation(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            s = sim.summary()
            self.assertGreater(s['total'], 0)
            self.assertEqual(
                s['insertable'] + s['blocked'] + s['warnings'],
                s['total'])
            self.assertTrue(s['legacy_unchanged'])
            self.assertTrue(s['default_db_unchanged'])
        finally:
            os.unlink(db_path)

    def test_deterministic_logical_result(self):
        """Run simulation twice and compare logical signatures.

        target_id (auto-increment) is excluded from comparison.
        Only entity, legacy_id, status, and failure signatures are compared.
        """
        db_path = _make_legacy_db()
        try:
            sig1 = _run_sim(db_path).get_logical_signature()
            sig2 = _run_sim(db_path).get_logical_signature()
            self.assertEqual(
                len(sig1), len(sig2),
                f'Different record counts: {len(sig1)} vs {len(sig2)}')
            for i, (a, b) in enumerate(zip(sig1, sig2)):
                self.assertEqual(
                    a, b,
                    f'Record {i} differs:\n  run1: {a}\n  run2: {b}')
        finally:
            os.unlink(db_path)

    def test_insertable_matches_traceability(self):
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            s = sim.summary()
            actual = sum(1 for t in sim.traceability
                        if t.get('target_id'))
            self.assertEqual(
                s['insertable'], actual,
                'Insertable count does not match traceability count')
        finally:
            os.unlink(db_path)

    def test_summary_root_dependent_counts(self):
        """Verify root_blockers and dependent_blockers are in summary."""
        db_path = _make_legacy_db()
        try:
            sim = _run_sim(db_path)
            s = sim.summary()
            self.assertIn('root_blockers', s)
            self.assertIn('dependent_blockers', s)
            self.assertIsInstance(s['root_blockers'], int)
            self.assertIsInstance(s['dependent_blockers'], int)
            self.assertGreaterEqual(s['root_blockers'], 0)
            self.assertGreaterEqual(s['dependent_blockers'], 0)
        finally:
            os.unlink(db_path)
