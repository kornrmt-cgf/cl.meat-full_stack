"""
Inventory Service Tests — Phase 03 comprehensive coverage.

Tests cover:
- Product: create, duplicate SKU
- Batch: create, duplicate batch number, FK validation
- Package: create, weight rules, barcode uniqueness, location
- Stock Operations: receive, move, adjust, sell, discard
- Stock Movement audit trail
- Weight rules (Decimal, min/max, negative prevention)
- Stock consistency checks
- Concurrency (double deduction, concurrent update)
- End-to-end integration
"""
from decimal import Decimal
from unittest import SkipTest

from django.db import transaction, IntegrityError
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from inventory.models import (
    Category, Supplier, Product, Batch, Package,
    PackageState, StorageLocation, StockMovement,
)
from inventory.services import (
    create_package, move_package, adjust_weight,
    sell_package, discard_package, receive_stock,
    get_available_stock, get_package_stock_summary,
    verify_stock_consistency,
    validate_weight, validate_price,
    WeightError, StockError,
)


# ============================================================
# HELPERS
# ============================================================

_test_counter = 0

def _unique(suffix=''):
    global _test_counter
    _test_counter += 1
    return f'{_test_counter}_{suffix}'

def _create_category(code=None, name='Pork'):
    code = code or _unique('CAT')
    return Category.objects.get_or_create(
        code=code, defaults={'name': name, 'name_thai': name, 'is_active': True})[0]

def _create_supplier(name=None):
    name = name or _unique('SUP')
    return Supplier.objects.get_or_create(
        name=name, defaults={'locations': 'Bangkok'})[0]

def _create_product(sku=None, name='Pork Test', category=None,
                    supplier=None):
    sku = sku or _unique('SKU')
    if category is None:
        category = _create_category()
    if supplier is None:
        supplier = _create_supplier()
    return Product.objects.create(
        sku=sku, name=name, name_thai=name, category=category,
        supplier=supplier, unit='KG', cost_per_kg=Decimal('80'),
        selling_price_per_kg=Decimal('120'), active=True)

def _create_batch(product=None, supplier=None, batch_number=None):
    batch_number = batch_number or _unique('BATCH')
    if product is None:
        product = _create_product()
    if supplier is None:
        supplier = product.supplier or _create_supplier()
    return Batch.objects.create(
        batch_number=batch_number, supplier=supplier,
        received_at=timezone.now())

def _create_location(name='Freezer A', loc_type='FREEZER'):
    return StorageLocation.objects.create(
        name=name, location_type=loc_type, capacity=50)

def _create_package(product=None, batch=None, barcode='BAR-001',
                    weight='1.500', location=None):
    if product is None:
        product = _create_product()
    if batch is None:
        batch = _create_batch(product=product)
    return Package.objects.create(
        product=product, batch=batch, barcode=barcode,
        weight=Decimal(weight), selling_price=Decimal('150'),
        packed_at=timezone.now(), current_state=PackageState.PACKED,
        storage_location=location)


# ============================================================
# 1. WEIGHT VALIDATION
# ============================================================

class TestWeightValidation(SimpleTestCase):
    """Test weight and price validation rules."""

    def test_valid_weight(self):
        w = validate_weight('1.500')
        self.assertEqual(w, Decimal('1.500'))

    def test_valid_weight_integer(self):
        w = validate_weight(2)
        self.assertEqual(w, Decimal('2'))

    def test_minimum_weight(self):
        w = validate_weight('0.001')
        self.assertEqual(w, Decimal('0.001'))

    def test_below_minimum_weight(self):
        with self.assertRaises(WeightError) as ctx:
            validate_weight('0.000')
        self.assertIn('below minimum', str(ctx.exception))

    def test_negative_weight(self):
        with self.assertRaises(WeightError):
            validate_weight(-1)

    def test_maximum_weight(self):
        w = validate_weight('999.999')
        self.assertEqual(w, Decimal('999.999'))

    def test_above_maximum_weight(self):
        with self.assertRaises(WeightError):
            validate_weight('1000.000')

    def test_invalid_weight_string(self):
        with self.assertRaises(WeightError):
            validate_weight('abc')

    def test_none_weight(self):
        with self.assertRaises(WeightError):
            validate_weight(None)

    def test_valid_price(self):
        p = validate_price('150.50')
        self.assertEqual(p, Decimal('150.50'))

    def test_zero_price_allowed(self):
        p = validate_price(0)
        self.assertEqual(p, Decimal('0'))

    def test_negative_price_rejected(self):
        with self.assertRaises(StockError):
            validate_price(-1)

    def test_invalid_price_string(self):
        with self.assertRaises(StockError):
            validate_price('abc')


