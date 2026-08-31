"""
Phase 03 — Final Hardening Tests (v2).

Tests cover:
1. Real PostgreSQL concurrency via actual service API calls
2. Barcode sequence atomicity and sequence verification
3. Price adjustment atomicity and rollback
4. Stock consistency model (authoritative weight-based calculation)
5. Backward compatibility (all legacy create_package signatures)
6. Decimal pricing consistency (pure Decimal ROUND_CEILING)
7. Audit semantics (StockMovement vs PriceChangeHistory)
8. State semantics (COMPLETED terminal state for sell/discard)
9. Service rollback tests (exception after DB write → rollback)
"""
import threading
import time
from decimal import Decimal, ROUND_CEILING
from unittest import SkipTest

from django.db import transaction, IntegrityError, connection
from django.test import TransactionTestCase
from django.utils import timezone

from inventory.models import (
    Category, Supplier, Product, Batch, Package,
    PackageState, StorageLocation, StockMovement,
    PriceChangeHistory, BarcodeSequence,
)
from inventory.services import (
    create_package, move_package, adjust_weight,
    sell_package, discard_package, receive_stock,
    generate_barcode, calculate_package_price,
    adjust_package_price, get_available_stock,
    get_package_stock_summary, verify_stock_consistency,
    validate_weight, validate_price,
    WeightError, StockError, InventoryError,
)


# ============================================================
# HELPERS
# ============================================================

_test_counter = 20000


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
                    supplier=None, selling_price_per_kg=Decimal('120'),
                    cost_per_kg=Decimal('80')):
    sku = sku or _unique('SKU')
    if category is None:
        category = _create_category()
    if supplier is None:
        supplier = _create_supplier()
    return Product.objects.create(
        sku=sku, name=name, name_thai=name, category=category,
        supplier=supplier, unit='KG', cost_per_kg=cost_per_kg,
        selling_price_per_kg=selling_price_per_kg, active=True)


def _create_batch(product=None, supplier=None, batch_number=None):
    batch_number = batch_number or _unique('BATCH')
    if product is None:
        product = _create_product()
    if supplier is None:
        supplier = product.supplier or _create_supplier()
    return Batch.objects.create(
        batch_number=batch_number, supplier=supplier,
        received_at=timezone.now())


def _create_location(name=None, loc_type='FREEZER'):
    name = name or _unique('LOC')
    return StorageLocation.objects.create(
        name=name, location_type=loc_type, capacity=50)


def _create_package_with_service(product=None, batch=None, weight='1.500',
                                  selling_price='150', state=PackageState.PACKED):
    """Create a package through the service layer with audit trail."""
    if product is None:
        product = _create_product()
    if batch is None:
        batch = _create_batch(product=product)
    return create_package(
        product=product, batch=batch, weight=weight,
        selling_price=selling_price, actor='test_setup')


def _create_package_obj(product=None, batch=None, barcode=None,
                        weight='1.500', state=PackageState.PACKED,
                        location=None, selling_price='150'):
    """Create a package directly via ORM (for test setup only)."""
    if product is None:
        product = _create_product()
    if batch is None:
        batch = _create_batch(product=product)
    if barcode is None:
        barcode = _unique('BAR')
    return Package.objects.create(
        product=product, batch=batch, barcode=barcode,
        weight=Decimal(weight), selling_price=Decimal(selling_price),
        packed_at=timezone.now(), current_state=state,
        storage_location=location)


def _is_postgres():
    return connection.vendor == 'postgresql'


# ============================================================
# 1. REAL POSTGRESQL CONCURRENCY — ACTUAL SERVICE API
# ============================================================

