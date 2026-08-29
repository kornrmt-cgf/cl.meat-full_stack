"""
Integration tests for the Migration Simulation Engine.

Proves that resolved legacy data can be transformed into target Django models.
Uses real Django models and real temporary database for constraint validation.
"""
import os
import tempfile

from django.test import TestCase
from django.db import connection

from inventory.migration_engine import DryRunEngine, Status, FindingCode, file_hash
from inventory.migration_simulation import (
    MigrationSimulation, FailureCategory, SimulationRecord,
)
from inventory.resolution import ResolutionApplier, classify_findings


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


def _make_simulation_db():
    """DB with realistic data for simulation testing."""
    return _create_test_db([
        ('stock_meat_category', ['ids', 'name_type'], [
            ('1', 'PORK'),
            ('2', 'CHICKEN'),
            ('3', 'test'),
        ]),
        ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [
            ('1', 'Supplier_A', '14.0,100.0'),
            ('2', 'Supplier_B', '13.7,100.5'),
        ]),
        ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
            ('1', 'Pork_Neck', '8001', '185.0', '31.7', '6.2', '1'),
            ('2', 'Chicken_Breast', '1001', '172.0', '21.0', '3.0', '2'),
            ('3', 'Pork_A_Dup', '8001', '185.0', '31.7', '6.2', '1'),  # duplicate SKU
            ('4', 'Pork_B_Dup', '8001', '190.0', '30.0', '7.0', '1'),  # duplicate SKU
            ('10', 'Chicken_Mid_Wing', '1002', '200.0', '18.0', '12.0', None),  # no category
            ('21', 'Test_Product', '9001', '0.0', '0.0', '0.0', '3'),  # test category
        ]),
        ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
            ('1', '1', '1', '1', '1', '0.0', '82', '97', '2024-01-15 10:00:00'),
            ('2', '2', '1', '2', '1', '0.0', '65', '85', '2024-01-15 11:00:00'),
            ('3', '3', '1', '1', '1', '0.0', '82', '97', '2024-01-15 12:00:00'),
            ('4', '4', '1', '1', '1', '0.0', '82', '97', '2024-01-15 13:00:00'),
            ('19', '10', '1', '1', '1', '0.0', '50', '60', '2024-01-15 14:00:00'),
            ('21', '21', '1', '1', '1', '0.0', '10', '12', '2024-01-15 15:00:00'),
        ]),
        ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
            ('1', '1', '1-1-8001-0001', '850', '82', 'frozen', '0', 'LV-001', '', '', '1', '2024-01-15 10:00:00'),
            ('2', '2', '1-2-1001-0001', '750', '65', 'frozen', '0', 'LV-002', '', '', '1', '2024-01-15 11:00:00'),
            ('67', '1', '1-1-8001-0067', '900', '82', 'depleted', '3', 'LV-067', '', '', '0', '2024-01-15 12:00:00'),  # conflict
            ('80', '1', '1-1-8001-0080', '800', '82', 'depleted', '4', 'LV-080', '', '', '0', '2024-01-15 13:00:00'),  # conflict
            ('100', '19', '1-1-1002-0001', '500', '50', 'frozen', '0', '', '', '', '0', '2024-01-15 14:00:00'),
            ('200', '21', '1-1-9001-0001', '300', '30', 'frozen', '0', '', '', '', '0', '2024-01-15 15:00:00'),
            ('300', '1', '1-1-8001-0002', '400', '40', 'pending', '0', '', '', '', '0', '2024-01-15 16:00:00'),
        ]),
    ])


# ============================================================
# TEST 1: Simulation runs without errors
# ============================================================

class TestSimulationBasic(TestCase):
    def test_simulation_runs(self):
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()
            s = sim.summary()
            self.assertGreater(s['total_target_candidates'], 0)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 2: Duplicate SKU is TARGET_CONSTRAINT_BLOCKER
# ============================================================

class TestDuplicateSkuBlocker(TestCase):
    def test_duplicate_sku_blocks_products(self):
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()

            # Find blocked products with duplicate SKU
            blocked_products = [r for r in sim.results.get('products', [])
                              if r.status == 'BLOCKED']
            sku_blockers = [r for r in blocked_products
                          for f in r.failures
                          if f.category == FailureCategory.TARGET_CONSTRAINT_BLOCKER]
            self.assertGreater(len(sku_blockers), 0,
                'Should have TARGET_CONSTRAINT_BLOCKER for duplicate SKUs')

            # Verify the blocker mentions the specific SKU
            for r in sku_blockers:
                sku_failures = [f for f in r.failures
                               if f.category == FailureCategory.TARGET_CONSTRAINT_BLOCKER]
                for f in sku_failures:
                    self.assertIn('Duplicate SKU', f.message)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 3: Package #67/#80 HISTORICAL_DATA_LOSS_RISK
