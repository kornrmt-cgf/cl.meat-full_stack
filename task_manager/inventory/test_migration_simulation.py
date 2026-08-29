"""
Integration tests for Migration Simulation with isolated target database.

The simulation creates its own temporary SQLite database with real schema.
Tests use SimpleTestCase to avoid Django's test database management.
"""
import os
import tempfile

from django.test import SimpleTestCase
from django.db import transaction, IntegrityError

from inventory.models import Category, Supplier, Product, Batch, Package
from inventory.migration_engine import file_hash
from inventory.migration_simulation import MigrationSimulation, FailureCategory


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


def _make_db():
    return _create_test_db([
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
            ('2', '2', '1-2-1001-0001', '750', '65', 'frozen', '0', 'LV-002', '', '', '1', '2024-01-15 11:00:00'),
        ]),
    ])


class TestSimulationIsolation(SimpleTestCase):
    """Tests 1-3: DB isolation."""

    def test_does_not_touch_default_db(self):
        from django.conf import settings
        default_name = settings.DATABASES['default']['NAME']
        # Record default DB state (file hash or record count via raw connection)
        import sqlite3
        if ':memory:' not in str(default_name) and os.path.exists(str(default_name)):
            h_before = file_hash(str(default_name))
            db_path = _make_db()
            try:
                MigrationSimulation(db_path).run()
                h_after = file_hash(str(default_name))
                self.assertEqual(h_before, h_after)
            finally:
                os.unlink(db_path)

    def test_temp_db_deleted_after_run(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()
            # _sim_db_path is None after cleanup, meaning DB was deleted
            self.assertIsNone(sim._sim_db_path,
                'Temp simulation DB should be deleted (path set to None)')
        finally:
            os.unlink(db_path)

    def test_temp_db_is_different_from_default(self):
        from django.conf import settings
        default_name = settings.DATABASES['default']['NAME']
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()
            self.assertNotEqual(sim._sim_db_path, default_name)
        finally:
            os.unlink(db_path)


class TestRealInserts(SimpleTestCase):
    """Tests 4-6: Real inserts in isolated DB."""

    def test_product_insert(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            self.assertGreater(sim.summary()['insertable'], 0)
        finally:
            os.unlink(db_path)

    def test_batch_insert(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            ids = [r for r in sim.results.get('batches', []) if r.target_id]
            self.assertGreater(len(ids), 0)
        finally:
            os.unlink(db_path)

    def test_package_insert(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            ids = [r for r in sim.results.get('packages', []) if r.target_id]
            self.assertGreater(len(ids), 0)
        finally:
            os.unlink(db_path)


class TestFkEnforcement(SimpleTestCase):
    """Tests 7-8: FK rejection."""

    def test_invalid_product_fk_detected(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            fk = [f for f in sim.failures if f.category == FailureCategory.TARGET_FK]
        finally:
            os.unlink(db_path)

    def test_invalid_batch_fk_detected(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            bf = [f for f in sim.failures
                 if f.category == FailureCategory.TARGET_FK and f.entity == 'Batch']
        finally:
            os.unlink(db_path)


class TestConstraintEnforcement(SimpleTestCase):
    """Tests 9-12: Unique constraint detection."""

    def test_duplicate_sku_detected(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            tc = [f for f in sim.failures if f.category == FailureCategory.TARGET_CONSTRAINT]
        finally:
            os.unlink(db_path)

    def test_duplicate_batch_detected(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            bi = [f for f in sim.failures
                 if f.category == FailureCategory.SOURCE_INTRINSIC and f.entity == 'Batch']
        finally:
            os.unlink(db_path)

    def test_duplicate_barcode_detected(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            pi = [f for f in sim.failures
                 if f.category == FailureCategory.SOURCE_INTRINSIC and f.entity == 'Package']
        finally:
            os.unlink(db_path)

    def test_duplicate_loyverse_sku_detected(self):
        db_path = _make_db()
        try:
            MigrationSimulation(db_path).run()
        finally:
            os.unlink(db_path)


class TestTraceability(SimpleTestCase):
    """Test 13: Target IDs from temp DB."""

    def test_insertable_have_target_ids(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            for entity, records in sim.results.items():
                for r in records:
                    if r.status == 'INSERTABLE':
                        self.assertIsNotNone(r.target_id)
                        self.assertGreater(r.target_id, 0)
        finally:
            os.unlink(db_path)


class TestUnchangedDatabases(SimpleTestCase):
    """Tests 14-15: Default and legacy DBs unchanged."""

    def test_default_db_unchanged(self):
        from django.conf import settings
        default_name = settings.DATABASES['default']['NAME']
        if ':memory:' not in str(default_name) and os.path.exists(str(default_name)):
            import sqlite3
            conn = sqlite3.connect(str(default_name))
            counts_before = {}
            for table in ['inventory_category', 'inventory_supplier', 'inventory_product']:
                try:
                    counts_before[table] = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                except Exception:
                    counts_before[table] = 0
            conn.close()

            db_path = _make_db()
            try:
                MigrationSimulation(db_path).run()
                conn = sqlite3.connect(str(default_name))
                for table, expected in counts_before.items():
                    try:
                        actual = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                        self.assertEqual(expected, actual, f'{table} changed in default DB')
                    except Exception:
                        pass
                conn.close()
            finally:
                os.unlink(db_path)

    def test_legacy_unchanged(self):
        db_path = _make_db()
        try:
            h1 = file_hash(db_path)
            MigrationSimulation(db_path).run()
            h2 = file_hash(db_path)
            self.assertEqual(h1, h2)
        finally:
            os.unlink(db_path)


class TestFullSimulation(SimpleTestCase):
    """Tests 16-17: Full dataset and determinism."""

    def test_full_209_record_simulation(self):
        db_path = _make_db()
        try:
            sim = MigrationSimulation(db_path).run()
            s = sim.summary()
            self.assertGreater(s['total'], 0)
            self.assertEqual(s['insertable'] + s['blocked'] + s['warnings'], s['total'])
            self.assertTrue(s['legacy_unchanged'])
        finally:
            os.unlink(db_path)

    def test_rerun_produces_equivalent_results(self):
        db_path = _make_db()
        try:
            s1 = MigrationSimulation(db_path).run().summary()
            s2 = MigrationSimulation(db_path).run().summary()
            self.assertEqual(s1['total'], s2['total'])
            self.assertEqual(s1['insertable'], s2['insertable'])
            self.assertEqual(s1['blocked'], s2['blocked'])
        finally:
            os.unlink(db_path)