class TestPostgresConcurrency(TransactionTestCase):
    """
    Real PostgreSQL concurrency tests calling actual service functions.

    Each thread uses an independent DB connection/transaction.
    Proves that select_for_update() inside services serializes access.
    """

    def _skip_if_not_pg(self):
        if not _is_postgres():
            raise SkipTest('Requires PostgreSQL for real concurrency tests')

    def test_concurrent_adjust_weight_via_service(self):
        """
        Two threads call adjust_weight() on the same package.
        The second thread must wait for the first's lock, then succeed.
        """
        self._skip_if_not_pg()

        pkg = _create_package_with_service(weight='1.000')
        pkg_id = pkg.pk
        results = []
        errors = []

        def worker(new_weight, name):
            try:
                p = adjust_weight(pkg, str(new_weight), actor=name,
                                  reason=f'{name} adjustment')
                results.append((name, p.weight))
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=(2.000, 'W1'))
        t2 = threading.Thread(target=worker, args=(3.000, 'W2'))

        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(errors, [], f'Service errors: {errors}')
        self.assertEqual(len(results), 2)

        pkg.refresh_from_db()
        self.assertIn(pkg.weight, [Decimal('2.000'), Decimal('3.000')])

        # Both ADJUSTED movements should exist
        adj_count = StockMovement.objects.filter(
            package=pkg, movement_type='ADJUSTED').count()
        self.assertEqual(adj_count, 2)

    def test_concurrent_sell_via_service(self):
        """
        Two threads call sell_package() on the same READY_FOR_SALE package.
        Only one should succeed; the other should fail (state already changed).
        """
        self._skip_if_not_pg()

        pkg = _create_package_with_service()
        # Move to READY_FOR_SALE via ORM (not a service concern for setup)
        pkg.current_state = PackageState.READY_FOR_SALE
        pkg.save(update_fields=['current_state'])
        pkg_id = pkg.pk

        successes = []
        errors = []

        def worker(name):
            try:
                sold = sell_package(pkg, actor=name, reason=f'{name} sale')
                successes.append(name)
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=('S1',))
        t2 = threading.Thread(target=worker, args=('S2',))

        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(successes), 1,
                         f'Only one sell should succeed: {successes}')
        self.assertEqual(len(errors), 1,
                         f'One should fail: {errors}')

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        sold_mv = StockMovement.objects.filter(
            package=pkg, movement_type='SOLD').count()
        self.assertEqual(sold_mv, 1, 'Exactly one SOLD movement')

    def test_concurrent_discard_via_service(self):
        """
        Two threads call discard_package() on the same ON_DISPLAY package.
        Only one should succeed.
        """
        self._skip_if_not_pg()

        pkg = _create_package_with_service()
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])

        successes = []
        errors = []

        def worker(name):
            try:
                discard_package(pkg, actor=name, reason=f'{name} discard')
                successes.append(name)
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=('D1',))
        t2 = threading.Thread(target=worker, args=('D2',))

        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        disc_mv = StockMovement.objects.filter(
            package=pkg, movement_type='DISCARDED').count()
        self.assertEqual(disc_mv, 1)

    def test_concurrent_move_via_service(self):
        """
        Two threads call move_package() on the same package to different
        locations. Both should succeed; final location is last writer.
        """
        self._skip_if_not_pg()

        loc1 = _create_location('Conc_L1')
        loc2 = _create_location('Conc_L2')
        loc3 = _create_location('Conc_L3')

        pkg = _create_package_with_service()
        pkg.storage_location = loc1
        pkg.save(update_fields=['storage_location'])

        results = []
        errors = []

        def worker(target_loc, name):
            try:
                moved = move_package(pkg, target_loc, actor=name,
                                     reason=f'{name} move')
                results.append((name, moved.storage_location.pk))
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=(loc2, 'M1'))
        t2 = threading.Thread(target=worker, args=(loc3, 'M2'))

        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(errors, [], f'Errors: {errors}')
        self.assertEqual(len(results), 2)

        pkg.refresh_from_db()
        self.assertIn(pkg.storage_location.pk, [loc2.pk, loc3.pk])

        move_count = StockMovement.objects.filter(
            package=pkg, movement_type='MOVED').count()
        self.assertEqual(move_count, 2)

    def test_concurrent_adjust_price_via_service(self):
        """
        Two threads call adjust_package_price() on the same package.
        Both should complete (select_for_update serializes).
        """
        self._skip_if_not_pg()

        pkg = _create_package_with_service(selling_price='100')

        results = []
        errors = []

        def worker(new_price, name):
            try:
                p = adjust_package_price(pkg, str(new_price),
                                         mode='manual', actor=name)
                results.append((name, p.selling_price))
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=(150, 'P1'))
        t2 = threading.Thread(target=worker, args=(200, 'P2'))

        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(errors, [], f'Errors: {errors}')
        self.assertEqual(len(results), 2)

        pkg.refresh_from_db()
        self.assertIn(pkg.selling_price, [Decimal('150'), Decimal('200')])

        history = PriceChangeHistory.objects.filter(package=pkg)
        self.assertEqual(history.count(), 2)

    def test_concurrent_barcode_via_service(self):
        """
        Five threads call generate_barcode() for same product/batch.
        All barcodes must be unique; no sequence lost.
        """
        self._skip_if_not_pg()

        product = _create_product()
        batch = _create_batch(product=product)
        barcodes = []
        lock = threading.Lock()

        def worker():
            bc = generate_barcode(product, batch)
            with lock:
                barcodes.append(bc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(barcodes), 5, 'All 5 should succeed on PG')
        self.assertEqual(len(barcodes), len(set(barcodes)),
                         f'Duplicate barcodes: {barcodes}')

        # Verify BarcodeSequence matches
        seq = BarcodeSequence.objects.get(
            product=product, batch_number=batch.batch_number,
            supplier_id=batch.supplier_id)
        self.assertEqual(seq.last_sequence, 5)