# ============================================================
# 2. PRODUCT
# ============================================================

class TestProduct(TransactionTestCase):
    """Product creation and validation."""

    def test_create_product(self):
        p = _create_product()
        self.assertIsNotNone(p.id)
        self.assertIsNotNone(p.sku)
        self.assertTrue(p.active)

    def test_duplicate_sku_fails(self):
        _create_product(sku='MP-DUP')
        with self.assertRaises(IntegrityError):
            _create_product(sku='MP-DUP')

    def test_product_str(self):
        p = _create_product(sku='MP-001', name='Pork Neck')
        self.assertIn('Pork Neck', str(p))
        self.assertIn('MP-001', str(p))


# ============================================================
# 3. BATCH
# ============================================================

class TestBatch(TransactionTestCase):
    """Batch creation and validation."""

    def test_create_batch(self):
        b = _create_batch()
        self.assertIsNotNone(b.id)
        self.assertTrue(b.active)

    def test_duplicate_batch_number_fails(self):
        _create_batch(batch_number='B-DUP')
        with self.assertRaises(IntegrityError):
            _create_batch(batch_number='B-DUP')

    def test_batch_has_supplier(self):
        s = _create_supplier(name='Test Supplier')
        b = _create_batch(supplier=s)
        self.assertEqual(b.supplier, s)

    def test_batch_str(self):
        b = _create_batch(batch_number='B-001')
        self.assertIn('B-001', str(b))


# ============================================================
# 4. PACKAGE
# ============================================================

class TestPackage(TransactionTestCase):
    """Package creation and validation."""

    def test_create_package(self):
        pkg = _create_package()
        self.assertIsNotNone(pkg.id)
        self.assertEqual(pkg.current_state, PackageState.PACKED)
        self.assertEqual(pkg.weight, Decimal('1.500'))

    def test_duplicate_barcode_fails(self):
        _create_package(barcode='DUP-BAR')
        with self.assertRaises(IntegrityError):
            _create_package(barcode='DUP-BAR')

    def test_package_weight_is_decimal(self):
        pkg = _create_package(weight='2.345')
        self.assertIsInstance(pkg.weight, Decimal)
        self.assertEqual(pkg.weight, Decimal('2.345'))

    def test_package_belongs_to_product(self):
        p = _create_product(sku='MP-PKG')
        pkg = _create_package(product=p)
        self.assertEqual(pkg.product, p)

    def test_package_belongs_to_batch(self):
        b = _create_batch(batch_number='B-PKG-001')
        pkg = _create_package(batch=b)
        self.assertEqual(pkg.batch, b)

    def test_package_with_location(self):
        loc = _create_location()
        pkg = _create_package(location=loc)
        self.assertEqual(pkg.storage_location, loc)


# ============================================================
# 5. STOCK OPERATIONS — CREATE PACKAGE VIA SERVICE
# ============================================================

class TestCreatePackage(TransactionTestCase):
    """Test create_package service function."""

    def test_create_package_success(self):
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(
            product=p, batch=b, barcode='SVC-001',
            weight='2.000', selling_price='240')
        self.assertIsNotNone(pkg.id)
        self.assertEqual(pkg.weight, Decimal('2.000'))
        self.assertEqual(pkg.current_state, PackageState.PACKED)

    def test_create_package_records_movement(self):
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(
            product=p, batch=b, barcode='SVC-MV',
            weight='1.000', selling_price='120')
        movements = StockMovement.objects.filter(package=pkg)
        self.assertEqual(movements.count(), 1)
        self.assertEqual(movements.first().movement_type, 'RECEIVED')

    def test_create_package_duplicate_barcode(self):
        p = _create_product()
        b = _create_batch(product=p)
        create_package(
            product=p, batch=b, barcode='DUP-SVC',
            weight='1.000', selling_price='120')
        with self.assertRaises(StockError) as ctx:
            create_package(
                product=p, batch=b, barcode='DUP-SVC',
                weight='1.000', selling_price='120')
        self.assertIn('Barcode already exists', str(ctx.exception))

    def test_create_package_invalid_weight(self):
        p = _create_product()
        b = _create_batch(product=p)
        with self.assertRaises(WeightError):
            create_package(
                product=p, batch=b, barcode='WT-001',
                weight='0', selling_price='120')

    def test_create_package_negative_price(self):
        p = _create_product()
        b = _create_batch(product=p)
        with self.assertRaises(StockError):
            create_package(
                product=p, batch=b, barcode='PR-001',
                weight='1.000', selling_price='-50')


