"""
PostgreSQL Staging Simulation Tests

Validates the migration pipeline against a real PostgreSQL database with
actual database constraints (UNIQUE, FK, NOT NULL, decimal precision).

Uses SimpleTestCase to avoid Django test DB machinery.
Connects directly to PostgreSQL via psycopg2.
"""
import os
import sqlite3
import tempfile

from django.test import SimpleTestCase

from inventory.migration_engine import file_hash
from inventory.pg_simulation import (
    PgMigrationSimulation, FailureCategory, BlockerType,
    _pg_connect, _pg_truncate_all, PG_CONFIG,
)


def _pg_available():
    """Check if PostgreSQL staging DB is reachable."""
    try:
        conn = _pg_connect()
        conn.close()
        return True
    except Exception:
        return False


def _make_legacy_db():
    """Create a minimal legacy SQLite database for testing."""
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


def _run_pg_sim(db_path):
    """Run PostgreSQL simulation and return result."""
    return PgMigrationSimulation(db_path).run()


def _find_failures(sim, category=None, entity=None, field=None):
    """Filter sim.failures by optional criteria."""
    results = sim.failures
    if category:
        results = [f for f in results if f.category == category]
    if entity:
        results = [f for f in results if f.entity == entity]
    if field:
        results = [f for f in results if f.field == field]
    return results


def _get_record(sim, entity, legacy_id):
    """Get SimRecord by entity and legacy_id."""
    target = str(legacy_id)
    for records in sim.results.values():
        for r in records:
            if r.entity == entity and str(r.legacy_id) == target:
                return r
    return None


# Skip all tests if PostgreSQL is not available
_skip_pg = not _pg_available()


# ============================================================
# ISOLATION TESTS
# ============================================================

