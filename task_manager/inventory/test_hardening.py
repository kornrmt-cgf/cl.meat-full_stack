"""
Phase 03 — Final Hardening Tests.

Tests cover:
1. Real PostgreSQL concurrency (select_for_update serialization)
2. Barcode sequence atomicity (race condition prevention)
3. Price adjustment atomicity (transaction + rollback)
4. Stock consistency model (authoritative weight-based calculation)
5. Backward compatibility (legacy create_package signatures)
6. Decimal pricing consistency (always Decimal, ceiling rounding)
7. Audit semantics (StockMovement vs PriceChangeHistory)
8. State semantics (COMPLETED terminal state for sell/discard)
"""
import threading
import time
from decimal import Decimal
from unittest import SkipTest

from django.db import transaction, IntegrityError, connection
from django.test import TransactionTestCase, skipUnlessDBFeature
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
    WeightError, StockError,
)


# ============================================================
# HELPERS
# ============================================================

_test_counter = 10000  # Start high to avoid collisions with test_services


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


def _create_package_obj(product=None, batch=None, barcode=None,
                        weight='1.500', state=PackageState.PACKED,
                        location=None, selling_price='150'):
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
# 1. REAL POSTGRESQL CONCURRENCY TESTS
# ============================================================

class TestPostgresConcurrency(TransactionTestCase):
    """
    Real PostgreSQL concurrency tests using independent transactions.

    These tests prove that select_for_update() serializes access.
    They only run against PostgreSQL (skipped on SQLite).
    """

    def _skip_if_not_pg(self):
        if not _is_postgres():
            raise SkipTest('Requires PostgreSQL for real concurrency tests')

    def test_concurrent_weight_adjustment_serialized(self):
        """
        Two threads try to adjust the same package weight concurrently.
        The second thread must wait for the first's transaction to commit,
        then see the updated value.
        """
        self._skip_if_not_pg()

        pkg = _create_package_obj(weight='1.000')
        pkg_id = pkg.pk
        errors = []
        results = []

        def adjust_in_thread(new_weight, thread_name):
            try:
                with transaction.atomic():
                    p = Package.objects.select_for_update().get(pk=pkg_id)
                    old_w = p.weight
                    time.sleep(0.05)  # Simulate work while holding lock
                    p.weight = Decimal(str(new_weight))
                    p.save(update_fields=['weight', 'updated_at'])
                    results.append((thread_name, old_w, p.weight))
            except Exception as e:
                errors.append((thread_name, str(e)))

        t1 = threading.Thread(target=adjust_in_thread, args=(2.000, 'T1'))
        t2 = threading.Thread(target=adjust_in_thread, args=(3.000, 'T2'))

        t1.start()
        time.sleep(0.01)  # Small offset so T1 grabs lock first
        t2.start()

        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertEqual(errors, [], f'Concurrency errors: {errors}')
        self.assertEqual(len(results), 2, 'Both threads should complete')

        pkg.refresh_from_db()
        # Both threads should have completed, last writer wins
        self.assertIn(pkg.weight, [Decimal('2.000'), Decimal('3.000')])
        # The intermediate value should have been visible to T2
        t1_result = [r for r in results if r[0] == 'T1'][0]
        t2_result = [r for r in results if r[0] == 'T2'][0]
        # T1's final weight should be 2.000
        self.assertEqual(t1_result[2], Decimal('2.000'))
        # T2 should see T1's committed value (2.000) as old, not 1.000
        self.assertEqual(t2_result[1], Decimal('2.000'))

    def test_concurrent_sell_two_threads(self):
        """
        Two threads try to sell the same package.
        Only one should succeed; the other should fail because
        the state has already changed to COMPLETED.
        """
        self._skip_if_not_pg()

        pkg = _create_package_obj(state=PackageState.READY_FOR_SALE)
        pkg_id = pkg.pk
        errors = []
        successes = []

        def sell_in_thread(thread_name):
            try:
                with transaction.atomic():
                    p = Package.objects.select_for_update().get(pk=pkg_id)
                    if p.current_state != PackageState.READY_FOR_SALE:
                        raise StockError(f'State changed: {p.current_state}')
                    # Simulate processing time
                    time.sleep(0.05)
                    p.current_state = PackageState.COMPLETED
                    p.save(update_fields=['current_state', 'updated_at'])
                    successes.append(thread_name)
            except Exception as e:
                errors.append((thread_name, str(e)))

        t1 = threading.Thread(target=sell_in_thread, args=('SELL_T1',))
        t2 = threading.Thread(target=sell_in_thread, args=('SELL_T2',))

        t1.start()
        time.sleep(0.01)
        t2.start()

        t1.join(timeout=5)
        t2.join(timeout=5)

        # Exactly one should succeed, one should see state changed
        self.assertEqual(len(successes), 1,
                         f'Only one sell should succeed, got: {successes}')
        self.assertEqual(len(errors), 1,
                         f'One thread should fail, got errors: {errors}')

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

    def test_concurrent_discard_prevents_double(self):
        """
        Two threads try to discard the same ON_DISPLAY package.
        Only one should succeed.
        """
        self._skip_if_not_pg()

        pkg = _create_package_obj(state=PackageState.ON_DISPLAY)
        pkg_id = pkg.pk
        errors = []
        successes = []

        def discard_in_thread(thread_name):
            try:
                with transaction.atomic():
                    p = Package.objects.select_for_update().get(pk=pkg_id)
                    if p.current_state != PackageState.ON_DISPLAY:
                        raise StockError(f'State changed: {p.current_state}')
                    time.sleep(0.05)
                    p.current_state = PackageState.COMPLETED
                    p.save(update_fields=['current_state', 'updated_at'])
                    successes.append(thread_name)
            except Exception as e:
                errors.append((thread_name, str(e)))

        t1 = threading.Thread(target=discard_in_thread, args=('DISC_T1',))
        t2 = threading.Thread(target=discard_in_thread, args=('DISC_T2',))

        t1.start()
        time.sleep(0.01)
        t2.start()

        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

    def test_concurrent_move_same_package(self):
        """
        Two threads try to move the same package to different locations.
        The second thread must see the first thread's committed location.
        """
        self._skip_if_not_pg()

        loc1 = _create_location('Loc_Conc_1')
        loc2 = _create_location('Loc_Conc_2')
        loc3 = _create_location('Loc_Conc_3')

        pkg = _create_package_obj(location=loc1)
        pkg_id = pkg.pk
        results = []
        errors = []

        def move_in_thread(target_loc, thread_name):
            try:
                with transaction.atomic():
                    p = Package.objects.select_for_update().get(pk=pkg_id)
                    old_loc = p.storage_location
                    time.sleep(0.05)
                    p.storage_location = target_loc
                    p.save(update_fields=['storage_location', 'updated_at'])
                    results.append((thread_name, old_loc_id(old_loc), target_loc.name))
            except Exception as e:
                errors.append((thread_name, str(e)))

        def old_loc_id(loc):
            return loc.pk if loc else None

        t1 = threading.Thread(target=move_in_thread, args=(loc2, 'MOV_T1'))
        t2 = threading.Thread(target=move_in_thread, args=(loc3, 'MOV_T2'))

        t1.start()
        time.sleep(0.01)
        t2.start()

        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertEqual(errors, [], f'Errors: {errors}')
        self.assertEqual(len(results), 2)

        pkg.refresh_from_db()
        self.assertIn(pkg.storage_location, [loc2, loc3])

    def test_concurrent_barcode_generation_no_duplicates(self):
        """
        Two threads generate barcodes for the same product/batch.
        They must get different sequence numbers.
        """
        self._skip_if_not_pg()

        product = _create_product()
        batch = _create_batch(product=product)
        barcodes = []
        errors = []

        def generate_in_thread(thread_name):
            try:
                with transaction.atomic():
                    bc = generate_barcode(product, batch)
                    barcodes.append((thread_name, bc))
            except Exception as e:
                errors.append((thread_name, str(e)))

        threads = []
        for i in range(5):
            t = threading.Thread(target=generate_in_thread, args=(f'T{i}',))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f'Barcode gen errors: {errors}')
        self.assertEqual(len(barcodes), 5, 'All 5 threads should generate barcodes')

        # All barcodes must be unique
        barcode_values = [bc[1] for bc in barcodes]
        self.assertEqual(len(barcode_values), len(set(barcode_values)),
                         f'Duplicate barcodes generated: {barcode_values}')