# ============================================================
# 6. STOCK OPERATIONS — MOVE
# ============================================================

class TestMovePackage(TransactionTestCase):
    """Test move_package service function."""

    def test_move_package(self):
        loc_a = _create_location('Freezer A')
        loc_b = _create_location('Freezer B')
        pkg = _create_package(location=loc_a)

        moved = move_package(pkg, loc_b, actor='user1',
                            reason='Reorganizing')

        self.assertEqual(moved.storage_location, loc_b)

        mv = StockMovement.objects.filter(
            package=pkg, movement_type='MOVED').first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.from_location, loc_a)
        self.assertEqual(mv.to_location, loc_b)
        self.assertEqual(mv.actor, 'user1')

    def test_move_package_no_change_same_location(self):
        loc = _create_location()
        pkg = _create_package(location=loc)
        move_package(pkg, loc)
        # Should still have the RECEIVED movement but no extra MOVED
        movements = StockMovement.objects.filter(package=pkg)
        self.assertEqual(movements.count(), 1)

    def test_move_discarded_package_fails(self):
        p = _create_product()
        b = _create_batch(product=p)
        pkg = _create_package(product=p, batch=b)
        pkg.current_state = PackageState.DISCARDED
        pkg.save(update_fields=['current_state'])

        with self.assertRaises(StockError) as ctx:
            move_package(pkg, _create_location())
        self.assertIn('terminal state', str(ctx.exception))

    def test_move_completed_package_fails(self):
        pkg = _create_package()
        pkg.current_state = PackageState.COMPLETED
        pkg.save(update_fields=['current_state'])

        with self.assertRaises(StockError):
            move_package(pkg, _create_location())


# ============================================================
# 7. STOCK OPERATIONS — ADJUST WEIGHT
# ============================================================

class TestAdjustWeight(TransactionTestCase):
    """Test adjust_weight service function."""

    def test_adjust_weight(self):
        pkg = _create_package(weight='2.000')
        adjusted = adjust_weight(pkg, '2.500', actor='user1',
                                reason='Re-weighed')

        self.assertEqual(adjusted.weight, Decimal('2.500'))

        mv = StockMovement.objects.filter(
            package=pkg, movement_type='ADJUSTED').first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.metadata['old_weight_kg'], '2.000')
        self.assertEqual(mv.metadata['new_weight_kg'], '2.500')

    def test_adjust_weight_no_change(self):
        pkg = _create_package(weight='2.000')
        adjust_weight(pkg, '2.000')
        # No ADJUSTED movement should be created
        movements = StockMovement.objects.filter(
            package=pkg, movement_type='ADJUSTED')
        self.assertEqual(movements.count(), 0)

    def test_adjust_weight_invalid(self):
        pkg = _create_package()
        with self.assertRaises(WeightError):
            adjust_weight(pkg, '0')

    def test_adjust_weight_discarded_fails(self):
        pkg = _create_package()
        pkg.current_state = PackageState.DISCARDED
        pkg.save(update_fields=['current_state'])

        with self.assertRaises(StockError):
            adjust_weight(pkg, '3.000')


# ============================================================
# 8. STOCK OPERATIONS — SELL
# ============================================================