class TestPgIsolation(SimpleTestCase):
    """Verify the simulation uses isolated PostgreSQL staging DB."""

    @staticmethod
    def _skip():
        if _skip_pg:
            import unittest
            raise unittest.SkipTest('PostgreSQL staging not available')

    def test_staging_db_truncated_after_run(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            # After simulation, all inventory tables should be empty
            conn = _pg_connect()
            cur = conn.cursor()
            for table in ['inventory_category', 'inventory_product',
                          'inventory_batch', 'inventory_package',
                          'inventory_supplier']:
                cur.execute(f'SELECT COUNT(*) FROM {table}')
                count = cur.fetchone()[0]
                self.assertEqual(count, 0,
                    f'{table} not cleaned up after simulation (count={count})')
            conn.close()
        finally:
            os.unlink(db_path)

    def test_legacy_unchanged(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            h1 = file_hash(db_path)
            _run_pg_sim(db_path)
            h2 = file_hash(db_path)
            self.assertEqual(h1, h2, 'Legacy database was modified')
        finally:
            os.unlink(db_path)

    def test_default_db_unchanged(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            self.assertTrue(sim.summary()['default_db_unchanged'])
        finally:
            os.unlink(db_path)


# ============================================================
# REAL POSTGRESQL INSERTS
# ============================================================

class TestPgRealInserts(SimpleTestCase):
    """Verify records are inserted into real PostgreSQL with valid FK chains."""

    @staticmethod
    def _skip():
        if _skip_pg:
            import unittest
            raise unittest.SkipTest('PostgreSQL staging not available')

    def test_products_inserted_with_valid_category_fk(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            prod1 = _get_record(sim, 'Product', 1)
            prod2 = _get_record(sim, 'Product', 2)
            self.assertIsNotNone(prod1)
            self.assertIsNotNone(prod2)
            self.assertEqual(prod1.status, 'INSERTABLE')
            self.assertEqual(prod2.status, 'INSERTABLE')
            self.assertIsNotNone(prod1.target_id)
            self.assertGreater(prod1.target_id, 0)
        finally:
            os.unlink(db_path)

    def test_batches_inserted_with_valid_supplier_fk(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            batch_records = [r for r in sim.results.get('batches', [])
                            if r.status == 'INSERTABLE']
            self.assertGreater(len(batch_records), 0)
            for r in batch_records:
                self.assertIsNotNone(r.target_id)
                self.assertGreater(r.target_id, 0)
        finally:
            os.unlink(db_path)

    def test_packages_inserted_with_valid_product_and_batch_fk(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            pkg_records = [r for r in sim.results.get('packages', [])
                          if r.status == 'INSERTABLE']
            self.assertGreater(len(pkg_records), 0)
            for r in pkg_records:
                self.assertIsNotNone(r.target_id)
                self.assertGreater(r.target_id, 0)
        finally:
            os.unlink(db_path)

    def test_insertable_count_is_positive(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            self.assertGreater(sim.summary()['insertable'], 0)
        finally:
            os.unlink(db_path)


# ============================================================
# CONSTRAINT ENFORCEMENT (PostgreSQL)
# ============================================================

class TestPgConstraintEnforcement(SimpleTestCase):
    """Verify PostgreSQL real DB constraint enforcement."""

    @staticmethod
    def _skip():
        if _skip_pg:
            import unittest
            raise unittest.SkipTest('PostgreSQL staging not available')

    def test_duplicate_sku_is_database_constraint(self):
        """Duplicate Product.sku triggers UNIQUE constraint on PostgreSQL."""
        self._skip()
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
                cur.execute(f'INSERT INTO {tname} VALUES ({ph})', row)
        conn.commit()
        conn.close()

        try:
            sim = _run_pg_sim(path)
            dc_sku = _find_failures(
                sim, category=FailureCategory.DATABASE_CONSTRAINT,
                entity='Product', field='sku')
            self.assertGreater(len(dc_sku), 0,
                'No DATABASE_CONSTRAINT on Product.sku — '
                'duplicate SKU not caught by PostgreSQL')
            self.assertEqual(dc_sku[0].error_class, 'IntegrityError')
            prod1 = _get_record(sim, 'Product', 1)
            prod2 = _get_record(sim, 'Product', 2)
            self.assertIsNotNone(prod1)
            self.assertIsNotNone(prod2)
            self.assertEqual(prod1.status, 'INSERTABLE')
            self.assertEqual(prod2.status, 'BLOCKED')
            self.assertIsNotNone(prod1.target_id)
            self.assertIsNone(prod2.target_id)
        finally:
            os.unlink(path)

    def test_duplicate_batch_number_is_source_intrinsic(self):
        """Duplicate Batch.batch_number triggers UNIQUE on PostgreSQL."""
        self._skip()
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
                cur.execute(f'INSERT INTO {tname} VALUES ({ph})', row)
        conn.commit()
        conn.close()

        try:
            sim = _run_pg_sim(path)
            sib_batch = _find_failures(
                sim, category=FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                entity='Batch', field='batch_number')
            self.assertGreater(len(sib_batch), 0,
                'No SOURCE_INTRINSIC_BLOCKER on Batch.batch_number')
            self.assertEqual(sib_batch[0].error_class, 'IntegrityError')
            batch1 = _get_record(sim, 'Batch', 1)
            batch2 = _get_record(sim, 'Batch', 2)
            self.assertIsNotNone(batch1)
            self.assertIsNotNone(batch2)
            self.assertEqual(batch1.status, 'INSERTABLE')
            self.assertEqual(batch2.status, 'BLOCKED')
        finally:
            os.unlink(path)

    def test_duplicate_barcode_detected(self):
        """Duplicate Package.barcode caught by DryRunEngine pre-validation."""
        self._skip()
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
                cur.execute(f'INSERT INTO {tname} VALUES ({ph})', row)
        conn.commit()
        conn.close()

        try:
            sim = _run_pg_sim(path)
            pkg1 = _get_record(sim, 'Package', '1')
            self.assertIsNotNone(pkg1)
            self.assertEqual(pkg1.status, 'INSERTABLE')
            self.assertIsNotNone(pkg1.target_id)
            pkg2 = _get_record(sim, 'Package', '2')
            self.assertIsNotNone(pkg2)
            self.assertEqual(pkg2.status, 'BLOCKED')
            self.assertIsNone(pkg2.target_id)
            barcode_failures = [f for f in pkg2.failures
                               if 'barcode' in f.field.lower()
                               or 'barcode' in f.message.lower()]
            self.assertGreater(len(barcode_failures), 0)
        finally:
            os.unlink(path)


# ============================================================
# TRACEABILITY
# ============================================================

class TestPgTraceability(SimpleTestCase):
    """Verify every PG-inserted record has a target_id."""

    @staticmethod
    def _skip():
        if _skip_pg:
            import unittest
            raise unittest.SkipTest('PostgreSQL staging not available')

    def test_insertable_have_target_ids(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            for entity, records in sim.results.items():
                for r in records:
                    if r.status == 'INSERTABLE':
                        self.assertIsNotNone(r.target_id,
                            f'{r.entity} #{r.legacy_id} INSERTABLE but no target_id')
                        self.assertGreater(r.target_id, 0)
        finally:
            os.unlink(db_path)

    def test_blocked_have_no_target_id(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            for entity, records in sim.results.items():
                for r in records:
                    if r.status == 'BLOCKED':
                        self.assertIsNone(r.target_id,
                            f'{r.entity} #{r.legacy_id} BLOCKED but has target_id')
        finally:
            os.unlink(db_path)


# ============================================================
# ERROR CLASSIFICATION
# ============================================================

class TestPgErrorClassification(SimpleTestCase):
    """Verify all PG failures have valid categories."""

    @staticmethod
    def _skip():
        if _skip_pg:
            import unittest
            raise unittest.SkipTest('PostgreSQL staging not available')

    def test_all_failures_classified(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
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
                self.assertIn(f.category, valid_categories,
                    f'Unclassified failure: {f.category}')
        finally:
            os.unlink(db_path)

    def test_all_failures_have_blocker_type(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            valid_types = {BlockerType.ROOT, BlockerType.DEPENDENT}
            for f in sim.failures:
                if f.category in (FailureCategory.WARNING,
                                  FailureCategory.HISTORICAL_DATA_LOSS_RISK):
                    continue
                self.assertIn(f.blocker_type, valid_types,
                    f'Invalid blocker_type: {f.blocker_type}')
        finally:
            os.unlink(db_path)


# ============================================================
# FULL SIMULATION + DETERMINISM
# ============================================================

class TestPgFullSimulation(SimpleTestCase):
    """End-to-end PG simulation."""

    @staticmethod
    def _skip():
        if _skip_pg:
            import unittest
            raise unittest.SkipTest('PostgreSQL staging not available')

    def test_full_record_simulation(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            s = sim.summary()
            self.assertGreater(s['total'], 0)
            self.assertEqual(
                s['insertable'] + s['blocked'] + s['warnings'],
                s['total'])
            self.assertTrue(s['legacy_unchanged'])
        finally:
            os.unlink(db_path)

    def test_deterministic_logical_result(self):
        """Run PG simulation twice and compare logical signatures."""
        self._skip()
        db_path = _make_legacy_db()
        try:
            sig1 = _run_pg_sim(db_path).get_logical_signature()
            sig2 = _run_pg_sim(db_path).get_logical_signature()
            self.assertEqual(len(sig1), len(sig2))
            for i, (a, b) in enumerate(zip(sig1, sig2)):
                self.assertEqual(a, b,
                    f'Record {i} differs:\n  run1: {a}\n  run2: {b}')
        finally:
            os.unlink(db_path)

    def test_insertable_matches_traceability(self):
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            s = sim.summary()
            actual = sum(1 for t in sim.traceability if t.get('target_id'))
            self.assertEqual(s['insertable'], actual)
        finally:
            os.unlink(db_path)

    def test_performance_metrics_present(self):
        """Verify timing metrics are collected."""
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            s = sim.summary()
            self.assertIn('insertion_time', s)
            self.assertIn('records_per_sec', s)
            self.assertGreaterEqual(s['insertion_time'], 0)
            self.assertGreaterEqual(s['records_per_sec'], 0)
        finally:
            os.unlink(db_path)

    def test_schema_introspection_populated(self):
        """Verify PostgreSQL schema introspection works."""
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            self.assertGreater(len(sim._schema_columns), 0)
            self.assertIn('inventory_product', sim._schema_columns)
            self.assertIn('inventory_package', sim._schema_columns)
            self.assertIn('inventory_batch', sim._schema_columns)
        finally:
            os.unlink(db_path)

    def test_pg_unique_constraints_populated(self):
        """Verify unique constraints were introspected from PG."""
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            uqs = getattr(sim, '_pg_unique_constraints', {})
            self.assertIn('inventory_product', uqs)
            self.assertIn('sku', uqs['inventory_product'])
            self.assertIn('inventory_category', uqs)
            self.assertIn('code', uqs['inventory_category'])
            self.assertIn('inventory_batch', uqs)
            self.assertIn('batch_number', uqs['inventory_batch'])
            self.assertIn('inventory_package', uqs)
            self.assertIn('barcode', uqs['inventory_package'])
        finally:
            os.unlink(db_path)

    def test_pg_foreign_keys_populated(self):
        """Verify FK constraints were introspected from PG."""
        self._skip()
        db_path = _make_legacy_db()
        try:
            sim = _run_pg_sim(db_path)
            fks = getattr(sim, '_pg_foreign_keys', [])
            fk_pairs = {(fk['table'], fk['column'], fk['ref_table']) for fk in fks}
            self.assertIn(('inventory_product', 'category_id', 'inventory_category'), fk_pairs)
            self.assertIn(('inventory_batch', 'supplier_id', 'inventory_supplier'), fk_pairs)
            self.assertIn(('inventory_package', 'product_id', 'inventory_product'), fk_pairs)
            self.assertIn(('inventory_package', 'batch_id', 'inventory_batch'), fk_pairs)
        finally:
            os.unlink(db_path)