# ============================================================
# 2. BARCODE SEQUENCE ATOMICITY
# ============================================================

class TestBarcodeAtomicity(TransactionTestCase):
    """Test that barcode generation is atomic and race-condition-free."""

    def test_sequence_increment_is_monotonic(self):
        """Generate 10 barcodes sequentially — sequences must be monotonically increasing."""
        product = _create_product()
        batch = _create_batch(product=product)

        barcodes = []
        for _ in range(10):
            bc = generate_barcode(product, batch)
            barcodes.append(bc)

        # All must be unique
        self.assertEqual(len(barcodes), len(set(barcodes)),
                         f'Duplicate barcodes: {barcodes}')

        # Sequences extracted from barcodes must be monotonically increasing
        sequences = [int(bc[-4:]) for bc in barcodes]
        for i in range(1, len(sequences)):
            self.assertEqual(sequences[i], sequences[i - 1] + 1,
                             f'Sequence not monotonic: {sequences}')

    def test_different_products_independent_sequences(self):
        """Two different products have independent barcode sequences."""
        p1 = _create_product()
        p2 = _create_product()
        b1 = _create_batch(product=p1)
        b2 = _create_batch(product=p2)

        bc1 = generate_barcode(p1, b1)
        bc2 = generate_barcode(p1, b1)
        bc3 = generate_barcode(p2, b2)

        # p1 should have sequence 1, 2; p2 should have sequence 1
        self.assertNotEqual(bc1, bc2)
        self.assertEqual(int(bc1[-4:]), 1)
        self.assertEqual(int(bc2[-4:]), 2)
        self.assertEqual(int(bc3[-4:]), 1)

    def test_concurrent_barcode_no_lost_sequence(self):
        """
        Generate barcodes concurrently — no sequence should be lost,
        no duplicates. On SQLite, table locking limits concurrency;
        we assert uniqueness of those that succeed.
        Real concurrency testing is in TestPostgresConcurrency.
        """
        product = _create_product()
        batch = _create_batch(product=product)
        barcodes = []
        errors = []
        lock = threading.Lock()

        def gen():
            try:
                bc = generate_barcode(product, batch)
                with lock:
                    barcodes.append(bc)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=gen) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All successful barcodes must be unique — zero duplicates
        self.assertEqual(len(barcodes), len(set(barcodes)),
                         f'Duplicate in concurrent gen: {barcodes}')
        # At least one should succeed on any backend
        self.assertGreaterEqual(len(barcodes), 1,
                                f'No barcodes generated at all: {errors}')

    def test_package_barcode_unique_constraint_safety_net(self):
        """Even if service layer fails, Package.barcode UNIQUE catches duplicates."""
        product = _create_product()
        batch = _create_batch(product=product)
        create_package(product=product, batch=batch, barcode='UNIQUE-SAFETY-1',
                       weight='1.000', selling_price='100')
        with self.assertRaises(StockError) as ctx:
            create_package(product=product, batch=batch, barcode='UNIQUE-SAFETY-1',
                           weight='2.000', selling_price='200')
        self.assertIn('Barcode already exists', str(ctx.exception))