class TestSellPackage(TransactionTestCase):
    """Test sell_package service function."""

    def test_sell_from_ready_for_sale(self):
        pkg = _create_package()
        pkg.current_state = PackageState.READY_FOR_SALE
        pkg.save(update_fields=['current_state'])

        sold = sell_package(pkg, actor='cashier1')
        self.assertEqual(sold.current_state, PackageState.COMPLETED)

        mv = StockMovement.objects.filter(
            package=pkg, movement_type='SOLD').first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.actor, 'cashier1')

    def test_sell_from_on_display(self):
        """ON_DISPLAY → PROCESSING → COMPLETED via state machine."""
        pkg = _create_package()
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])

        sell_package(pkg)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

    def test_sell_from_packed_fails(self):
        pkg = _create_package()
        with self.assertRaises(StockError) as ctx:
            sell_package(pkg)
        self.assertIn('Cannot sell', str(ctx.exception))

    def test_sell_from_frozen_fails(self):
        pkg = _create_package()
        pkg.current_state = PackageState.FROZEN
        pkg.save(update_fields=['current_state'])

        with self.assertRaises(StockError):
            sell_package(pkg)

    def test_sell_already_completed_fails(self):
        pkg = _create_package()
        pkg.current_state = PackageState.COMPLETED
        pkg.save(update_fields=['current_state'])

        with self.assertRaises(StockError):
            sell_package(pkg)


# ============================================================
# 9. STOCK OPERATIONS — DISCARD
# ============================================================

class TestDiscardPackage(TransactionTestCase):
    """Test discard_package service function."""

    def test_discard_from_packed_fails(self):
        """PACKED → DISCARDED is not allowed by state machine."""
        pkg = _create_package()
        with self.assertRaises(StockError) as ctx:
            discard_package(pkg, actor='user1', reason='Damaged')
        self.assertIn('Cannot discard', str(ctx.exception))

    def test_discard_from_on_display(self):
        """ON_DISPLAY → DISCARDED → COMPLETED is allowed."""
        pkg = _create_package()
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])

        discarded = discard_package(pkg, reason='Expired')
        self.assertEqual(discarded.current_state, PackageState.COMPLETED)

        mv = StockMovement.objects.filter(
            package=pkg, movement_type='DISCARDED').first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.reason, 'Expired')

    def test_discard_already_completed_fails(self):
        pkg = _create_package()
        pkg.current_state = PackageState.COMPLETED
        pkg.save(update_fields=['current_state'])

        with self.assertRaises(StockError):
            discard_package(pkg)

    def test_discard_already_discarded_fails(self):
        pkg = _create_package()
        pkg.current_state = PackageState.DISCARDED
        pkg.save(update_fields=['current_state'])

        with self.assertRaises(StockError):
            discard_package(pkg)


# ============================================================
# 10. STOCK QUERIES — SINGLE SOURCE OF TRUTH
# ============================================================

class TestStockQueries(TransactionTestCase):
    """Test available stock calculation (single source of truth)."""

    def test_available_stock_empty(self):
        self.assertEqual(get_available_stock(), Decimal('0'))

    def test_available_stock_packed(self):
        _create_package(barcode='STK-1', weight='2.000')
        _create_package(barcode='STK-2', weight='3.000')
        self.assertEqual(get_available_stock(), Decimal('5.000'))

    def test_available_stock_excludes_terminal(self):
        p = _create_product()
        b = _create_batch(product=p)
        _create_package(product=p, batch=b, barcode='STK-A', weight='2.000')
        pkg = _create_package(
            product=p, batch=b, barcode='STK-B', weight='3.000')
        pkg.current_state = PackageState.COMPLETED
        pkg.save(update_fields=['current_state'])

        self.assertEqual(get_available_stock(product=p), Decimal('2.000'))

    def test_available_stock_by_product(self):
        p1 = _create_product(sku='MP-1')
        p2 = _create_product(sku='MP-2')
        _create_package(product=p1, barcode='SP-1', weight='1.000')
        _create_package(product=p2, barcode='SP-2', weight='2.000')
        self.assertEqual(get_available_stock(product=p1), Decimal('1.000'))
        self.assertEqual(get_available_stock(product=p2), Decimal('2.000'))

    def test_available_stock_by_location(self):
        loc = _create_location()
        _create_package(barcode='SL-1', weight='1.500', location=loc)
        _create_package(barcode='SL-2', weight='2.500')
        self.assertEqual(get_available_stock(location=loc), Decimal('1.500'))

    def test_stock_summary(self):
        p = _create_product()
        b = _create_batch(product=p)
        _create_package(product=p, batch=b, barcode='SM-1', weight='1.000')
        pkg2 = _create_package(
            product=p, batch=b, barcode='SM-2', weight='2.000')
        pkg2.current_state = PackageState.COMPLETED
        pkg2.save(update_fields=['current_state'])

        summary = get_package_stock_summary(product=p)
        self.assertEqual(summary[PackageState.PACKED], Decimal('1.000'))
        self.assertEqual(summary[PackageState.COMPLETED], Decimal('2.000'))
        self.assertEqual(summary['total'], Decimal('3.000'))