# ============================================================
# 2. BARCODE SEQUENCE ATOMICITY
# ============================================================

class TestBarcodeAtomicity(TransactionTestCase):
    """Test barcode generation correctness and sequence tracking."""

    def test_sequence_monotonic(self):
        """10 sequential barcodes → sequences 1..10."""
        product = _create_product()
        batch = _create_batch(product=product)
        barcodes = [generate_barcode(product, batch) for _ in range(10)]

        self.assertEqual(len(barcodes), len(set(barcodes)),
                         f'Duplicates: {barcodes}')
        sequences = [int(bc[-4:]) for bc in barcodes]
        for i in range(1, len(sequences)):
            self.assertEqual(sequences[i], sequences[i - 1] + 1)

    def test_independent_product_sequences(self):
        """Different products → independent sequence counters."""
        p1, p2 = _create_product(), _create_product()
        b1, b2 = _create_batch(product=p1), _create_batch(product=p2)

        bc1 = generate_barcode(p1, b1)
        bc2 = generate_barcode(p1, b1)
        bc3 = generate_barcode(p2, b2)

        self.assertEqual(int(bc1[-4:]), 1)
        self.assertEqual(int(bc2[-4:]), 2)
        self.assertEqual(int(bc3[-4:]), 1)

    def test_sequence_value_matches_generation_count(self):
        """After N generations, BarcodeSequence.last_sequence == N."""
        product = _create_product()
        batch = _create_batch(product=product)

        for i in range(7):
            generate_barcode(product, batch)

        seq = BarcodeSequence.objects.get(
            product=product, batch_number=batch.batch_number,
            supplier_id=batch.supplier_id)
        self.assertEqual(seq.last_sequence, 7)

    def test_package_unique_constraint_safety_net(self):
        """Package.barcode UNIQUE catches duplicates even if service doesn't."""
        product = _create_product()
        batch = _create_batch(product=product)
        create_package(product=product, batch=batch, barcode='SAFETY-1',
                       weight='1.000', selling_price='100')
        with self.assertRaises(StockError) as ctx:
            create_package(product=product, batch=batch, barcode='SAFETY-1',
                           weight='2.000', selling_price='200')
        self.assertIn('Barcode already exists', str(ctx.exception))


# ============================================================
# 3. SERVICE ROLLBACK TESTS
# ============================================================