# ============================================================
# 3. PRICE ADJUSTMENT ATOMICITY
# ============================================================

class TestPriceAdjustmentAtomicity(TransactionTestCase):
    """Test that price adjustment is atomic and rolls back on failure."""

    def test_successful_price_adjustment(self):
        """Adjust price → PriceChangeHistory created → package updated."""
        pkg = _create_package_obj(selling_price='100')
        old_price = pkg.selling_price

        adjust_package_price(pkg, '150', mode='manual', actor='test_user')

        pkg.refresh_from_db()
        self.assertEqual(pkg.selling_price, Decimal('150'))

        history = PriceChangeHistory.objects.filter(package=pkg)
        self.assertEqual(history.count(), 1)
        h = history.first()
        self.assertEqual(h.old_price, Decimal('100'))
        self.assertEqual(h.new_price, Decimal('150'))
        self.assertEqual(h.mode, 'manual')
        self.assertEqual(h.actor, 'test_user')

    def test_price_adjustment_invalid_price_rollback(self):
        """Invalid price → neither Package nor PriceChangeHistory changed."""
        pkg = _create_package_obj(selling_price='200')
        original_price = pkg.selling_price

        with self.assertRaises(StockError):
            adjust_package_price(pkg, '-50', mode='manual')

        pkg.refresh_from_db()
        self.assertEqual(pkg.selling_price, original_price)
        self.assertEqual(PriceChangeHistory.objects.filter(package=pkg).count(), 0)

    def test_concurrent_price_adjustment_last_writer_wins(self):
        """
        Two concurrent price adjustments — on PostgreSQL, select_for_update
        serializes access. On SQLite, table locking may cause errors.
        """
        if not _is_postgres():
            # SQLite: test sequential price adjustment instead
            pkg = _create_package_obj(selling_price='100')
            adjust_package_price(pkg, '150', mode='manual', actor='A')
            adjust_package_price(pkg, '200', mode='manual', actor='B')
            pkg.refresh_from_db()
            self.assertEqual(pkg.selling_price, Decimal('200'))
            history = PriceChangeHistory.objects.filter(package=pkg)
            self.assertEqual(history.count(), 2)
            return

        # PostgreSQL: real concurrent test
        pkg = _create_package_obj(selling_price='100')
        pkg_id = pkg.pk
        results = []
        errors = []

        def adjust_in_thread(new_price, thread_name):
            try:
                with transaction.atomic():
                    p = Package.objects.select_for_update().get(pk=pkg_id)
                    old = p.selling_price
                    time.sleep(0.05)  # Hold lock briefly
                    p.selling_price = Decimal(str(new_price))
                    p.save(update_fields=['selling_price', 'updated_at'])
                    PriceChangeHistory.objects.create(
                        package=p, old_price=old, new_price=Decimal(str(new_price)),
                        mode='manual', actor=thread_name)
                    results.append(thread_name)
            except Exception as e:
                errors.append((thread_name, str(e)))

        t1 = threading.Thread(target=adjust_in_thread, args=(150, 'PRICE_T1'))
        t2 = threading.Thread(target=adjust_in_thread, args=(200, 'PRICE_T2'))

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

    def test_price_history_preserves_old_price(self):
        """Multiple price changes — history preserves each old/new value."""
        pkg = _create_package_obj(selling_price='100')

        adjust_package_price(pkg, '120', mode='manual', actor='A')
        adjust_package_price(pkg, '150', mode='manual', actor='B')
        adjust_package_price(pkg, '130', mode='discount', actor='C',
                            value='13')

        history = PriceChangeHistory.objects.filter(
            package=pkg).order_by('created_at')
        self.assertEqual(history.count(), 3)

        h1, h2, h3 = history
        self.assertEqual(h1.old_price, Decimal('100'))
        self.assertEqual(h1.new_price, Decimal('120'))
        self.assertEqual(h2.old_price, Decimal('120'))
        self.assertEqual(h2.new_price, Decimal('150'))
        self.assertEqual(h3.old_price, Decimal('150'))
        self.assertEqual(h3.new_price, Decimal('130'))