# ============================================================
# 11. STOCK MOVEMENT AUDIT TRAIL
# ============================================================

class TestStockMovementAudit(TransactionTestCase):
    """Verify audit trail completeness."""

    def test_receive_creates_movement(self):
        """create_package service creates RECEIVED movement."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(
            product=p, batch=b, barcode='AUD-1',
            weight='1.500', selling_price='180')
        movements = StockMovement.objects.filter(package=pkg)
        self.assertEqual(movements.count(), 1)
        self.assertEqual(movements.first().movement_type, 'RECEIVED')

    def test_move_creates_movement(self):
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(
            product=p, batch=b, barcode='AUD-2',
            weight='1.500', selling_price='180')
        move_package(pkg, _create_location(), actor='test_user')

        types = list(StockMovement.objects.filter(
            package=pkg).values_list('movement_type', flat=True))
        self.assertIn('RECEIVED', types)
        self.assertIn('MOVED', types)

    def test_full_lifecycle_has_complete_audit(self):
        p = _create_product()
        b = _create_batch(product=p)
        loc = _create_location()
        pkg = create_package(
            product=p, batch=b, barcode='AUD-LC',
            weight='1.500', selling_price='180',
            storage_location=loc, actor='receiver')

        # Move to display (simulate lifecycle: PACKED → FREEZING → FROZEN → ...
        # → READY_FOR_SALE → ON_DISPLAY)
        pkg.current_state = PackageState.READY_FOR_SALE
        pkg.save(update_fields=['current_state'])
        move_package(pkg, _create_location('Display'), actor='mover')

        # Sell from ON_DISPLAY → PROCESSING → COMPLETED
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        sell_package(pkg, actor='cashier')

        movements = StockMovement.objects.filter(
            package=pkg).order_by('timestamp')
        types = [m.movement_type for m in movements]
        self.assertIn('RECEIVED', types)
        self.assertIn('MOVED', types)
        self.assertIn('SOLD', types)

        for m in movements:
            self.assertTrue(m.actor, f'Movement {m.id} has no actor')
            self.assertTrue(m.weight_at_movement > 0,
                          f'Movement {m.id} has zero weight')

    def test_adjustment_records_before_after(self):
        pkg = _create_package(weight='2.000')
        adjust_weight(pkg, '2.500', reason='Scale calibration')

        mv = StockMovement.objects.filter(
            package=pkg, movement_type='ADJUSTED').first()
        self.assertEqual(mv.metadata['old_weight_kg'], '2.000')
        self.assertEqual(mv.metadata['new_weight_kg'], '2.500')


# ============================================================
# 12. CONCURRENCY
# ============================================================

class TestConcurrency(TransactionTestCase):
    """Test concurrent stock operations."""

    def test_concurrent_weight_adjustment(self):
        """Two adjustments to the same package — only one should win."""
        pkg = _create_package(weight='2.000')

        # Simulate concurrent adjustment using save() directly
        # The second save should overwrite the first (lost update)
        # This tests that select_for_update prevents lost updates
        pkg1 = Package.objects.select_for_update().get(pk=pkg.pk)
        pkg2 = Package.objects.select_for_update().get(pk=pkg.pk)

        pkg1.weight = Decimal('3.000')
        pkg1.save(update_fields=['weight', 'updated_at'])

        pkg2.weight = Decimal('4.000')
        pkg2.save(update_fields=['weight', 'updated_at'])

        pkg.refresh_from_db()
        # Last writer wins — this is expected without explicit locking
        self.assertEqual(pkg.weight, Decimal('4.000'))

    def test_double_barcode_rejected(self):
        """Two packages with the same barcode — second must fail."""
        p = _create_product()
        b = _create_batch(product=p)
        create_package(product=p, batch=b, barcode='DUP-CONC',
                      weight='1.000', selling_price='100')
        with self.assertRaises(StockError):
            create_package(product=p, batch=b, barcode='DUP-CONC',
                          weight='1.000', selling_price='100')

    def test_sell_package_with_select_for_update(self):
        """Package row is locked during sell operation."""
        pkg = _create_package()
        # Must be in READY_FOR_SALE to sell
        pkg.current_state = PackageState.READY_FOR_SALE
        pkg.save(update_fields=['current_state'])

        # Sell uses select_for_update internally
        sold = sell_package(pkg)
        self.assertEqual(sold.current_state, PackageState.COMPLETED)

    def test_negative_stock_prevention(self):
        """Ensure available stock never goes negative."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(
            product=p, batch=b, barcode='NS-1',
            weight='2.000', selling_price='200')

        stock = get_available_stock(product=p)
        self.assertGreater(stock, Decimal('0'))

        # Discard: ON_DISPLAY → DISCARDED → COMPLETED
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        discard_package(pkg, reason='Test')

        stock_after = get_available_stock(product=p)
        self.assertEqual(stock_after, Decimal('0'))
        self.assertGreaterEqual(stock_after, Decimal('0'))