# ============================================================

class TestPackageStateConflict(TestCase):
    def test_package_67_and_80_have_data_loss_risk(self):
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()

            # Check for HISTORICAL_DATA_LOSS_RISK
            loss_risks = [f for f in sim.failures
                         if f.category == FailureCategory.HISTORICAL_DATA_LOSS_RISK]
            self.assertGreater(len(loss_risks), 0,
                'Should have HISTORICAL_DATA_LOSS_RISK for Package #67/#80')

            # Verify it mentions thaw queue
            for f in loss_risks:
                self.assertIn('thaw_queue', f.message.lower())
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 4: Traceability — every record has legacy ID
# ============================================================

class TestTraceability(TestCase):
    def test_every_record_has_legacy_id(self):
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()

            for entity_key, records in sim.results.items():
                for r in records:
                    self.assertIsNotNone(r.legacy_id,
                        f'{entity_key} record missing legacy_id')
                    self.assertGreater(int(r.legacy_id), 0,
                        f'{entity_key} record has invalid legacy_id: {r.legacy_id}')
                    self.assertTrue(len(r.source_table) > 0,
                        f'{entity_key} record missing source_table')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 5: Legacy DB unchanged
# ============================================================

class TestLegacyUnchanged(TestCase):
    def test_legacy_db_unchanged_after_simulation(self):
        db_path = _make_simulation_db()
        try:
            hash_before = file_hash(db_path)
            sim = MigrationSimulation(db_path)
            sim.run()
            hash_after = file_hash(db_path)
            self.assertEqual(hash_before, hash_after,
                'Legacy database must not be modified by simulation')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 6: Resolution traceability
# ============================================================

class TestResolutionTraceability(TestCase):
    def test_product_10_has_resolution_status(self):
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()

            prod_10 = [r for r in sim.results.get('products', [])
                      if int(r.legacy_id) == 10]
            self.assertEqual(len(prod_10), 1)
            self.assertEqual(prod_10[0].resolution_status, 'APPLIED')
            self.assertEqual(prod_10[0].resolution_rule, 'RESOLVE_PRODUCT_10_CATEGORY')
        finally:
            os.unlink(db_path)

    def test_pending_package_has_resolution_status(self):
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()

            pkg_300 = [r for r in sim.results.get('packages', [])
                      if int(r.legacy_id) == 300]
            self.assertEqual(len(pkg_300), 1)
            self.assertEqual(pkg_300[0].resolution_status, 'APPLIED')
            self.assertEqual(pkg_300[0].resolution_rule, 'RESOLVE_PENDING_TO_PACKED')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 7: Category unique constraint
# ============================================================

class TestCategoryConstraints(TestCase):
    def test_test_category_does_not_block(self):
        """Category #3 ('test') is WARNING but not BLOCKED."""
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()

            cat_3 = [r for r in sim.results.get('categories', [])
                    if int(r.legacy_id) == 3]
            self.assertEqual(len(cat_3), 1)
            # test category is WARNING (accepted exception), not BLOCKED
            self.assertIn(cat_3[0].status, ('WARNING', 'INSERTABLE'))
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 8: Field validation
# ============================================================

class TestFieldValidation(TestCase):
    def test_weight_validation(self):
        """Weight must be > 0 and <= 999.999."""
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()

            for r in sim.results.get('packages', []):
                if r.status != 'BLOCKED':
                    weight = Decimal(r.target_data.get('weight_kg', '0'))
                    self.assertGreater(weight, Decimal('0'),
                        f'Package #{r.legacy_id} weight must be > 0')
                    self.assertLessEqual(weight, Decimal('999.999'),
                        f'Package #{r.legacy_id} weight must be <= 999.999')
        finally:
            os.unlink(db_path)

    def test_state_choices_valid(self):
        """All package states must be valid choices."""
        from decimal import Decimal
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()

            valid_states = ['PACKED', 'FREEZING', 'FROZEN', 'READY_FOR_THAW', 'THAW_QUEUED',
                           'THAWING', 'READY_FOR_SALE', 'ON_DISPLAY', 'REFREEZE_PENDING',
                           'PROCESSING', 'DISCARDED', 'COMPLETED']

            for r in sim.results.get('packages', []):
                state = r.target_data.get('canonical_state', '')
                if state:
                    self.assertIn(state, valid_states,
                        f'Package #{r.legacy_id} has invalid state: {state}')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 9: Summary counts are consistent
# ============================================================

class TestSummaryConsistency(TestCase):
    def test_insertable_plus_blocked_plus_warning_equals_total(self):
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()
            s = sim.summary()
            self.assertEqual(
                s['insertable'] + s['blocked'] + s['warnings'],
                s['total_target_candidates'],
                'Insertable + blocked + warnings must equal total')
        finally:
            os.unlink(db_path)


from decimal import Decimal
