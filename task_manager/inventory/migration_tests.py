"""
Tests for the legacy migration dry-run engine.

Uses temporary test databases — never touches real operational data.
"""
import json
import os
import sqlite3
import tempfile
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from inventory.migration_engine import (
    DryRunEngine, LegacyDB, Status, Severity, ReconciliationReport,
    map_categories, map_suppliers, map_products, map_batches, map_packages,
    _map_storage_status, _generate_sku, make_batch_id, file_hash,
)


# ============================================================
# HELPERS
# ============================================================

def _create_test_db(tables_and_data):
    """Create a temporary SQLite database with specified tables and data."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
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


def _make_empty_db():
    return _create_test_db([])


def _make_minimal_db():
    """Create a minimal database with one category, one supplier, one product, one batch, one package."""
    return _create_test_db([
        ('stock_meat_category', ['ids', 'name_type'], [
            ('1', 'หมูสดใส่ถุง'),
        ]),
        ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [
            ('1', 'Test Supplier', '14.0,100.0'),
        ]),
        ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
            ('1', 'หมูบด', '8001', '185.0', '31.7', '6.2', '1'),
        ]),
        ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
            ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15 10:00:00'),
        ]),
        ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
            ('1', '1', '1-1-8001-0001', '850.0', '82.0', 'frozen', '0', '30001', '', '', '0', '2024-01-15 10:00:00'),
        ]),
    ])


# ============================================================
# TEST 1: Empty source
# ============================================================

class TestEmptySource(TestCase):
    def test_empty_database(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], []),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], []),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], []),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], []),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            self.assertEqual(engine.results['summary']['total'], 0)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 2: One valid product
# ============================================================

class TestOneValidProduct(TestCase):
    def test_single_product_valid(self):
        db_path = _make_minimal_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            prods = engine.results['products']
            self.assertEqual(len(prods), 1)
            self.assertEqual(prods[0].status, Status.VALID)
            self.assertEqual(prods[0].data['sku'], 'MP-8001')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 3: Multiple products
# ============================================================

class TestMultipleProducts(TestCase):
    def test_multiple_products(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู'), ('2', 'ไก่')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [('1', 'S1', '')]),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
                ('2', 'อกไก่', '1003', '172', '21', '3', '2'),
                ('3', 'สะโพกหมู', '8005', '200', '25', '10', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], []),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], []),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            prods = engine.results['products']
            self.assertEqual(len(prods), 3)
            skus = [p.data['sku'] for p in prods]
            self.assertIn('MP-8001', skus)
            self.assertIn('MP-1003', skus)
            self.assertIn('MP-8005', skus)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 4: Duplicate SKU
# ============================================================

class TestDuplicateSku(TestCase):
    def test_both_duplicates_are_warnings(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด A', '8001', '185', '31.7', '6.2', '1'),
                ('2', 'หมูบด B', '8001', '190', '30', '7', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], []),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], []),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            prods = engine.results['products']
            # Both should be WARNING since they share a SKU
            for p in prods:
                self.assertEqual(p.status, Status.WARNING,
                    f'Product {p.legacy_id} should be WARNING, got {p.status}')
            self.assertEqual(len(prods), 2)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 5: Missing category
# ============================================================

class TestMissingCategory(TestCase):
    def test_product_without_category(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], []),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '999'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], []),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], []),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            prods = engine.results['products']
            self.assertEqual(len(prods), 1)
            self.assertEqual(prods[0].status, Status.SKIPPED)
            errors = [i for i in prods[0].issues if i.severity == Severity.ERROR]
            self.assertTrue(any('Category' in i.message for i in errors))
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 6: Missing supplier
# ============================================================

class TestMissingSupplier(TestCase):
    def test_batch_without_supplier(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', None, '1', '0.0', '82.0', '97.0', '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], []),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            batches = engine.results['batches']
            self.assertEqual(len(batches), 1)
            warnings = [i for i in batches[0].issues if i.severity == Severity.WARNING]
            self.assertTrue(any('supplier' in i.message.lower() for i in warnings))
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 7: Duplicate batch candidate
# ============================================================

class TestDuplicateBatch(TestCase):
    def test_same_supplier_lot_date(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [('1', 'S1', '')]),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
                ('2', 'สะโพก', '8005', '200', '25', '10', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15 10:00:00'),
                ('2', '2', '1', '1', '1', '0.0', '100.0', '125.0', '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], []),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            batches = engine.results['batches']
            batch_numbers = [b.data['batch_number'] for b in batches]
            # Same supplier + same lot + same date = same batch number
            self.assertEqual(batch_numbers[0], batch_numbers[1])
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 8: Zero weight
# ============================================================

class TestZeroWeight(TestCase):
    def test_package_zero_weight(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [('1', 'S1', '')]),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                ('1', '1', '1-1-8001-0001', '0.0', '82.0', 'frozen', '0', '', '', '', '0', '2024-01-15 10:00:00'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            pkgs = engine.results['packages']
            self.assertEqual(len(pkgs), 1)
            self.assertEqual(pkgs[0].status, Status.SKIPPED)
            errors = [i for i in pkgs[0].issues if i.severity == Severity.ERROR]
            self.assertTrue(any('weight' in i.message.lower() for i in errors))
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 9: Negative weight
# ============================================================

class TestNegativeWeight(TestCase):
    def test_package_negative_weight(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [('1', 'S1', '')]),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                ('1', '1', '1-1-8001-0001', '-500.0', '82.0', 'frozen', '0', '', '', '', '0', '2024-01-15 10:00:00'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            pkgs = engine.results['packages']
            self.assertEqual(pkgs[0].status, Status.SKIPPED)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 10: Duplicate barcode
# ============================================================

class TestDuplicateBarcode(TestCase):
    def test_duplicate_barcodes(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [('1', 'S1', '')]),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                ('1', '1', '1-1-8001-0001', '850.0', '82.0', 'frozen', '0', '', '', '', '0', '2024-01-15 10:00:00'),
                ('2', '1', '1-1-8001-0001', '900.0', '85.0', 'frozen', '0', '', '', '', '0', '2024-01-15 10:00:00'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            pkgs = engine.results['packages']
            statuses = [p.status for p in pkgs]
            self.assertIn(Status.SKIPPED, statuses)
            # Both should be candidates (1 valid + 1 skipped)
            self.assertEqual(len(pkgs), 2)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 11: Invalid state
# ============================================================

class TestInvalidState(TestCase):
    def test_unknown_storage_status(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [('1', 'S1', '')]),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                ('1', '1', '1-1-8001-0001', '850.0', '82.0', 'UNKNOWN_STATUS', '0', '', '', '', '0', '2024-01-15 10:00:00'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            pkgs = engine.results['packages']
            self.assertEqual(pkgs[0].status, Status.SKIPPED)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 12: Conflicting state
# ============================================================

class TestConflictingState(TestCase):
    def test_depleted_with_thaw_queue(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [('1', 'S1', '')]),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                ('1', '1', '1-1-8001-0001', '850.0', '82.0', 'depleted', '3', '', '', '', '0', '2024-01-15 10:00:00'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            pkgs = engine.results['packages']
            self.assertEqual(pkgs[0].status, Status.WARNING)
            warnings = [i for i in pkgs[0].issues if i.severity == Severity.WARNING]
            self.assertTrue(any('inconsistent' in i.message.lower() or 'conflicting' in i.message.lower() for i in warnings))
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 13: Deterministic SKU
# ============================================================

class TestDeterministicSku(TestCase):
    def test_same_input_same_sku(self):
        sku1 = _generate_sku('8001')
        sku2 = _generate_sku('8001')
        self.assertEqual(sku1, sku2)
        self.assertEqual(sku1, 'MP-8001')

    def test_empty_prefix(self):
        sku = _generate_sku('')
        self.assertIsNone(sku)

    def test_none_prefix(self):
        sku = _generate_sku(None)
        self.assertIsNone(sku)


# ============================================================
# TEST 14: Deterministic batch number
# ============================================================

class TestDeterministicBatch(TestCase):
    def test_batch_number_format(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], [('1', 'S1', '')]),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15 10:00:00'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], []),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            batches = engine.results['batches']
            self.assertEqual(len(batches), 1)
            self.assertTrue(batches[0].data['batch_number'].startswith('B-20240115-01-01'))
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 15: Repeated dry-run
# ============================================================

class TestRepeatedDryRun(TestCase):
    def test_same_results_twice(self):
        db_path = _make_minimal_db()
        try:
            engine1 = DryRunEngine(db_path)
            engine1.run()
            result1 = [(c.legacy_id, c.status) for c in engine1.results['products']]

            engine2 = DryRunEngine(db_path)
            engine2.run()
            result2 = [(c.legacy_id, c.status) for c in engine2.results['products']]

            self.assertEqual(result1, result2)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 16: Already-migrated detection
# ============================================================

class TestAlreadyMigratedDetection(TestCase):
    def test_already_migrated_lookup(self):
        db_path = _make_minimal_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            for c in engine.results['categories']:
                self.assertEqual(c.legacy_source, 'stock_meat_category')
                self.assertIsNotNone(c.legacy_id)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST 17: Read-only protection
# ============================================================

class TestReadOnlyProtection(TestCase):
    def test_readonly_uri(self):
        db_path = _make_minimal_db()
        try:
            db = LegacyDB(db_path)
            db.open()
            # Verify connection works for reads
            count = db.table_count('stock_meat_category')
            self.assertEqual(count, 1)
            # Verify writes fail
            with self.assertRaises(sqlite3.OperationalError):
                db.conn.execute("INSERT INTO stock_meat_category (ids, name_type) VALUES ('99', 'hacked')")
            db.close()
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: State mapping
# ============================================================

class TestStateMapping(TestCase):
    def test_frozen_no_queue(self):
        row = {'storage_status': 'frozen', 'thaw_queue_position': 0}
        state, note = _map_storage_status(row)
        self.assertEqual(state, 'FROZEN')

    def test_frozen_with_queue(self):
        row = {'storage_status': 'frozen', 'thaw_queue_position': 3}
        state, note = _map_storage_status(row)
        self.assertEqual(state, 'THAW_QUEUED')

    def test_thawing(self):
        row = {'storage_status': 'thawing', 'thaw_queue_position': 0}
        state, note = _map_storage_status(row)
        self.assertEqual(state, 'THAWING')

    def test_display(self):
        row = {'storage_status': 'display', 'thaw_queue_position': 0}
        state, note = _map_storage_status(row)
        self.assertEqual(state, 'ON_DISPLAY')

    def test_depleted(self):
        row = {'storage_status': 'depleted', 'thaw_queue_position': 0}
        state, note = _map_storage_status(row)
        self.assertEqual(state, 'COMPLETED')

    def test_unknown(self):
        row = {'storage_status': 'bogus', 'thaw_queue_position': 0}
        state, note = _map_storage_status(row)
        self.assertIsNone(state)


# ============================================================
# TEST: JSON export
# ============================================================

class TestJsonExport(TestCase):
    def test_export_json(self):
        db_path = _make_minimal_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            out_path = db_path + '.report.json'
            engine.export_json(out_path)
            data = json.loads(Path(out_path).read_text())
            self.assertIn('migration_batch', data)
            self.assertIn('summary', data)
            self.assertIn('reconciliation', data)
            # All minimal DB products are valid
            self.assertEqual(data['summary']['valid'], data['summary']['total'])
            os.unlink(out_path)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: Reconciliation invariant
# ============================================================

class TestReconciliation(TestCase):
    def test_invariant_holds_minimal_db(self):
        """source_count == valid + warning + invalid + skipped for each model."""
        db_path = _make_minimal_db()
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            for model_name, m in engine.reconciliation.models.items():
                ok, computed = engine.reconciliation.check_invariant(model_name)
                self.assertTrue(ok,
                    f'{model_name}: source={m["source_count"]} ≠ '
                    f'valid({m["valid"]})+warning({m["warning"]})+'
                    f'invalid({m["invalid"]})+skipped({m["skipped"]})={computed}')
        finally:
            os.unlink(db_path)

    def test_invariant_holds_empty_db(self):
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], []),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], []),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], []),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], []),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            for model_name, m in engine.reconciliation.models.items():
                ok, computed = engine.reconciliation.check_invariant(model_name)
                self.assertTrue(ok, f'{model_name} invariant violated')
                self.assertEqual(m['source_count'], 0)
                self.assertEqual(computed, 0)
        finally:
            os.unlink(db_path)

    def test_invariant_with_skipped_product(self):
        """Missing category → product SKIPPED → package that references it SKIPPED."""
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], []),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '999'),  # bad category
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                ('1', '1', '1-1-8001-0001', '850.0', '82.0', 'frozen', '0', '', '', '', '0', '2024-01-15'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            # All models should satisfy the invariant
            for model_name in ['categories', 'suppliers', 'products', 'batches', 'packages']:
                ok, computed = engine.reconciliation.check_invariant(model_name)
                self.assertTrue(ok, f'{model_name} invariant violated')
            # Product is skipped, so package should also be skipped
            self.assertEqual(engine.results['products'][0].status, Status.SKIPPED)
            self.assertEqual(engine.results['packages'][0].status, Status.SKIPPED)
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: Issue count is not record count
# ============================================================

class TestIssueCount(TestCase):
    def test_multiple_issues_one_record(self):
        """One record with multiple issues should still count as one candidate."""
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                ('1', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                # depleted + thaw_queue=3 = conflicting state + negative price
                ('1', '1', '1-1-8001-0001', '850.0', '-10', 'depleted', '3', '', '', '', '0', '2024-01-15'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            pkgs = engine.results['packages']
            self.assertEqual(len(pkgs), 1)
            # Should have 2 issues (conflicting state + negative price) but only 1 candidate
            self.assertGreaterEqual(len(pkgs[0].issues), 2)
            # The reconciliation should still hold
            ok, _ = engine.reconciliation.check_invariant('packages')
            self.assertTrue(ok, 'packages invariant violated')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: Product resolution through product_info chain
# ============================================================

class TestProductResolution(TestCase):
    def test_correct_product_via_product_info_chain(self):
        """Package should resolve to the correct product via product_info → meat_parts chain."""
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู'), ('2', 'ไก่')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
                ('2', 'อกไก่', '1003', '172', '21', '3', '2'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                # product_info 1 → meat_parts 2 (อกไก่)
                ('1', '2', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                # package references product_info 1 (which maps to meat_parts 2, not 1!)
                ('1', '1', '1-1-1003-0001', '850.0', '82.0', 'frozen', '0', '', '', '', '0', '2024-01-15'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            pkgs = engine.results['packages']
            self.assertEqual(len(pkgs), 1)
            self.assertEqual(pkgs[0].status, Status.VALID)
            # Should resolve to อกไก่ (meat_parts 2), not หมูบด (meat_parts 1)
            self.assertEqual(pkgs[0].data['product_sku'], 'MP-1003')
            self.assertEqual(str(pkgs[0].data['meat_parts_id']), '2')
        finally:
            os.unlink(db_path)

    def test_skipped_product_info(self):
        """Package referencing product_info with invalid meat_parts should be SKIPPED."""
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                # product_info 1 → meat_parts 999 (doesn't exist)
                ('1', '999', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                ('1', '1', '1-1-8001-0001', '850.0', '82.0', 'frozen', '0', '', '', '', '0', '2024-01-15'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            pkgs = engine.results['packages']
            self.assertEqual(len(pkgs), 1)
            self.assertEqual(pkgs[0].status, Status.SKIPPED)
        finally:
            os.unlink(db_path)

    def test_high_product_info_ids_resolve(self):
        """Product_info IDs > meat_parts max ID should resolve correctly through the chain."""
        db_path = _create_test_db([
            ('stock_meat_category', ['ids', 'name_type'], [('1', 'หมู')]),
            ('stock_meat_supply_meat', ['ids', 'name_place', 'locations'], []),
            ('stock_meat_meat_parts', ['id', 'name', 'prefix_barcode', 'kcalories', 'protent', 'fat', 'category_id'], [
                ('1', 'หมูบด', '8001', '185', '31.7', '6.2', '1'),
                ('5', 'สันนอก', '8005', '200', '25', '10', '1'),
            ]),
            ('stock_meat_product_info', ['id', 'name_id', 'type_product_id', 'import_from_id', 'lot_number', 'weight', 'cost', 'selling_price_per_kg', 'created_at'], [
                # product_info 32 → meat_parts 1 (high ID, should still resolve)
                ('32', '1', '1', '1', '1', '0.0', '82.0', '97.0', '2024-01-15'),
                ('33', '5', '1', '1', '1', '0.0', '100.0', '125.0', '2024-01-15'),
            ]),
            ('stock_meat_product_list', ['id', 'product_id', 'barcode', 'weight', 'selling_price', 'storage_status', 'thaw_queue_position', 'loyverse_sku', 'loyverse_item_id', 'loyverse_variant_id', 'loyverse_synced', 'mfg'], [
                ('1', '32', '1-1-8001-0001', '850.0', '82.0', 'frozen', '0', '', '', '', '0', '2024-01-15'),
                ('2', '33', '1-1-8005-0002', '900.0', '100.0', 'frozen', '0', '', '', '', '0', '2024-01-15'),
            ]),
        ])
        try:
            engine = DryRunEngine(db_path)
            engine.run()
            pkgs = engine.results['packages']
            self.assertEqual(len(pkgs), 2)
            for p in pkgs:
                self.assertEqual(p.status, Status.VALID)
            self.assertEqual(pkgs[0].data['product_sku'], 'MP-8001')
            self.assertEqual(str(pkgs[0].data['meat_parts_id']), '1')
            self.assertEqual(pkgs[1].data['product_sku'], 'MP-8005')
            self.assertEqual(str(pkgs[1].data['meat_parts_id']), '5')
        finally:
            os.unlink(db_path)


# ============================================================
# TEST: File hash (read-only verification)
# ============================================================

class TestFileHash(TestCase):
    def test_file_unchanged_after_dry_run(self):
        db_path = _make_minimal_db()
        try:
            hash_before = file_hash(db_path)
            engine = DryRunEngine(db_path)
            engine.run()
            hash_after = file_hash(db_path)
            self.assertEqual(hash_before, hash_after,
                'Legacy database was modified during dry-run!')
        finally:
            os.unlink(db_path)