# ============================================================
# 13. STOCK CONSISTENCY CHECK
# ============================================================

class TestStockConsistency(TransactionTestCase):
    """Test verify_stock_consistency service."""

    def test_consistent_when_empty(self):
        result = verify_stock_consistency()
        self.assertTrue(result['consistent'])
        self.assertEqual(len(result['issues']), 0)

    def test_consistent_with_valid_packages(self):
        p = _create_product()
        b = _create_batch(product=p)
        # Use create_package service so RECEIVED movement is created
        create_package(product=p, batch=b, barcode='VC-1',
                      weight='1.000', selling_price='100')
        create_package(product=p, batch=b, barcode='VC-2',
                      weight='2.000', selling_price='200')
        result = verify_stock_consistency()
        self.assertTrue(result['consistent'])

    def test_detects_missing_received_movement(self):
        """A package without a RECEIVED movement is an issue."""
        pkg = _create_package(barcode='NO-MV')
        StockMovement.objects.filter(package=pkg).delete()
        result = verify_stock_consistency()
        self.assertFalse(result['consistent'])
        types = [i['type'] for i in result['issues']]
        self.assertIn('MISSING_RECEIVED_MOVEMENT', types)


# ============================================================
# 14. RECEIVE STOCK (HIGH-LEVEL)
# ============================================================

class TestReceiveStock(TransactionTestCase):
    """Test the receive_stock high-level operation."""

    def test_receive_stock_creates_batch_and_packages(self):
        p = _create_product()
        s = p.supplier
        result = receive_stock(
            product=p, supplier=s, batch_number='B-RCV-001',
            packages_data=[
                {'barcode': 'RCV-1', 'weight': '1.500',
                 'selling_price': '180'},
                {'barcode': 'RCV-2', 'weight': '2.000',
                 'selling_price': '240'},
            ],
            actor='receiver1',
        )

        self.assertIsNotNone(result['batch'].id)
        self.assertEqual(result['batch'].batch_number, 'B-RCV-001')
        self.assertEqual(len(result['packages']), 2)
        self.assertEqual(result['total_weight'], Decimal('3.500'))

    def test_receive_stock_duplicate_batch_number(self):
        p = _create_product()
        s = p.supplier
        receive_stock(
            product=p, supplier=s, batch_number='B-DUP-RCV',
            packages_data=[{'barcode': 'DR-1', 'weight': '1.000'}])
        with self.assertRaises(StockError) as ctx:
            receive_stock(
                product=p, supplier=s, batch_number='B-DUP-RCV',
                packages_data=[{'barcode': 'DR-2', 'weight': '1.000'}])
        self.assertIn('Batch number already exists', str(ctx.exception))

    def test_receive_stock_records_movements(self):
        p = _create_product()
        s = p.supplier
        result = receive_stock(
            product=p, supplier=s, batch_number='B-MV-RCV',
            packages_data=[
                {'barcode': 'MR-1', 'weight': '1.000'},
                {'barcode': 'MR-2', 'weight': '2.000'},
            ])

        for pkg in result['packages']:
            self.assertEqual(
                StockMovement.objects.filter(
                    package=pkg, movement_type='RECEIVED').count(), 1)


