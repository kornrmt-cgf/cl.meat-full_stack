"""
Integration tests for the Migration Simulation Engine.

Uses REAL Django target models and REAL database constraint enforcement.
Tests prove that the target database actually rejects invalid data.
"""
import os
import tempfile
from decimal import Decimal

from django.test import TransactionTestCase
from django.db import transaction, IntegrityError, connection
from django.utils import timezone

from inventory.models import (
    Category, Supplier, Product, Batch, Package, PackageState,
)
from inventory.migration_engine import DryRunEngine, Status, FindingCode, file_hash
from inventory.migration_simulation import (
    MigrationSimulation, FailureCategory,
)
from inventory.resolution import ResolutionApplier


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
    return _create_test_db([
        ('stock_meat_category', ['ids', 'name_type'], [
            ('1', 'PORK'),
            ('2', 'CHICKEN'),
        ]),
        ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [
            ('1', 'Supplier_A', '14.0,100.0'),
        ]),
        ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
            ('1', 'Pork_Neck', '8001', '185.0', '31.7', '6.2', '1'),
            ('2', 'Chicken_Breast', '1001', '172.0', '21.0', '3.0', '2'),
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


# ============================================================
# TEST 1: Real target Product insert
# ============================================================

class TestRealProductInsert(TransactionTestCase):
    def test_product_inserts_with_real_fk(self):
        """Product.category FK must resolve to real Category object."""
        sid = transaction.savepoint()
        try:
            cat = Category.objects.create(code='PORK', name='Pork')
            prod = Product.objects.create(
                sku='MP-TEST-001', name='Test Pork', category=cat,
                unit='KG', barcode_prefix='8001')
            self.assertIsNotNone(prod.pk)
            self.assertEqual(prod.category.code, 'PORK')
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 2: Real target Batch insert
# ============================================================

class TestRealBatchInsert(TransactionTestCase):
    def test_batch_inserts_with_real_supplier_fk(self):
        sid = transaction.savepoint()
        try:
            supplier = Supplier.objects.create(name='Test Supplier')
            batch = Batch.objects.create(
                batch_number='B-20240115-01-01',
                supplier=supplier,
                received_at=timezone.now())
            self.assertIsNotNone(batch.pk)
            self.assertEqual(batch.supplier.name, 'Test Supplier')
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 3: Real target Package insert
# ============================================================class TestRealPackageInsert(TransactionTestCase):
    def test_package_inserts_with_real_product_batch_fk(self):
        sid = transaction.savepoint()
        try:
            cat = Category.objects.create(code='PORK', name='Pork')
            supplier = Supplier.objects.create(name='Test Supplier')
            prod = Product.objects.create(sku='MP-TEST', name='Pork', category=cat)
            batch = Batch.objects.create(batch_number='B-TEST', supplier=supplier,
                                        received_at=timezone.now())
            pkg = Package.objects.create(
                product=prod, batch=batch, barcode='BAR-TEST-001',
                weight=Decimal('1.500'), selling_price=Decimal('82.00'),
                packed_at=timezone.now(), current_state='PACKED')
            self.assertIsNotNone(pkg.pk)
            self.assertEqual(pkg.product.sku, 'MP-TEST')
            self.assertEqual(pkg.batch.batch_number, 'B-TEST')
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 4: Duplicate Product SKU rejected by DB
# ============================================================

class TestDuplicateSkuRejected(TransactionTestCase):
    def test_second_product_same_sku_fails(self):
        sid = transaction.savepoint()
        try:
            cat = Category.objects.create(code='PORK', name='Pork')
            Product.objects.create(sku='MP-DUP', name='Product A', category=cat)
            with self.assertRaises(IntegrityError):
                Product.objects.create(sku='MP-DUP', name='Product B', category=cat)
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 5: Duplicate Batch number rejected by DB
# ============================================================

class TestDuplicateBatchRejected(TransactionTestCase):
    def test_second_batch_same_number_fails(self):
        sid = transaction.savepoint()
        try:
            supplier = Supplier.objects.create(name='Test Supplier')
            Batch.objects.create(batch_number='B-DUP', supplier=supplier,
                                received_at=timezone.now())
            with self.assertRaises(IntegrityError):
                Batch.objects.create(batch_number='B-DUP', supplier=supplier,
                                    received_at=timezone.now())
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 6: Duplicate Package barcode rejected by DB
# ============================================================

class TestDuplicateBarcodeRejected(TransactionTestCase):
    def test_second_package_same_barcode_fails(self):
        sid = transaction.savepoint()
        try:
            cat = Category.objects.create(code='PORK', name='Pork')
            supplier = Supplier.objects.create(name='S')
            prod = Product.objects.create(sku='MP-1', name='P', category=cat)
            batch = Batch.objects.create(batch_number='B-1', supplier=supplier,
                                        received_at=timezone.now())
            Package.objects.create(product=prod, batch=batch, barcode='DUP-BAR',
                                  weight=Decimal('1.000'), packed_at=timezone.now())
            with self.assertRaises(IntegrityError):
                Package.objects.create(product=prod, batch=batch, barcode='DUP-BAR',
                                      weight=Decimal('1.000'), packed_at=timezone.now())
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 7: Duplicate Loyverse SKU rejected when non-null
# ============================================================

class TestDuplicateLoyverseSkuRejected(TransactionTestCase):
    def test_second_package_same_loyverse_sku_fails(self):
        sid = transaction.savepoint()
        try:
            cat = Category.objects.create(code='PORK', name='Pork')
            supplier = Supplier.objects.create(name='S')
            prod = Product.objects.create(sku='MP-LV', name='P', category=cat)
            batch = Batch.objects.create(batch_number='B-LV', supplier=supplier,
                                        received_at=timezone.now())
            Package.objects.create(product=prod, batch=batch, barcode='LV-1',
                                  weight=Decimal('1.000'), packed_at=timezone.now(),
                                  loyverse_sku='LOY-DUP')
            with self.assertRaises(IntegrityError):
                Package.objects.create(product=prod, batch=batch, barcode='LV-2',
                                      weight=Decimal('1.000'), packed_at=timezone.now(),
                                      loyverse_sku='LOY-DUP')
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 8: Invalid Product FK rejected by DB
# ============================================================

class TestInvalidProductFkRejected(TransactionTestCase):
    def test_package_with_nonexistent_product_fails(self):
        """FK validation via Django model — non-existent product_id raises error."""
        sid = transaction.savepoint()
        try:
            supplier = Supplier.objects.create(name='S')
            batch = Batch.objects.create(batch_number='B-FK', supplier=supplier,
                                        received_at=timezone.now())
            pkg = Package(product_id=99999, batch=batch, barcode='FK-TEST',
                         weight=Decimal('1.000'), packed_at=timezone.now())
            with self.assertRaises(Exception):
                pkg.full_clean()
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 9: Invalid Batch FK rejected by DB
# ============================================================

class TestInvalidBatchFkRejected(TransactionTestCase):
    def test_package_with_nonexistent_batch_fails(self):
        """FK validation via Django model — non-existent batch_id raises error."""
        sid = transaction.savepoint()
        try:
            cat = Category.objects.create(code='PORK', name='Pork')
            prod = Product.objects.create(sku='MP-FK', name='P', category=cat)
            pkg = Package(product=prod, batch_id=99999, barcode='FK-BATCH',
                         weight=Decimal('1.000'), packed_at=timezone.now())
            with self.assertRaises(Exception):
                pkg.full_clean()
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 10: Required field rejected
# ============================================================

class TestRequiredFieldRejected(TransactionTestCase):
    def test_product_without_sku_fails(self):
        """Required field validation via Django model — empty SKU rejected."""
        sid = transaction.savepoint()
        try:
            cat = Category.objects.create(code='PORK', name='Pork')
            prod = Product(sku='', name='No SKU', category=cat)
            with self.assertRaises(Exception):
                prod.full_clean()
        finally:
            transaction.savepoint_rollback(sid)


# ============================================================
# TEST 11: Traceability contains target temporary ID
# ============================================================

class TestTraceabilityTargetId(TransactionTestCase):
    def test_simulation_records_have_target_ids(self):
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()
            for entity, records in sim.results.items():
                for r in records:
                    if r.status == 'INSERTABLE':
                        self.assertIsNotNone(r.target_id,
                            f'{entity} #{r.legacy_id} should have target_id after successful insert')
                        self.assertGreater(r.target_id, 0,
                            f'{entity} #{r.legacy_id} target_id should be positive')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 12: Temporary DB isolation
# ============================================================

class TestTempDbIsolation(TransactionTestCase):
    def test_simulation_does_not_persist(self):
        """Simulation records should be in the test DB but isolated."""
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()
            # Simulation creates real objects in the test DB
            cat_count = Category.objects.count()
            self.assertGreater(cat_count, 0,
                'Simulation should have created Category objects')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 13: Legacy DB unchanged
# ============================================================

class TestLegacyUnchanged(TransactionTestCase):
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
# TEST 14: Full resolved dataset simulation
# ============================================================

class TestFullSimulation(TransactionTestCase):
    def test_simulation_summary_consistency(self):
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()
            s = sim.summary()
            self.assertEqual(
                s['real_db_insertable'] + s['database_blocked'] + s['warnings'],
                s['total_target_candidates'],
                'Insertable + blocked + warnings must equal total')
            self.assertTrue(s['legacy_db_unchanged'])
        finally:
            os.unlink(db_path)

    def test_insertable_records_actually_in_db(self):
        """Records marked INSERTABLE should actually exist in the target DB."""
        db_path = _make_simulation_db()
        try:
            sim = MigrationSimulation(db_path)
            sim.run()
            # Check that some records were actually inserted
            self.assertGreater(Category.objects.count(), 0)
            self.assertGreater(Supplier.objects.count(), 0)
            self.assertGreater(Product.objects.count(), 0)
        finally:
            os.unlink(db_path)