# ============================================================
# 4. STOCK CONSISTENCY MODEL
# ============================================================

class TestStockConsistencyModel(TransactionTestCase):
    """
    Prove that available stock = SUM(package.weight) for active states.

    StockMovement is the audit trail, NOT the numeric source of truth.
    """

    def test_sold_package_removed_from_available_stock(self):
        """After selling, package no longer contributes to available stock."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(product=p, batch=b, barcode='SCS-1',
                             weight='3.000', selling_price='300')

        stock_before = get_available_stock(product=p)
        self.assertEqual(stock_before, Decimal('3.000'))

        # Sell: ON_DISPLAY → PROCESSING → COMPLETED
        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        sell_package(pkg, actor='test')

        stock_after = get_available_stock(product=p)
        self.assertEqual(stock_after, Decimal('0'))

    def test_discarded_package_removed_from_available_stock(self):
        """After discarding, package no longer contributes to available stock."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(product=p, batch=b, barcode='SCS-2',
                             weight='2.000', selling_price='200')

        self.assertEqual(get_available_stock(product=p), Decimal('2.000'))

        pkg.current_state = PackageState.ON_DISPLAY
        pkg.save(update_fields=['current_state'])
        discard_package(pkg, reason='Damaged')

        self.assertEqual(get_available_stock(product=p), Decimal('0'))

    def test_active_package_contributes_weight(self):
        """PACKED and FROZEN packages contribute weight to available stock."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg1 = create_package(product=p, batch=b, barcode='SCS-3',
                              weight='1.500', selling_price='150')
        pkg2 = create_package(product=p, batch=b, barcode='SCS-4',
                              weight='2.500', selling_price='250')

        # Both PACKED → both in available stock
        self.assertEqual(get_available_stock(product=p), Decimal('4.000'))

        # Move pkg2 to FROZEN (still active)
        pkg2.current_state = PackageState.FROZEN
        pkg2.save(update_fields=['current_state'])

        self.assertEqual(get_available_stock(product=p), Decimal('4.000'))

    def test_adjusted_weight_changes_stock_exactly_once(self):
        """Weight adjustment changes available stock by exactly the delta."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(product=p, batch=b, barcode='SCS-5',
                             weight='2.000', selling_price='200')

        stock_before = get_available_stock(product=p)
        self.assertEqual(stock_before, Decimal('2.000'))

        adjust_weight(pkg, '2.500', reason='Re-weighed')

        stock_after = get_available_stock(product=p)
        self.assertEqual(stock_after, Decimal('2.500'))
        self.assertEqual(stock_after - stock_before, Decimal('0.500'))

    def test_repeated_movement_does_not_double_count(self):
        """Creating multiple StockMovement records does not inflate stock."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(product=p, batch=b, barcode='SCS-6',
                             weight='1.000', selling_price='100')

        # Move to create additional audit record
        pkg.current_state = PackageState.READY_FOR_SALE
        pkg.save(update_fields=['current_state'])
        move_package(pkg, _create_location(), actor='test')

        # Stock is still based on package.weight, not movement count
        self.assertEqual(get_available_stock(product=p), Decimal('1.000'))

        # Verify two movements exist but stock is unchanged
        movements = StockMovement.objects.filter(package=pkg)
        self.assertGreaterEqual(movements.count(), 2)
        self.assertEqual(get_available_stock(product=p), Decimal('1.000'))

    def test_multiple_products_stock_isolation(self):
        """Stock is correctly isolated by product."""
        p1 = _create_product(sku='ISO-1')
        p2 = _create_product(sku='ISO-2')
        b1 = _create_batch(product=p1)
        b2 = _create_batch(product=p2)

        create_package(product=p1, batch=b1, barcode='SCS-7',
                       weight='5.000', selling_price='500')
        create_package(product=p2, batch=b2, barcode='SCS-8',
                       weight='3.000', selling_price='300')

        self.assertEqual(get_available_stock(product=p1), Decimal('5.000'))
        self.assertEqual(get_available_stock(product=p2), Decimal('3.000'))
        self.assertEqual(get_available_stock(), Decimal('8.000'))


# ============================================================
# 5. BACKWARD COMPATIBILITY
# ============================================================

class TestBackwardCompatibility(TransactionTestCase):
    """Verify legacy calling conventions still work."""

    def test_legacy_3arg_create_package(self):
        """create_package(product, batch, weight) — old positional API."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(p, b, 2.5)
        self.assertIsNotNone(pkg.id)
        self.assertEqual(pkg.weight, Decimal('2.5'))
        self.assertEqual(pkg.current_state, PackageState.PACKED)

    def test_legacy_3arg_with_location(self):
        """create_package(product, batch, weight, storage_location=loc)."""
        p = _create_product()
        b = _create_batch(product=p)
        loc = _create_location()
        pkg = create_package(p, b, 1.8, storage_location=loc)
        self.assertEqual(pkg.storage_location, loc)

    def test_explicit_keyword_api(self):
        """create_package(product=..., batch=..., barcode=..., weight=..., ...)."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(
            product=p, batch=b, barcode='EXPL-001',
            weight='3.000', selling_price='360',
            storage_location=_create_location(),
            actor='test_user')
        self.assertEqual(pkg.barcode, 'EXPL-001')
        self.assertEqual(pkg.weight, Decimal('3.000'))

    def test_mixed_positional_keyword(self):
        """create_package(product, batch, barcode='...', weight=...)."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(p, b, barcode='MIX-001', weight='1.500')
        self.assertEqual(pkg.barcode, 'MIX-001')

    def test_legacy_3arg_auto_generates_barcode(self):
        """Legacy call without barcode → auto-generates one."""
        p = _create_product()
        b = _create_batch(product=p)
        pkg = create_package(p, b, 2.0)
        self.assertIsNotNone(pkg.barcode)
        self.assertGreater(len(pkg.barcode), 0)

    def test_legacy_3arg_calculates_price(self):
        """Legacy call without price → auto-calculates from product price/kg."""
        p = _create_product(selling_price_per_kg=Decimal('120'))
        b = _create_batch(product=p)
        pkg = create_package(p, b, 2.0)
        # price = ceil(120 * 2.0) = 240
        self.assertEqual(pkg.selling_price, Decimal('240'))


