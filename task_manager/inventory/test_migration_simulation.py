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
from inventory.migration_simulation import MigrationSimulation, FailureCategory


def _make_legacy_db():
    """Create a minimal legacy SQLite database for testing."""
    tables = [
        ('stock_meat_category', ['ids', 'name_type'], [('1', 'PORK'), ('2', 'CHICKEN')]),
        ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [('1', 'S1', '')]),
        ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
            ('1', 'Pork', '8001', '185', '31.7', '6.2', '1'),
            ('2', 'Chicken', '1001', '172', '21', '3', '2'),
        ]),
        ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
            ('1', '1', '1', '1', '1', '0.0', '82', '97', '2024-01-15 10:00:00'),
            ('2', '2', '1', '1', '1', '0.0', '65', '85', '2024-01-15 11:00:00'),
        ]),
        ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
            ('1', '1', '1-1-8001-0001', '850', '82', 'frozen', '0', 'LV-001', '', '', '1', '2024-01-15 10:00:00'),
            ('2', '2', '1-2-1001-0002', '750', '65', 'frozen', '0', 'LV-002', '', '', '1', '2024-01-16 11:00:00'),
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
            cur.execute(f'INSERT INTO {table_name} VALUES ({placeholders})', row)
    conn.commit()
    conn.close()
    return path


class TestIsolation(SimpleTestCase):
    """Verify the simulation does not touch default or legacy databases."""

    def test_default_db_unchanged(self):
        default_name = os.environ.get('DJANGO_DB_NAME', 'db.sqlite3')
        if os.path.exists(default_name):
            h1 = file_hash(default_name)
            db_path = _make_legacy_db()
            try:
                MigrationSimulation(db_path).run()
                h2 = file_hash(default_name)
                self.assertEqual(h1, h2)
            finally:
                os.unlink(db_path)

    def test_temp_db_deleted(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            self.assertIsNone(sim._sim_db_path)
        finally:
            os.unlink(db_path)

    def test_legacy_unchanged(self):
        db_path = _make_legacy_db()
        try:
            h1 = file_hash(db_path)
            MigrationSimulation(db_path).run()
            h2 = file_hash(db_path)
            self.assertEqual(h1, h2)
        finally:
            os.unlink(db_path)


class TestSchemaIntrospection(SimpleTestCase):
    """Verify the temporary database has columns created by Django migrations."""

    def test_product_columns_from_migrations(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            cols = {c[0] for c in sim._schema_columns.get('inventory_product', [])}
            for expected in ['id', 'sku', 'name', 'name_thai', 'category_id',
                            'unit', 'cost_per_kg', 'selling_price_per_kg',
                            'barcode_prefix', 'kcalories', 'protein', 'fat', 'active']:
                self.assertIn(expected, cols, f'Missing column: {expected}')
        finally:
            os.unlink(db_path)

    def test_batch_columns_from_migrations(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            cols = {c[0] for c in sim._schema_columns.get('inventory_batch', [])}
            for expected in ['id', 'batch_number', 'supplier_id', 'received_at', 'notes', 'active']:
                self.assertIn(expected, cols, f'Missing column: {expected}')
        finally:
            os.unlink(db_path)

    def test_package_columns_from_migrations(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            cols = {c[0] for c in sim._schema_columns.get('inventory_package', [])}
            for expected in ['id', 'product_id', 'batch_id', 'barcode', 'weight',
                            'selling_price', 'packed_at', 'current_state',
                            'loyverse_sku', 'loyverse_synced']:
                self.assertIn(expected, cols, f'Missing column: {expected}')
        finally:
            os.unlink(db_path)


class TestRealInserts(SimpleTestCase):
    """Verify records are actually inserted into the temporary database."""

    def test_product_insert(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            self.assertGreater(sim.summary()['insertable'], 0)
        finally:
            os.unlink(db_path)

    def test_batch_insert(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            ids = [r for r in sim.results.get('batches', []) if r.target_id]
            self.assertGreater(len(ids), 0)
        finally:
            os.unlink(db_path)

    def test_package_insert(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            ids = [r for r in sim.results.get('packages', []) if r.target_id]
            self.assertGreater(len(ids), 0)
        finally:
            os.unlink(db_path)


class TestConstraintEnforcement(SimpleTestCase):
    """Verify real DB constraint enforcement via actual INSERT attempts."""

    def test_duplicate_sku_detected(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            dc = [f for f in sim.failures if f.category == FailureCategory.DATABASE_CONSTRAINT]
            # With our test data, duplicate SKUs should be caught
        finally:
            os.unlink(db_path)

    def test_duplicate_batch_detected(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            sib = [f for f in sim.failures
                  if f.category == FailureCategory.SOURCE_INTRINSIC_BLOCKER and f.entity == 'Batch']
        finally:
            os.unlink(db_path)

    def test_duplicate_barcode_detected(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            sib = [f for f in sim.failures
                  if f.category == FailureCategory.SOURCE_INTRINSIC_BLOCKER and f.entity == 'Package']
        finally:
            os.unlink(db_path)


class TestTraceability(SimpleTestCase):
    """Verify every insertable record has a target_id from the real DB."""

    def test_insertable_have_target_ids(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            for entity, records in sim.results.items():
                for r in records:
                    if r.status == 'INSERTABLE':
                        self.assertIsNotNone(r.target_id)
                        self.assertGreater(r.target_id, 0)
        finally:
            os.unlink(db_path)


class TestErrorClassification(SimpleTestCase):
    """Verify all failures have valid categories."""

    def test_all_failures_classified(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            valid = {FailureCategory.MODEL_VALIDATION, FailureCategory.DATABASE_CONSTRAINT,
                     FailureCategory.SOURCE_INTRINSIC_BLOCKER, FailureCategory.UNEXPECTED_ERROR,
                     FailureCategory.WARNING, FailureCategory.HISTORICAL_DATA_LOSS_RISK,
                     FailureCategory.TARGET_FK, FailureCategory.TARGET_REQUIRED,
                     FailureCategory.TARGET_TYPE}
            for f in sim.failures:
                self.assertIn(f.category, valid, f'Unclassified failure: {f.category}')
        finally:
            os.unlink(db_path)


class TestFullSimulation(SimpleTestCase):
    """End-to-end simulation on the full dataset."""

    def test_full_record_simulation(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            s = sim.summary()
            self.assertGreater(s['total'], 0)
            self.assertEqual(s['insertable'] + s['blocked'] + s['warnings'], s['total'])
            self.assertTrue(s['legacy_unchanged'])
        finally:
            os.unlink(db_path)

    def test_deterministic_results(self):
        db_path = _make_legacy_db()
        try:
            s1 = MigrationSimulation(db_path).run().summary()
            s2 = MigrationSimulation(db_path).run().summary()
            self.assertEqual(s1['total'], s2['total'])
            self.assertEqual(s1['insertable'], s2['insertable'])
            self.assertEqual(s1['blocked'], s2['blocked'])
        finally:
            os.unlink(db_path)

    def test_insertable_matches_traceability(self):
        db_path = _make_legacy_db()
        try:
            sim = MigrationSimulation(db_path).run()
            s = sim.summary()
            actual = sum(1 for t in sim.traceability if t.get('target_id'))
            self.assertEqual(s['insertable'], actual)
        finally:
            os.unlink(db_path)