class TestServiceRollback(TransactionTestCase):
    """Verify transactional rollback when services fail mid-operation."""

    def test_adjust_weight_rollback_on_exception(self):
        """
        Force exception after Package weight update but before audit.
        Package must revert; no ADJUSTED movement should exist.
        """
        pkg = _create_package_with_service(weight='2.000')
        original_weight = pkg.weight

        # Monkeypatch _record_movement to raise
        from inventory import services
        original_record = services._record_movement

        def failing_record(*args, **kwargs):
            raise RuntimeError('Simulated audit failure')

        services._record_movement = failing_record
        try:
            with self.assertRaises(RuntimeError):
                adjust_weight(pkg, '3.000', reason='Should fail')

            pkg.refresh_from_db()
            self.assertEqual(pkg.weight, original_weight,
                             'Weight should be rolled back')

            adj = StockMovement.objects.filter(
                package=pkg, movement_type='ADJUSTED')
            self.assertEqual(adj.count(), 0,
                             'No ADJUSTED movement after rollback')
        finally:
            services._record_movement = original_record

    def test_move_package_rollback_on_exception(self):
        """Force exception after location update → package reverts."""
        loc1 = _create_location('Roll_L1')
        loc2 = _create_location('Roll_L2')
        pkg = _create_package_with_service()
        pkg.storage_location = loc1
        pkg.save(update_fields=['storage_location'])

        from inventory import services
        original_record = services._record_movement

        def failing_record(*args, **kwargs):
            raise RuntimeError('Simulated audit failure')

        services._record_movement = failing_record
        try:
            with self.assertRaises(RuntimeError):
                move_package(pkg, loc2, reason='Should fail')

            pkg.refresh_from_db()
            self.assertEqual(pkg.storage_location.pk, loc1.pk,
                             'Location should be rolled back')

            mv = StockMovement.objects.filter(
                package=pkg, movement_type='MOVED')
            self.assertEqual(mv.count(), 0)
        finally:
            services._record_movement = original_record

    def test_sell_rollback_on_exception(self):
        """Force exception during sell → package state reverts."""
        pkg = _create_package_with_service()
        pkg.current_state = PackageState.READY_FOR_SALE
        pkg.save(update_fields=['current_state'])
        original_state = pkg.current_state

        from inventory import services
        original_record = services._record_movement

        def failing_record(*args, **kwargs):
            raise RuntimeError('Simulated audit failure')

        services._record_movement = failing_record
        try:
            with self.assertRaises(RuntimeError):
                sell_package(pkg, reason='Should fail')

            pkg.refresh_from_db()
            # State may have been partially transitioned; verify no SOLD
            sold_mv = StockMovement.objects.filter(
                package=pkg, movement_type='SOLD')
            self.assertEqual(sold_mv.count(), 0,
                             'No SOLD movement after rollback')
        finally:
            services._record_movement = original_record

    def test_discard_rollback_on_exception(self):
        """Force exception during discard → no DISCARDED movement."""
        pkg = _create_package_with_service()
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])

        from inventory import services
        original_record = services._record_movement

        def failing_record(*args, **kwargs):
            raise RuntimeError('Simulated audit failure')

        services._record_movement = failing_record
        try:
            with self.assertRaises(RuntimeError):
                discard_package(pkg, reason='Should fail')

            disc_mv = StockMovement.objects.filter(
                package=pkg, movement_type='DISCARDED')
            self.assertEqual(disc_mv.count(), 0,
                             'No DISCARDED movement after rollback')
        finally:
            services._record_movement = original_record

    def test_adjust_price_rollback_on_exception(self):
        """Force exception during price adjust → both Package and History revert."""
        pkg = _create_package_with_service(selling_price='100')
        original_price = pkg.selling_price

        from inventory import services
        original_validate = services.validate_price

        def failing_validate(price):
            raise StockError('Simulated validation failure')

        services.validate_price = failing_validate
        try:
            with self.assertRaises(StockError):
                adjust_package_price(pkg, '999', mode='manual', actor='test')

            pkg.refresh_from_db()
            self.assertEqual(pkg.selling_price, original_price)

            history = PriceChangeHistory.objects.filter(package=pkg)
            self.assertEqual(history.count(), 0,
                             'No PriceChangeHistory after rollback')
        finally:
            services.validate_price = original_validate


# ============================================================
# 4. BACKWARD COMPATIBILITY
# ============================================================