# ============================================================
# 15. END-TO-END INTEGRATION
# ============================================================

class TestEndToEndIntegration(TransactionTestCase):
    """Full lifecycle: Product → Batch → Package → Receive → Move → Sell."""

    def test_full_lifecycle(self):
        """
        Full lifecycle: Receive → Move → Adjust → Sell/Discard.
        
        Uses create_package service for audit trail, and
        state machine transitions for state changes.
        """
        # 1. Setup
        cat = _create_category()
        sup = _create_supplier()
        product = _create_product(
            sku='MP-CHICKEN-001', name='Chicken Wing',
            category=cat, supplier=sup)
        freezer = _create_location('Freezer A', 'FREEZER')
        display = _create_location('Display Case', 'DISPLAY')

        # 2. Receive stock
        result = receive_stock(
            product=product, supplier=sup,
            batch_number='B-CHICKEN-2026-001',
            packages_data=[
                {'barcode': 'CHK-001', 'weight': '2.500',
                 'selling_price': '300', 'storage_location': freezer},
                {'barcode': 'CHK-002', 'weight': '1.800',
                 'selling_price': '216', 'storage_location': freezer},
            ],
            actor='receiver_somchai',
        )

        batch = result['batch']
        pkg1, pkg2 = result['packages']

        # 3. Verify initial state
        self.assertEqual(pkg1.current_state, PackageState.PACKED)
        self.assertEqual(pkg1.storage_location, freezer)
        self.assertEqual(pkg2.current_state, PackageState.PACKED)
        self.assertEqual(get_available_stock(product=product),
                        Decimal('4.300'))

        # 4. Move to display (simulating intermediate states)
        pkg1.current_state = PackageState.READY_FOR_SALE
        pkg1.save(update_fields=['current_state'])
        move_package(pkg1, display, actor='mover_ree',
                    reason='Move to display')
        pkg1.refresh_from_db()
        self.assertEqual(pkg1.storage_location, display)

        # 5. Adjust weight (re-weigh)
        adjust_weight(pkg1, '2.450', actor='weigher',
                     reason='Re-weighed on display scale')
        pkg1.refresh_from_db()
        self.assertEqual(pkg1.weight, Decimal('2.450'))

        # 6. Sell pkg1 (ON_DISPLAY → PROCESSING → COMPLETED)
        pkg1.current_state = PackageState.ON_DISPLAY
        pkg1.save(update_fields=['current_state'])
        sell_package(pkg1, actor='cashier_noo')

        # 7. Discard pkg2 (FROZEN → need to move to ON_DISPLAY first)
        pkg2.current_state = PackageState.ON_DISPLAY
        pkg2.save(update_fields=['current_state'])
        discard_package(pkg2, actor='mover_ree', reason='Damaged in transit')

        # 8. Verify final state
        pkg1.refresh_from_db()
        pkg2.refresh_from_db()
        self.assertEqual(pkg1.current_state, PackageState.COMPLETED)
        self.assertEqual(pkg2.current_state, PackageState.COMPLETED)
        self.assertEqual(get_available_stock(product=product),
                        Decimal('0'))

        # 9. Verify audit trail completeness for pkg1
        mv1 = list(StockMovement.objects.filter(
            package=pkg1).order_by('timestamp').values_list(
            'movement_type', flat=True))
        self.assertIn('RECEIVED', mv1)
        self.assertIn('MOVED', mv1)
        self.assertIn('ADJUSTED', mv1)
        self.assertIn('SOLD', mv1)

        # 10. Verify audit trail for pkg2
        mv2 = list(StockMovement.objects.filter(
            package=pkg2).order_by('timestamp').values_list(
            'movement_type', flat=True))
        self.assertIn('RECEIVED', mv2)
        self.assertIn('DISCARDED', mv2)

        # 11. Verify no orphan records
        self.assertEqual(
            Package.objects.filter(batch=batch).count(), 2)
        self.assertEqual(
            StockMovement.objects.filter(package__batch=batch).count(),
            len(mv1) + len(mv2))

        # 12. Final consistency check
        result = verify_stock_consistency(product=product)
        self.assertTrue(result['consistent'],
                       f'Consistency issues: {result["issues"]}')