# ============================================================
# 6. DECIMAL PRICING CONSISTENCY
# ============================================================

class TestDecimalPricing(TransactionTestCase):
    """Ensure calculate_package_price always returns Decimal."""

    def _make_product(self, spk=Decimal('120'), cpk=Decimal('80')):
        return _create_product(selling_price_per_kg=spk, cost_per_kg=cpk)

    def test_returns_decimal_for_auto_mode(self):
        p = self._make_product(spk=Decimal('120'))
        result = calculate_package_price(p, Decimal('1.5'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('180'))

    def test_zero_point_zero_one_kg(self):
        """0.01 kg at 120 THB/kg → ceil(1.2) = 2 THB."""
        p = self._make_product(spk=Decimal('120'))
        result = calculate_package_price(p, Decimal('0.01'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('2'))

    def test_zero_point_five_kg(self):
        """0.5 kg at 120 THB/kg → ceil(60) = 60 THB."""
        p = self._make_product(spk=Decimal('120'))
        result = calculate_package_price(p, Decimal('0.5'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('60'))

    def test_one_kg(self):
        """1.0 kg at 120 THB/kg → 120 THB."""
        p = self._make_product(spk=Decimal('120'))
        result = calculate_package_price(p, Decimal('1'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('120'))

    def test_fractional_price_ceiling(self):
        """0.75 kg at 120 THB/kg → ceil(90) = 90 THB."""
        p = self._make_product(spk=Decimal('120'))
        result = calculate_package_price(p, Decimal('0.75'))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('90'))

    def test_fractional_price_ceiling_rounds_up(self):
        """0.35 kg at 120 THB/kg → ceil(42) = 42 THB.
        Actually ceil(42.0) = 42. Test ceiling with non-integer intermediate."""
        p = self._make_product(spk=Decimal('110'))
        result = calculate_package_price(p, Decimal('0.35'))
        self.assertIsInstance(result, Decimal)
        # 110 * 0.35 = 38.5 → ceil = 39
        self.assertEqual(result, Decimal('39'))

    def test_price_per_kg_mode(self):
        p = self._make_product()
        result = calculate_package_price(p, Decimal('2.0'),
                                         mode='price_per_kg', value=150)
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal('300'))

    def test_cost_margin_mode(self):
        p = self._make_product(cpk=Decimal('80'))
        result = calculate_package_price(p, Decimal('2.0'),
                                         mode='cost_margin', value=50)
        self.assertIsInstance(result, Decimal)
        # 80 * 1.5 * 2.0 = 240
        self.assertEqual(result, Decimal('240'))

    def test_discount_mode(self):
        p = self._make_product(spk=Decimal('200'))
        result = calculate_package_price(p, Decimal('1.0'),
                                         mode='discount', value=10)
        self.assertIsInstance(result, Decimal)
        # 200 * 0.9 = 180
        self.assertEqual(result, Decimal('180'))

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


# ============================================================
# 7. AUDIT SEMANTICS
# ============================================================

class TestAuditSemantics(TransactionTestCase):
    """
    Price changes go to PriceChangeHistory.
    Stock mutations go to StockMovement.
    They are NOT mixed.
    """

    def test_price_change_creates_price_history_not_movement(self):
        """adjust_package_price → PriceChangeHistory only, no StockMovement."""
        pkg = _create_package_obj(selling_price='100')

        adjust_package_price(pkg, '150', mode='manual', actor='test')

        self.assertEqual(PriceChangeHistory.objects.filter(package=pkg).count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(
                package=pkg, movement_type='ADJUSTED').count(), 0)

    def test_weight_adjustment_creates_movement_not_price_history(self):
        """adjust_weight → StockMovement ADJUSTED only, no PriceChangeHistory."""
        pkg = _create_package_obj(weight='2.000')

        adjust_weight(pkg, '2.500', reason='Scale calibration')

        movements = StockMovement.objects.filter(
            package=pkg, movement_type='ADJUSTED')
        self.assertEqual(movements.count(), 1)
        self.assertEqual(PriceChangeHistory.objects.filter(package=pkg).count(), 0)

    def test_sell_creates_sold_movement(self):
        """Selling creates SOLD StockMovement."""
        pkg = _create_package_obj(state=PackageState.READY_FOR_SALE)
        sell_package(pkg, actor='cashier')

        sold = StockMovement.objects.filter(
            package=pkg, movement_type='SOLD')
        self.assertEqual(sold.count(), 1)
        self.assertEqual(sold.first().actor, 'cashier')

    def test_discard_creates_discarded_movement(self):
        """Discarding creates DISCARDED StockMovement."""
        pkg = _create_package_obj(state=PackageState.ON_DISPLAY)
        discard_package(pkg, reason='Expired')

        discarded = StockMovement.objects.filter(
            package=pkg, movement_type='DISCARDED')
        self.assertEqual(discarded.count(), 1)
        self.assertEqual(discarded.first().reason, 'Expired')


# ============================================================
# 8. STATE SEMANTICS
# ============================================================

class TestStateSemantics(TransactionTestCase):
    """COMPLETED is terminal for both sale and discard."""

    def test_sell_completes_to_terminal(self):
        """Sale goes through PROCESSING → COMPLETED (terminal)."""
        pkg = _create_package_obj(state=PackageState.ON_DISPLAY)
        sell_package(pkg, actor='cashier')
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        # No further transitions allowed
        from common.state_machine import get_allowed_transitions
        self.assertEqual(get_allowed_transitions('COMPLETED'), [])

    def test_discard_completes_to_terminal(self):
        """Discard goes through DISCARDED → COMPLETED (terminal)."""
        pkg = _create_package_obj(state=PackageState.ON_DISPLAY)
        discard_package(pkg, reason='Damaged')
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        from common.state_machine import get_allowed_transitions
        self.assertEqual(get_allowed_transitions('COMPLETED'), [])

    def test_cannot_sell_completed_package(self):
        """Cannot sell a COMPLETED package."""
        pkg = _create_package_obj(state=PackageState.COMPLETED)
        with self.assertRaises(StockError):
            sell_package(pkg)

    def test_cannot_discard_completed_package(self):
        """Cannot discard a COMPLETED package."""
        pkg = _create_package_obj(state=PackageState.COMPLETED)
        with self.assertRaises(StockError):
            discard_package(pkg)

    def test_sell_audit_trail_shows_sold_movement(self):
        """
        The SOLD StockMovement is the semantic indicator of sale,
        not a dedicated SOLD state.
        """
        pkg = _create_package_obj(state=PackageState.ON_DISPLAY)
        sell_package(pkg, actor='cashier1')

        # Verify the movement type is 'SOLD'
        sold_mv = StockMovement.objects.filter(
            package=pkg, movement_type='SOLD').first()
        self.assertIsNotNone(sold_mv)
        self.assertEqual(sold_mv.actor, 'cashier1')

        # Verify state is COMPLETED, not SOLD
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)


# ============================================================
# 9. STOCK CONSISTENCY VERIFICATION
# ============================================================

class TestStockConsistencyVerification(TransactionTestCase):
    """Test verify_stock_consistency checks."""

    def test_consistency_passes_for_clean_data(self):
        p = _create_product()
        b = _create_batch(product=p)
        create_package(product=p, batch=b, barcode='CSV-1',
                       weight='1.000', selling_price='100')
        result = verify_stock_consistency(product=p)
        self.assertTrue(result['consistent'], result['issues'])

    def test_detects_zero_weight_package(self):
        """A package with weight=0 violates weight rules."""
        pkg = _create_package_obj(barcode='CSV-ZERO', weight='0.001')
        pkg.weight = Decimal('0')
        pkg.save(update_fields=['weight'])
        result = verify_stock_consistency()
        types = [i['type'] for i in result['issues']]
        self.assertIn('NEGATIVE_WEIGHT', types)

    def test_detects_missing_received_movement(self):
        """Package without RECEIVED movement is flagged."""
        pkg = _create_package_obj(barcode='CSV-NORECV')
        StockMovement.objects.filter(package=pkg).delete()
        result = verify_stock_consistency()
        types = [i['type'] for i in result['issues']]
        self.assertIn('MISSING_RECEIVED_MOVEMENT', types)