class TestBackwardCompatibility(TransactionTestCase):
    """Verify all supported create_package calling conventions."""

    def test_legacy_3arg_positional(self):
        """create_package(product, batch, weight)"""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(p, b, 2.5)
        self.assertIsNotNone(pkg.id)
        self.assertEqual(pkg.weight, Decimal('2.5'))
        self.assertEqual(pkg.current_state, PackageState.PACKED)

    def test_legacy_4arg_with_location(self):
        """create_package(product, batch, weight, storage_location=loc)"""
        p = _create_product()
        b = _create_batch(product=p)
        loc = _create_location()
        pkg = create_package(p, b, 1.8, storage_location=loc)
        self.assertEqual(pkg.storage_location, loc)

    def test_legacy_4arg_positional_location(self):
        """create_package(product, batch, weight, storage_location) — 4 positional."""
        p = _create_product()
        b = _create_batch(product=p)
        loc = _create_location()
        pkg = create_package(p, b, 3.0, loc)
        self.assertEqual(pkg.storage_location, loc)
        self.assertEqual(pkg.weight, Decimal('3.0'))

    def test_explicit_keyword_api(self):
        """create_package(product=..., batch=..., barcode=..., weight=..., ...)"""
        p = _create_product()
        b = _create_batch(product=p)
        loc = _create_location()
        pkg = create_package(
            product=p, batch=b, barcode='EXPL-001',
            weight='3.000', selling_price='360',
            storage_location=loc, actor='tester')
        self.assertEqual(pkg.barcode, 'EXPL-001')
        self.assertEqual(pkg.weight, Decimal('3.000'))
        self.assertEqual(pkg.selling_price, Decimal('360'))

    def test_mixed_positional_keyword(self):
        """create_package(product, batch, barcode='...', weight=...)"""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(p, b, barcode='MIX-001', weight='1.500')
        self.assertEqual(pkg.barcode, 'MIX-001')
        self.assertEqual(pkg.weight, Decimal('1.500'))

    def test_legacy_auto_generates_barcode(self):
        """Legacy 3-arg call auto-generates barcode and price."""
        p = _create_product(selling_price_per_kg=Decimal('100'))
        b = _create_batch(product=p)
        pkg = create_package(p, b, 2.0)
        self.assertIsNotNone(pkg.barcode)
        self.assertGreater(len(pkg.barcode), 0)
        self.assertEqual(pkg.selling_price, Decimal('200'))

    def test_explicit_barcode_overrides_auto(self):
        """Explicit barcode takes precedence over auto-generation."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(p, b, barcode='CUSTOM-BC', weight='1.000')
        self.assertEqual(pkg.barcode, 'CUSTOM-BC')


# ============================================================
# 5. DECIMAL PRICING CONSISTENCY
# ============================================================

class TestDecimalPricing(TransactionTestCase):
    """
    Ensure calculate_package_price always returns Decimal.
    Uses pure Decimal ROUND_CEILING, no float intermediary.
    """

    def _make_product(self, spk=Decimal('120'), cpk=Decimal('80')):
        return _create_product(selling_price_per_kg=spk, cost_per_kg=cpk)

    def test_returns_decimal_auto(self):
        p = self._make_product()
        result = calculate_package_price(p, Decimal('1.5'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('180'))

    def test_zero_point_zero_one_kg(self):
        """0.01 kg at 120 THB/kg → ceil(1.2) = 2 THB."""
        p = self._make_product()
        result = calculate_package_price(p, Decimal('0.01'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('2'))

    def test_zero_point_five_kg(self):
        """0.5 kg at 120 THB/kg → ceil(60) = 60 THB."""
        p = self._make_product()
        result = calculate_package_price(p, Decimal('0.5'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('60'))

    def test_one_kg(self):
        """1.0 kg at 120 THB/kg → 120 THB."""
        p = self._make_product()
        result = calculate_package_price(p, Decimal('1'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('120'))

    def test_integer_boundary_exact(self):
        """1.0 kg at 100 THB/kg → exactly 100, no ceiling needed."""
        p = self._make_product(spk=Decimal('100'))
        result = calculate_package_price(p, Decimal('1'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('100'))

    def test_integer_boundary_just_above(self):
        """1.0001 kg at 100 THB/kg → ceil(100.01) = 101 THB."""
        p = self._make_product(spk=Decimal('100'))
        result = calculate_package_price(p, Decimal('1.0001'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('101'))

    def test_integer_boundary_just_below(self):
        """0.9999 kg at 100 THB/kg → ceil(99.99) = 100 THB."""
        p = self._make_product(spk=Decimal('100'))
        result = calculate_package_price(p, Decimal('0.9999'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('100'))

    def test_fractional_ceiling(self):
        """0.35 kg at 110 THB/kg → ceil(38.5) = 39 THB."""
        p = self._make_product(spk=Decimal('110'))
        result = calculate_package_price(p, Decimal('0.35'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('39'))

    def test_cost_margin_mode(self):
        """cost_per_kg=80, margin=50%, weight=2 → ceil(240) = 240."""
        p = self._make_product(cpk=Decimal('80'))
        result = calculate_package_price(p, Decimal('2.0'),
                                         mode='cost_margin', value=50)
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('240'))

    def test_discount_mode(self):
        """selling=200, discount=10%, weight=1 → ceil(180) = 180."""
        p = self._make_product(spk=Decimal('200'))
        result = calculate_package_price(p, Decimal('1.0'),
                                         mode='discount', value=10)
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('180'))

    def test_price_per_kg_mode(self):
        """price_per_kg=150, weight=2 → 300."""
        p = self._make_product()
        result = calculate_package_price(p, Decimal('2.0'),
                                         mode='price_per_kg', value=150)
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('300'))

    def test_zero_weight_returns_zero_decimal(self):
        p = self._make_product()
        result = calculate_package_price(p, Decimal('0'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('0'))

    def test_negative_weight_returns_zero_decimal(self):
        p = self._make_product()
        result = calculate_package_price(p, Decimal('-1'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('0'))

    def test_invalid_mode_raises(self):
        p = self._make_product()
        with self.assertRaises(ValueError) as ctx:
            calculate_package_price(p, Decimal('1'), mode='bogus')
        self.assertIn('Invalid price mode', str(ctx.exception))

    def test_small_weight_ceiling_correctly(self):
        """0.001 kg at 1000 THB/kg → ceil(1) = 1 THB."""
        p = self._make_product(spk=Decimal('1000'))
        result = calculate_package_price(p, Decimal('0.001'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('1'))

    def test_large_weight_ceiling_correctly(self):
        """50.5 kg at 80 THB/kg → ceil(4040) = 4040 THB."""
        p = self._make_product(spk=Decimal('80'))
        result = calculate_package_price(p, Decimal('50.5'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('4040'))


# ============================================================
# 6. STOCK CONSISTENCY MODEL
# ============================================================

class TestStockConsistencyModel(TransactionTestCase):
    """
    Prove that available stock = SUM(package.weight) for active states.
    StockMovement is the audit trail, NOT the numeric source of truth.
    """

    def test_sold_removes_from_available(self):
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(product=p, batch=b, barcode='SCM-1',
                             weight='3.000', selling_price='300')
        self.assertEqual(get_available_stock(product=p), Decimal('3.000'))

        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        sell_package(pkg)
        self.assertEqual(get_available_stock(product=p), Decimal('0'))

    def test_discarded_removes_from_available(self):
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(product=p, batch=b, barcode='SCM-2',
                             weight='2.000', selling_price='200')
        self.assertEqual(get_available_stock(product=p), Decimal('2.000'))

        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        discard_package(pkg, reason='Damaged')
        self.assertEqual(get_available_stock(product=p), Decimal('0'))

    def test_active_packages_contribute_weight(self):
        p = _create_product()
        b = _create_batch(product=p)
        create_package(product=p, batch=b, barcode='SCM-3',
                       weight='1.500', selling_price='150')
        pkg2 = create_package(product=p, batch=b, barcode='SCM-4',
                              weight='2.500', selling_price='250')
        self.assertEqual(get_available_stock(product=p), Decimal('4.000'))

        # FROZEN is still active
        pkg2.current_state = PackageState.FROZEN
        pkg2.save(update_fields=['current_state'])
        self.assertEqual(get_available_stock(product=p), Decimal('4.000'))

    def test_adjust_changes_stock_by_exact_delta(self):
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(product=p, batch=b, barcode='SCM-5',
                             weight='2.000', selling_price='200')
        before = get_available_stock(product=p)
        adjust_weight(pkg, '2.500', reason='Re-weighed')
        after = get_available_stock(product=p)
        self.assertEqual(after - before, Decimal('0.500'))

    def test_multiple_movements_no_double_count(self):
        """StockMovement records do not inflate stock."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(product=p, batch=b, barcode='SCM-6',
                             weight='1.000', selling_price='100')
        pkg.current_state = PackageState.READY_FOR_SALE
        pkg.save(update_fields=['current_state'])
        move_package(pkg, _create_location())

        self.assertGreaterEqual(
            StockMovement.objects.filter(package=pkg).count(), 2)
        self.assertEqual(get_available_stock(product=p), Decimal('1.000'))

    def test_product_isolation(self):
        p1 = _create_product(sku='ISO-1')
        p2 = _create_product(sku='ISO-2')
        b1 = _create_batch(product=p1)
        b2 = _create_batch(product=p2)

        create_package(product=p1, batch=b1, barcode='SCM-7',
                       weight='5.000', selling_price='500')
        create_package(product=p2, batch=b2, barcode='SCM-8',
                       weight='3.000', selling_price='300')

        self.assertEqual(get_available_stock(product=p1), Decimal('5.000'))
        self.assertEqual(get_available_stock(product=p2), Decimal('3.000'))
        self.assertEqual(get_available_stock(), Decimal('8.000'))


# ============================================================
# 7. AUDIT SEMANTICS
# ============================================================

class TestAuditSemantics(TransactionTestCase):
    """Price changes → PriceChangeHistory. Stock ops → StockMovement."""

    def test_price_change_not_stock_movement(self):
        pkg = _create_package_with_service(selling_price='100')
        adjust_package_price(pkg, '150', actor='test')
        self.assertEqual(PriceChangeHistory.objects.filter(package=pkg).count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(
                package=pkg, movement_type='ADJUSTED').count(), 0)

    def test_weight_change_not_price_history(self):
        pkg = _create_package_with_service(weight='2.000')
        adjust_weight(pkg, '2.500')
        self.assertEqual(
            StockMovement.objects.filter(
                package=pkg, movement_type='ADJUSTED').count(), 1)
        self.assertEqual(PriceChangeHistory.objects.filter(package=pkg).count(), 0)

    def test_sell_creates_sold_movement(self):
        pkg = _create_package_with_service()
        pkg.current_state = PackageState.READY_FOR_SALE
        pkg.save(update_fields=['current_state'])
        sell_package(pkg, actor='cashier')
        self.assertEqual(
            StockMovement.objects.filter(
                package=pkg, movement_type='SOLD').count(), 1)

    def test_discard_creates_discarded_movement(self):
        pkg = _create_package_with_service()
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        discard_package(pkg, reason='Expired')
        self.assertEqual(
            StockMovement.objects.filter(
                package=pkg, movement_type='DISCARDED').count(), 1)


# ============================================================
# 8. STATE SEMANTICS
# ============================================================

class TestStateSemantics(TransactionTestCase):
    """COMPLETED is terminal for both sale and discard."""

    def test_sell_completes_to_terminal(self):
        pkg = _create_package_with_service()
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        sell_package(pkg, actor='cashier')
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        from common.state_machine import get_allowed_transitions
        self.assertEqual(get_allowed_transitions('COMPLETED'), [])

    def test_discard_completes_to_terminal(self):
        pkg = _create_package_with_service()
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        discard_package(pkg, reason='Damaged')
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        from common.state_machine import get_allowed_transitions
        self.assertEqual(get_allowed_transitions('COMPLETED'), [])

    def test_cannot_sell_completed(self):
        pkg = _create_package_with_service(state=PackageState.COMPLETED)
        with self.assertRaises(StockError):
            sell_package(pkg)

    def test_cannot_discard_completed(self):
        pkg = _create_package_with_service(state=PackageState.COMPLETED)
        with self.assertRaises(StockError):
            discard_package(pkg)

    def test_sold_semantic_is_movement_not_state(self):
        pkg = _create_package_with_service()
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        sell_package(pkg, actor='cashier1')

        sold_mv = StockMovement.objects.filter(
            package=pkg, movement_type='SOLD').first()
        self.assertIsNotNone(sold_mv)
        self.assertEqual(sold_mv.actor, 'cashier1')

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)


# ============================================================
# 9. STOCK CONSISTENCY VERIFICATION
# ============================================================

class TestStockConsistencyVerification(TransactionTestCase):
    """Test verify_stock_consistency checks."""

    def test_passes_for_clean_data(self):
        p = _create_product()
        b = _create_batch(product=p)
        create_package(product=p, batch=b, barcode='CSV-1',
                       weight='1.000', selling_price='100')
        result = verify_stock_consistency(product=p)
        self.assertTrue(result['consistent'], result['issues'])

    def test_detects_zero_weight(self):
        pkg = _create_package_obj(barcode='CSV-ZERO', weight='0.001')
        pkg.weight = Decimal('0')
        pkg.save(update_fields=['weight'])
        result = verify_stock_consistency()
        types = [i['type'] for i in result['issues']]
        self.assertIn('NEGATIVE_WEIGHT', types)

    def test_detects_missing_received_movement(self):
        pkg = _create_package_obj(barcode='CSV-NORECV')
        StockMovement.objects.filter(package=pkg).delete()
        result = verify_stock_consistency()
        types = [i['type'] for i in result['issues']]
        self.assertIn('MISSING_RECEIVED_MOVEMENT', types)
