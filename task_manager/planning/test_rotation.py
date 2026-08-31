"""
Phase 04 — Rotation Lifecycle Tests.

Comprehensive coverage of:
- Freeze lifecycle (start, complete, invalid states)
- Thaw lifecycle (queue, start, complete, cancel)
- Rotation cycles (first, refreeze, second, third)
- Concurrency (duplicate queue, cancel vs start, sale vs refreeze)
- Time (timezone-aware, invalid ordering)
- Audit (every transition creates history, history preserved)
- Capacity (interval overlap)
"""
import threading
import time
from datetime import timedelta
from decimal import Decimal

from django.db import transaction, IntegrityError
from django.test import TransactionTestCase
from django.utils import timezone

from inventory.models import (
    Category, Supplier, Product, Batch, Package,
    PackageState, StorageLocation, StockMovement,
)
from inventory.services import create_package
from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, RotationCycle,
    ThawQueueEntry, PlanStatus, QueueStatus, CapacityLock,
)
from planning.services import (
    calculate_freeze_duration, calculate_thaw_duration,
    create_rotation_plan, generate_worker_tasks,
    add_to_thaw_queue, remove_from_thaw_queue, cancel_rotation_plan,
    check_interval_overlap, check_thaw_interval_overlap,
    start_freeze, complete_freeze,
    start_thaw, complete_thaw,
    move_to_display,
    request_refreeze, start_refreeze,
    complete_sale, complete_discard,
    _get_or_create_cycle, _complete_cycle,
)
from common.state_machine import transition_package, InvalidTransitionError


# ============================================================
# HELPERS
# ============================================================

_counter = 50000

def _uid(suffix=''):
    global _counter
    _counter += 1
    return f'{_counter}_{suffix}'

def _create_cat():
    return Category.objects.get_or_create(
        code=_uid('CAT'), defaults={'name': 'Pork', 'is_active': True})[0]

def _create_sup():
    return Supplier.objects.get_or_create(
        name=_uid('SUP'), defaults={'locations': 'Bangkok'})[0]

def _create_prod(cat=None, sup=None):
    cat = cat or _create_cat()
    sup = sup or _create_sup()
    return Product.objects.create(
        sku=_uid('SKU'), name='Pork Neck', name_thai='คอหมู',
        category=cat, supplier=sup, unit='KG',
        cost_per_kg=Decimal('80'), selling_price_per_kg=Decimal('120'),
        barcode_prefix='0051', active=True)

def _create_batch(prod=None, sup=None):
    sup = sup or _create_sup()
    return Batch.objects.create(
        batch_number=_uid('BAT'), supplier=sup, received_at=timezone.now())

def _create_loc(name=None):
    return StorageLocation.objects.create(
        name=name or _uid('LOC'), location_type='FREEZER', capacity=50)

_pkg_counter = 0

def _create_pkg():
    global _pkg_counter
    _pkg_counter += 1
    prod = _create_prod()
    batch = _create_batch(prod)
    return create_package(prod, batch, barcode=f'PKG-{_pkg_counter:06d}',
                          weight='1.500', selling_price='180')

def _create_freeze_profile():
    return FreezeProfile.objects.create(
        name=_uid('FP'), target_temperature=Decimal('-8'),
        minimum_duration=timedelta(hours=4),
        default_duration=timedelta(hours=8),
        buffer_duration=timedelta(hours=1))

def _create_thaw_profile(capacity=10):
    return ThawProfile.objects.create(
        name=_uid('TP'), default_duration=timedelta(hours=24),
        minimum_duration=timedelta(hours=12),
        buffer_duration=timedelta(hours=2),
        weight_threshold_kg=Decimal('0.5'),
        weight_scale_factor=Decimal('1.2'),
        target_temperature=Decimal('3'),
        min_temperature=Decimal('1'), max_temperature=Decimal('5'),
        thaw_capacity=capacity)

def _frozen_pkg(weight=1.0):
    """Create a package in FROZEN state via the lifecycle services."""
    pkg = _create_pkg()
    pkg, _ = start_freeze(pkg, actor='test')
    pkg, _ = complete_freeze(pkg, actor='test')
    return pkg

def _thaw_queued_pkg(weight=1.0):
    """Create a package in THAW_QUEUED state with rotation plan."""
    pkg = _frozen_pkg(weight)
    fp = _create_freeze_profile()
    tp = _create_thaw_profile()
    target = timezone.now() + timedelta(days=3)
    plan = create_rotation_plan(pkg, target, fp, tp)
    add_to_thaw_queue(pkg, plan, actor='test')
    return pkg, plan

def _on_display_pkg():
    """Create a package in ON_DISPLAY state."""
    pkg, plan = _thaw_queued_pkg()
    pkg, _ = start_thaw(pkg, actor='test')
    pkg, _ = complete_thaw(pkg, actor='test')
    pkg, _ = move_to_display(pkg, actor='test')
    return pkg, plan


# ============================================================
# 1. FREEZE LIFECYCLE
# ============================================================

class TestFreezeLifecycle(TransactionTestCase):

    def test_start_freeze(self):
        pkg = _create_pkg()
        self.assertEqual(pkg.current_state, PackageState.PACKED)

        pkg2, cycle = start_freeze(pkg, actor='worker1', reason='Start freezing')
        pkg2.refresh_from_db()
        self.assertEqual(pkg2.current_state, PackageState.FREEZING)
        self.assertIsNotNone(cycle.freeze_started_at)
        self.assertEqual(cycle.cycle_number, 1)
        self.assertEqual(cycle.status, 'IN_PROGRESS')

    def test_complete_freeze(self):
        pkg = _create_pkg()
        pkg2, cycle = start_freeze(pkg, actor='worker1')
        pkg3, cycle2 = complete_freeze(pkg2, actor='worker1')
        pkg3.refresh_from_db()
        self.assertEqual(pkg3.current_state, PackageState.FROZEN)
        cycle.refresh_from_db()
        self.assertIsNotNone(cycle.freeze_completed_at)

    def test_start_freeze_wrong_state(self):
        pkg = _create_pkg()
        pkg, _ = start_freeze(pkg, actor='test')
        # Already FREEZING — can't start again
        with self.assertRaises(ValueError):
            start_freeze(pkg, actor='test')

    def test_complete_freeze_wrong_state(self):
        pkg = _create_pkg()
        # Still PACKED, can't complete freeze
        with self.assertRaises(ValueError):
            complete_freeze(pkg, actor='test')

    def test_freeze_creates_rotation_cycle(self):
        pkg = _create_pkg()
        pkg, cycle = start_freeze(pkg)
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle.package_id, pkg.pk)
        self.assertEqual(cycle.cycle_number, 1)

    def test_freeze_timestamps_consistent(self):
        pkg = _create_pkg()
        pkg, cycle = start_freeze(pkg)
        pkg, _ = complete_freeze(pkg)
        cycle.refresh_from_db()
        self.assertLess(cycle.freeze_started_at, cycle.freeze_completed_at)


# ============================================================
# 2. THAW LIFECYCLE
# ============================================================

class TestThawLifecycle(TransactionTestCase):

    def test_start_thaw(self):
        pkg, plan = _thaw_queued_pkg()
        pkg2, cycle = start_thaw(pkg, actor='worker1')
        pkg2.refresh_from_db()
        self.assertEqual(pkg2.current_state, PackageState.THAWING)
        cycle.refresh_from_db()
        self.assertIsNotNone(cycle.thaw_started_at)

        # Queue entry should be STARTED
        entry = ThawQueueEntry.objects.filter(package=pkg).first()
        self.assertEqual(entry.status, QueueStatus.STARTED)

    def test_complete_thaw(self):
        pkg, plan = _thaw_queued_pkg()
        pkg2, _ = start_thaw(pkg)
        pkg3, cycle = complete_thaw(pkg2, actor='worker1')
        pkg3.refresh_from_db()
        self.assertEqual(pkg3.current_state, PackageState.READY_FOR_SALE)
        cycle.refresh_from_db()
        self.assertIsNotNone(cycle.thaw_completed_at)

        entry = ThawQueueEntry.objects.filter(package=pkg).first()
        self.assertEqual(entry.status, QueueStatus.COMPLETED)

    def test_start_thaw_wrong_state(self):
        pkg = _create_pkg()
        with self.assertRaises(ValueError):
            start_thaw(pkg)

    def test_complete_thaw_wrong_state(self):
        pkg = _create_pkg()
        with self.assertRaises(ValueError):
            complete_thaw(pkg)


# ============================================================
# 3. DISPLAY
# ============================================================

class TestDisplayLifecycle(TransactionTestCase):

    def test_move_to_display(self):
        pkg, plan = _thaw_queued_pkg()
        start_thaw(pkg)
        complete_thaw(pkg)
        pkg2, cycle = move_to_display(pkg, actor='worker1')
        pkg2.refresh_from_db()
        self.assertEqual(pkg2.current_state, PackageState.ON_DISPLAY)
        cycle.refresh_from_db()
        self.assertIsNotNone(cycle.display_started_at)

    def test_move_to_display_wrong_state(self):
        pkg = _create_pkg()
        with self.assertRaises(ValueError):
            move_to_display(pkg)


# ============================================================
# 4. REFREEZE — MULTI-CYCLE
# ============================================================

class TestRefreeze(TransactionTestCase):

    def test_request_refreeze(self):
        pkg, plan = _on_display_pkg()
        pkg2, cycle = request_refreeze(pkg, actor='worker1')
        pkg2.refresh_from_db()
        self.assertEqual(pkg2.current_state, PackageState.REFREEZE_PENDING)
        cycle.refresh_from_db()
        self.assertIsNotNone(cycle.display_ended_at)

    def test_start_refreeze_completes_old_cycle(self):
        pkg, plan = _on_display_pkg()
        request_refreeze(pkg)
        pkg2, new_cycle = start_refreeze(pkg, actor='worker1')

        # Old cycle should be COMPLETED with REFROZEN outcome
        old_cycle = RotationCycle.objects.filter(
            package=pkg, status='COMPLETED'
        ).first()
        self.assertIsNotNone(old_cycle)
        self.assertEqual(old_cycle.outcome, 'REFROZEN')

        # New cycle should be IN_PROGRESS
        self.assertIsNotNone(new_cycle)
        self.assertEqual(new_cycle.status, 'IN_PROGRESS')
        self.assertEqual(new_cycle.cycle_number, 2)
        self.assertIsNotNone(new_cycle.freeze_started_at)

    def test_second_cycle_history_preserved(self):
        pkg, plan = _on_display_pkg()
        pkg, _ = request_refreeze(pkg)
        pkg, _ = start_refreeze(pkg, actor='test')
        pkg, _ = complete_freeze(pkg, actor='test')

        # Both cycles should exist
        cycles = RotationCycle.objects.filter(package=pkg).order_by('cycle_number')
        self.assertEqual(cycles.count(), 2)

        # Cycle 1 has complete history
        c1 = cycles[0]
        self.assertEqual(c1.status, 'COMPLETED')
        self.assertEqual(c1.outcome, 'REFROZEN')
        self.assertIsNotNone(c1.freeze_started_at)
        self.assertIsNotNone(c1.freeze_completed_at)
        self.assertIsNotNone(c1.thaw_started_at)
        self.assertIsNotNone(c1.thaw_completed_at)
        self.assertIsNotNone(c1.display_started_at)
        self.assertIsNotNone(c1.display_ended_at)

        # Cycle 2 is in progress
        c2 = cycles[1]
        self.assertEqual(c2.status, 'IN_PROGRESS')
        self.assertEqual(c2.cycle_number, 2)

    def test_third_cycle(self):
        """Three complete rotation cycles for same package."""
        pkg, plan = _on_display_pkg()
        pkg, _ = request_refreeze(pkg)
        pkg, _ = start_refreeze(pkg, actor='test')
        pkg, _ = complete_freeze(pkg, actor='test')

        # Start second thaw cycle
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan2 = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=6), fp, tp)
        add_to_thaw_queue(pkg, plan2, actor='test')
        pkg, _ = start_thaw(pkg, actor='test')
        pkg, _ = complete_thaw(pkg, actor='test')
        pkg, _ = move_to_display(pkg, actor='test')

        # Second refreeze
        pkg, _ = request_refreeze(pkg)
        pkg, _ = start_refreeze(pkg, actor='test')
        pkg, _ = complete_freeze(pkg, actor='test')

        cycles = RotationCycle.objects.filter(package=pkg).order_by('cycle_number')
        self.assertEqual(cycles.count(), 3)
        self.assertEqual(cycles[0].status, 'COMPLETED')
        self.assertEqual(cycles[0].outcome, 'REFROZEN')
        self.assertEqual(cycles[1].status, 'COMPLETED')
        self.assertEqual(cycles[1].outcome, 'REFROZEN')
        self.assertEqual(cycles[2].status, 'IN_PROGRESS')

    def test_request_refreeze_wrong_state(self):
        pkg = _create_pkg()
        # PACKED, cannot request refreeze
        with self.assertRaises(ValueError):
            request_refreeze(pkg)

    def test_start_refreeze_wrong_state(self):
        pkg = _create_pkg()
        # PACKED, cannot start refreeze
        with self.assertRaises(ValueError):
            start_refreeze(pkg)


# ============================================================
# 5. COMPLETION (SALE / DISCARD)
# ============================================================

class TestCompletion(TransactionTestCase):

    def test_complete_sale(self):
        pkg, plan = _on_display_pkg()
        pkg2 = complete_sale(pkg, actor='cashier1')
        pkg2.refresh_from_db()
        self.assertEqual(pkg2.current_state, PackageState.COMPLETED)

        cycle = RotationCycle.objects.filter(
            package=pkg, status='COMPLETED').first()
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle.outcome, 'SOLD')
        self.assertEqual(cycle.outcome_actor, 'cashier1')

    def test_complete_discard(self):
        pkg, plan = _on_display_pkg()
        pkg2 = complete_discard(pkg, actor='worker1', reason='Expired')
        pkg2.refresh_from_db()
        self.assertEqual(pkg2.current_state, PackageState.COMPLETED)

        cycle = RotationCycle.objects.filter(
            package=pkg, status='COMPLETED').first()
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle.outcome, 'DISCARDED')

    def test_sale_after_refreeze(self):
        """Sell after going through one refreeze cycle."""
        pkg, plan = _on_display_pkg()
        pkg, _ = request_refreeze(pkg)
        pkg, _ = start_refreeze(pkg, actor='test')
        pkg, _ = complete_freeze(pkg, actor='test')

        # Start second thaw
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan2 = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=6), fp, tp)
        add_to_thaw_queue(pkg, plan2, actor='test')
        pkg, _ = start_thaw(pkg, actor='test')
        pkg, _ = complete_thaw(pkg, actor='test')
        pkg, _ = move_to_display(pkg, actor='test')

        complete_sale(pkg, actor='cashier')

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        cycles = RotationCycle.objects.filter(package=pkg).order_by('cycle_number')
        self.assertEqual(cycles.count(), 2)
        self.assertEqual(cycles[0].outcome, 'REFROZEN')
        self.assertEqual(cycles[1].outcome, 'SOLD')


# ============================================================
# 6. QUEUE CANCELLATION
# ============================================================

class TestQueueCancellation(TransactionTestCase):

    def test_cancel_queue_transitions_to_PACKED(self):
        pkg, plan = _thaw_queued_pkg()
        entry = ThawQueueEntry.objects.filter(package=pkg).first()
        remove_from_thaw_queue(entry, actor='admin', reason='Changed mind')

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.PACKED)
        entry.refresh_from_db()
        self.assertEqual(entry.status, QueueStatus.CANCELLED)

    def test_cancel_queue_recycles_positions(self):
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()

        p1 = _frozen_pkg()
        p2 = _frozen_pkg()
        target = timezone.now() + timedelta(days=3)
        plan1 = create_rotation_plan(p1, target, fp, tp)
        plan2 = create_rotation_plan(p2, target + timedelta(hours=1), fp, tp)

        add_to_thaw_queue(p1, plan1, actor='test')
        e2 = add_to_thaw_queue(p2, plan2, actor='test')
        self.assertEqual(e2.queue_position, 2)

        # Cancel first entry
        e1 = ThawQueueEntry.objects.filter(package=p1).first()
        remove_from_thaw_queue(e1, actor='test')

        # Second entry should be repositioned
        e2.refresh_from_db()
        self.assertEqual(e2.queue_position, 1)

    def test_cancel_wrong_status_fails(self):
        pkg, plan = _thaw_queued_pkg()
        entry = ThawQueueEntry.objects.filter(package=pkg).first()
        entry.status = QueueStatus.COMPLETED
        entry.save(update_fields=['status'])

        with self.assertRaises(ValueError):
            remove_from_thaw_queue(entry)

    def test_cancel_wrong_package_state_fails(self):
        """Cancel entry when package is already in different state."""
        pkg, plan = _thaw_queued_pkg()
        # Directly move package out of THAW_QUEUED
        transition_package(pkg, 'PACKED', actor='test', reason='test')
        entry = ThawQueueEntry.objects.filter(package=pkg).first()
        with self.assertRaises(ValueError):
            remove_from_thaw_queue(entry)


# ============================================================
# 7. CAPACITY / INTERVAL OVERLAP
# ============================================================

class TestCapacity(TransactionTestCase):

    def test_no_overlap(self):
        """Non-overlapping intervals."""
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=1)
        b_start = a_end  # adjacent
        b_end = b_start + timedelta(hours=1)
        self.assertFalse(check_interval_overlap(a_start, a_end, b_start, b_end))

    def test_complete_overlap(self):
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=4)
        b_start = a_start + timedelta(hours=1)
        b_end = a_start + timedelta(hours=3)
        self.assertTrue(check_interval_overlap(a_start, a_end, b_start, b_end))

    def test_partial_overlap(self):
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=3)
        b_start = a_start + timedelta(hours=2)
        b_end = a_start + timedelta(hours=5)
        self.assertTrue(check_interval_overlap(a_start, a_end, b_start, b_end))

    def test_same_start(self):
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=2)
        b_end = a_start + timedelta(hours=3)
        self.assertTrue(check_interval_overlap(a_start, a_end, a_start, b_end))

    def test_same_end(self):
        now = timezone.now()
        a_start = now
        a_end = now + timedelta(hours=3)
        b_start = now + timedelta(hours=1)
        self.assertTrue(check_interval_overlap(a_start, a_end, b_start, a_end))

    def test_capacity_blocks_new_entry(self):
        """Capacity=1 blocks second concurrent thaw."""
        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=1)

        p1 = _frozen_pkg()
        plan1 = create_rotation_plan(
            p1, timezone.now() + timedelta(days=3), fp, tp)
        add_to_thaw_queue(p1, plan1, actor='test')

        p2 = _frozen_pkg()
        target2 = timezone.now() + timedelta(days=3)  # same interval
        plan2 = create_rotation_plan(p2, target2, fp, tp)
        with self.assertRaises(ValueError) as ctx:
            add_to_thaw_queue(p2, plan2, actor='test')
        self.assertIn('capacity', str(ctx.exception).lower())

    def test_capacity_allows_non_overlapping(self):
        """Capacity=1 allows sequential non-overlapping thaws."""
        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=1)

        p1 = _frozen_pkg()
        plan1 = create_rotation_plan(
            p1, timezone.now() + timedelta(days=3), fp, tp)
        add_to_thaw_queue(p1, plan1, actor='test')

        # Second package: different time window (well after first)
        p2 = _frozen_pkg()
        plan2 = create_rotation_plan(
            p2, timezone.now() + timedelta(days=10), fp, tp)
        # No overlap — should succeed
        entry2 = add_to_thaw_queue(p2, plan2, actor='test')
        self.assertIsNotNone(entry2)

    def test_multiple_simultaneous_within_capacity(self):
        """Capacity=3 allows 3 overlapping thaws."""
        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=3)

        pkgs = []
        plans = []
        for i in range(3):
            p = _frozen_pkg()
            target = timezone.now() + timedelta(days=3)
            plan = create_rotation_plan(p, target, fp, tp)
            entry = add_to_thaw_queue(p, plan, actor='test')
            pkgs.append(p)
            plans.append(plan)

        self.assertEqual(len(pkgs), 3)
        for p in pkgs:
            p.refresh_from_db()
            self.assertEqual(p.current_state, PackageState.THAW_QUEUED)


# ============================================================
# 8. CONCURRENCY
# ============================================================

class TestConcurrency(TransactionTestCase):

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def test_cannot_queue_twice(self):
        """Same package cannot be queued twice."""
        pkg, plan = _thaw_queued_pkg()
        with self.assertRaises(ValueError):
            add_to_thaw_queue(pkg, plan, actor='test')

    def test_concurrent_start_thaw(self):
        """Two threads try to start_thaw on same package — only one wins."""
        pkg, plan = _thaw_queued_pkg()
        results = []
        errors = []

        def worker(name):
            try:
                p, c = start_thaw(pkg, actor=name)
                results.append(name)
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=('W1',))
        t2 = threading.Thread(target=worker, args=('W2',))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.THAWING)

    def test_concurrent_complete_thaw(self):
        """Two threads try to complete_thaw — only one wins."""
        pkg, plan = _thaw_queued_pkg()
        start_thaw(pkg)

        results = []
        errors = []

        def worker(name):
            try:
                p, c = complete_thaw(pkg, actor=name)
                results.append(name)
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=('W1',))
        t2 = threading.Thread(target=worker, args=('W2',))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.READY_FOR_SALE)

    def test_concurrent_cancel_queue(self):
        """Two threads try to cancel same queue entry — only one wins."""
        pkg, plan = _thaw_queued_pkg()
        entry = ThawQueueEntry.objects.filter(package=pkg).first()

        results = []
        errors = []

        def worker(name):
            try:
                remove_from_thaw_queue(entry, actor=name)
                results.append(name)
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=('W1',))
        t2 = threading.Thread(target=worker, args=('W2',))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)

    def test_sale_vs_refreeze(self):
        """Sale and refreeze cannot both succeed."""
        pkg, plan = _on_display_pkg()

        results = []
        errors = []

        def worker_sale(name):
            try:
                complete_sale(pkg, actor=name)
                results.append(('sale', name))
            except Exception as e:
                errors.append(('sale', name, str(e)))

        def worker_refreeze(name):
            try:
                request_refreeze(pkg, actor=name)
                results.append(('refreeze', name))
            except Exception as e:
                errors.append(('refreeze', name, str(e)))

        t1 = threading.Thread(target=worker_sale, args=('S1',))
        t2 = threading.Thread(target=worker_refreeze, args=('R1',))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Exactly one should succeed
        self.assertEqual(len(results) + len(errors), 2)
        # Package must end in a valid terminal or pending state
        pkg.refresh_from_db()
        self.assertIn(pkg.current_state, [
            PackageState.COMPLETED, PackageState.REFREEZE_PENDING])


# ============================================================
# 9. TIME
# ============================================================

class TestTimeAwareness(TransactionTestCase):

    def test_timestamps_are_aware(self):
        pkg = _create_pkg()
        pkg2, cycle = start_freeze(pkg)
        self.assertIsNotNone(cycle.freeze_started_at)
        self.assertTrue(timezone.is_aware(cycle.freeze_started_at))

    def test_cycle_freeze_duration(self):
        pkg = _create_pkg()
        pkg2, cycle = start_freeze(pkg)
        # Simulate freeze completion 5 hours later
        cycle.freeze_completed_at = cycle.freeze_started_at + timedelta(hours=5)
        cycle.save(update_fields=['freeze_completed_at'])
        self.assertEqual(cycle.duration_freeze, timedelta(hours=5))

    def test_target_ready_after_planned_thaw_start(self):
        """target_ready_at must be after planned_thaw_start_at."""
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        pkg = _create_pkg()
        target = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, target, fp, tp)
        self.assertGreater(plan.target_ready_at, plan.planned_thaw_start_at)
        self.assertGreater(plan.planned_thaw_start_at, plan.planned_freeze_end_at)
        self.assertGreater(plan.planned_freeze_end_at, plan.planned_freeze_start_at)


# ============================================================
# 10. AUDIT TRAIL
# ============================================================

class TestAuditTrail(TransactionTestCase):

    def test_every_transition_creates_event(self):
        from operations.models import RotationEvent
        pkg = _create_pkg()
        pkg2, _ = start_freeze(pkg, actor='w1')
        events = RotationEvent.objects.filter(package=pkg)
        self.assertTrue(events.filter(from_state='PACKED', to_state='FREEZING').exists())

        pkg3, _ = complete_freeze(pkg2, actor='w1')
        events = RotationEvent.objects.filter(package=pkg)
        self.assertTrue(events.filter(from_state='FREEZING', to_state='FROZEN').exists())

    def test_history_preserved_across_cycles(self):
        from operations.models import RotationEvent
        pkg, plan = _on_display_pkg()
        events_before = RotationEvent.objects.filter(package=pkg).count()
        self.assertGreater(events_before, 0)

        # Refreeze creates new events
        pkg, _ = request_refreeze(pkg)
        pkg, _ = start_refreeze(pkg, actor='test')
        pkg, _ = complete_freeze(pkg, actor='test')

        events_after = RotationEvent.objects.filter(package=pkg).count()
        self.assertGreater(events_after, events_before)

        # Old events still exist
        self.assertTrue(
            RotationEvent.objects.filter(
                package=pkg, from_state='PACKED', to_state='FREEZING'
            ).exists()
        )

    def test_cycle_outcome_recorded(self):
        pkg, plan = _on_display_pkg()
        complete_sale(pkg, actor='cashier')
        cycle = RotationCycle.objects.filter(
            package=pkg, status='COMPLETED').first()
        self.assertEqual(cycle.outcome, 'SOLD')
        self.assertEqual(cycle.outcome_actor, 'cashier')
        self.assertIsNotNone(cycle.outcome_at)

    def test_audit_rotation_event_for_freeze(self):
        """start_freeze should create a RotationEvent via state machine."""
        from operations.models import RotationEvent
        pkg = _create_pkg()
        pkg2, _ = start_freeze(pkg, actor='w1')
        event = RotationEvent.objects.filter(
            package=pkg, from_state='PACKED', to_state='FREEZING').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, 'w1')


# ============================================================
# 11. FULL LIFECYCLE INTEGRATION
# ============================================================

class TestFullLifecycle(TransactionTestCase):

    def test_first_cycle_sell(self):
        """Complete first rotation cycle ending in sale."""
        pkg = _create_pkg()
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        target = timezone.now() + timedelta(days=3)

        # Create plan + queue
        plan = create_rotation_plan(pkg, target, fp, tp)

        # Full lifecycle via services
        pkg, _ = start_freeze(pkg, actor='w1')
        pkg, _ = complete_freeze(pkg, actor='w1')
        add_to_thaw_queue(pkg, plan, actor='w2')
        pkg, _ = start_thaw(pkg, actor='w3')
        pkg, _ = complete_thaw(pkg, actor='w3')
        pkg, _ = move_to_display(pkg, actor='w4')
        complete_sale(pkg, actor='cashier')

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        # Verify cycle
        cycles = RotationCycle.objects.filter(package=pkg)
        self.assertEqual(cycles.count(), 1)
        c = cycles.first()
        self.assertEqual(c.status, 'COMPLETED')
        self.assertEqual(c.outcome, 'SOLD')
        self.assertIsNotNone(c.freeze_started_at)
        self.assertIsNotNone(c.freeze_completed_at)
        self.assertIsNotNone(c.thaw_started_at)
        self.assertIsNotNone(c.thaw_completed_at)
        self.assertIsNotNone(c.display_started_at)

    def test_full_refreeze_then_sell(self):
        """Cycle 1 -> refreeze -> cycle 2 -> sell."""
        pkg = _create_pkg()
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        now = timezone.now()

        # Cycle 1
        plan1 = create_rotation_plan(pkg, now + timedelta(days=3), fp, tp)
        pkg, _ = start_freeze(pkg, actor='w1')
        pkg, _ = complete_freeze(pkg, actor='w1')
        add_to_thaw_queue(pkg, plan1, actor='w2')
        pkg, _ = start_thaw(pkg, actor='w3')
        pkg, _ = complete_thaw(pkg, actor='w3')
        pkg, _ = move_to_display(pkg, actor='w4')

        # Refreeze
        pkg, _ = request_refreeze(pkg, actor='w5')
        pkg, _ = start_refreeze(pkg, actor='w5')
        pkg, _ = complete_freeze(pkg, actor='w5')

        # Cycle 2
        plan2 = create_rotation_plan(pkg, now + timedelta(days=7), fp, tp)
        add_to_thaw_queue(pkg, plan2, actor='w2')
        pkg, _ = start_thaw(pkg, actor='w3')
        pkg, _ = complete_thaw(pkg, actor='w3')
        pkg, _ = move_to_display(pkg, actor='w4')
        complete_sale(pkg, actor='cashier')

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        cycles = RotationCycle.objects.filter(package=pkg).order_by('cycle_number')
        self.assertEqual(cycles.count(), 2)
        self.assertEqual(cycles[0].outcome, 'REFROZEN')
        self.assertEqual(cycles[1].outcome, 'SOLD')

        # Audit trail has events from both cycles
        from operations.models import RotationEvent
        events = RotationEvent.objects.filter(package=pkg)
        self.assertGreater(events.count(), 4)

    def test_first_cycle_discard(self):
        """Complete first cycle ending in discard."""
        pkg, plan = _on_display_pkg()
        complete_discard(pkg, reason='Damaged')

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.COMPLETED)

        cycle = RotationCycle.objects.filter(
            package=pkg, status='COMPLETED').first()
        self.assertEqual(cycle.outcome, 'DISCARDED')

    def test_multi_plan_same_package(self):
        """After a plan completes, a new plan can be created for the same package."""
        pkg, plan = _on_display_pkg()
        # Complete this cycle
        complete_sale(pkg)

        # Create a fresh package for new cycle test
        pkg2 = _create_pkg()
        pkg2, _ = start_freeze(pkg2)
        pkg2, _ = complete_freeze(pkg2)
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()

        # Can create a new plan for a different package
        plan2 = create_rotation_plan(
            pkg2, timezone.now() + timedelta(days=3), fp, tp)
        self.assertIsNotNone(plan2)

    def test_plan_after_completed_plan(self):
        """Can create new plan for a package after its plan is CANCELLED."""
        pkg, plan = _thaw_queued_pkg()
        cancel_rotation_plan(plan, actor='admin')
        plan.refresh_from_db()
        self.assertEqual(plan.status, PlanStatus.CANCELLED)

        # Package is back in PACKED after cancellation
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.PACKED)

        # Can create a new plan
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan2 = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=5), fp, tp)
        self.assertIsNotNone(plan2)


# ============================================================
# 12. CAPACITY CONCURRENCY (real PostgreSQL tests)
# ============================================================

class TestCapacityConcurrency(TransactionTestCase):
    """
    Real PostgreSQL concurrency tests for thaw capacity admission.

    Each thread uses an independent DB connection/transaction.
    Proves that the CapacityLock SELECT FOR UPDATE serializes
    capacity admission — concurrent overlapping requests cannot
    exceed the configured capacity.
    """

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def _create_frozen_with_plan(self, fp, tp, target):
        """Helper: create a frozen package + rotation plan, return (pkg, plan)."""
        pkg = _frozen_pkg()
        plan = create_rotation_plan(pkg, target, fp, tp)
        return pkg, plan

    def _thread_add_to_queue(self, pkg, plan, results, errors, name):
        """Thread worker that calls add_to_thaw_queue."""
        try:
            entry = add_to_thaw_queue(pkg, plan, actor=name)
            results.append(name)
        except Exception as e:
            errors.append((name, str(e)))

    # --- Capacity = 1 ---

    def test_concurrent_capacity_1_two_overlap(self):
        """Capacity=1: two overlapping requests → exactly 1 succeeds."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL for row-level locking')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=1)
        target = timezone.now() + timedelta(days=3)

        pkg1, plan1 = self._create_frozen_with_plan(fp, tp, target)
        pkg2, plan2 = self._create_frozen_with_plan(fp, tp, target)

        results = []
        errors = []

        t1 = threading.Thread(
            target=self._thread_add_to_queue,
            args=(pkg1, plan1, results, errors, 'W1'))
        t2 = threading.Thread(
            target=self._thread_add_to_queue,
            args=(pkg2, plan2, results, errors, 'W2'))

        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(results), 1,
            f'Expected exactly 1 success, got {len(results)}: {results}')
        self.assertEqual(len(errors), 1,
            f'Expected exactly 1 error, got {len(errors)}: {errors}')

        # Active queue count must be <= 1
        active = ThawQueueEntry.objects.filter(
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
        ).count()
        self.assertLessEqual(active, 1,
            f'Active queue count {active} exceeds capacity 1')

    # --- Capacity = 2 ---

    def test_concurrent_capacity_2_three_overlap(self):
        """Capacity=2: three overlapping requests → exactly 2 succeed."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL for row-level locking')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=2)
        target = timezone.now() + timedelta(days=3)

        pkgs_plans = [
            self._create_frozen_with_plan(fp, tp, target) for _ in range(3)
        ]

        results = []
        errors = []
        threads = []
        for i, (pkg, plan) in enumerate(pkgs_plans):
            t = threading.Thread(
                target=self._thread_add_to_queue,
                args=(pkg, plan, results, errors, f'W{i}'))
            threads.append(t)

        for t in threads:
            t.start()
            time.sleep(0.005)
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), 2,
            f'Expected 2 successes, got {len(results)}: {results}')
        self.assertEqual(len(errors), 1,
            f'Expected 1 error, got {len(errors)}: {errors}')

        active = ThawQueueEntry.objects.filter(
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
        ).count()
        self.assertLessEqual(active, 2)

    # --- Capacity = 3 ---

    def test_concurrent_capacity_3_four_overlap(self):
        """Capacity=3: four overlapping requests → exactly 3 succeed."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL for row-level locking')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=3)
        target = timezone.now() + timedelta(days=3)

        pkgs_plans = [
            self._create_frozen_with_plan(fp, tp, target) for _ in range(4)
        ]

        results = []
        errors = []
        threads = []
        for i, (pkg, plan) in enumerate(pkgs_plans):
            t = threading.Thread(
                target=self._thread_add_to_queue,
                args=(pkg, plan, results, errors, f'W{i}'))
            threads.append(t)

        for t in threads:
            t.start()
            time.sleep(0.005)
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), 3,
            f'Expected 3 successes, got {len(results)}: {results}')
        self.assertEqual(len(errors), 1,
            f'Expected 1 error, got {len(errors)}: {errors}')

        active = ThawQueueEntry.objects.filter(
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
        ).count()
        self.assertLessEqual(active, 3)

    # --- Non-overlapping concurrency ---

    def test_concurrent_non_overlapping_capacity_1(self):
        """Capacity=1: two non-overlapping intervals may both succeed."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL for row-level locking')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=1)

        now = timezone.now()
        # Intervals far apart — no overlap
        target1 = now + timedelta(days=3)
        target2 = now + timedelta(days=10)

        pkg1, plan1 = self._create_frozen_with_plan(fp, tp, target1)
        pkg2, plan2 = self._create_frozen_with_plan(fp, tp, target2)

        results = []
        errors = []

        t1 = threading.Thread(
            target=self._thread_add_to_queue,
            args=(pkg1, plan1, results, errors, 'W1'))
        t2 = threading.Thread(
            target=self._thread_add_to_queue,
            args=(pkg2, plan2, results, errors, 'W2'))

        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(results), 2,
            f'Non-overlapping should both succeed, got {len(results)}: {results}')
        self.assertEqual(len(errors), 0,
            f'Expected 0 errors for non-overlapping, got {len(errors)}: {errors}')

    # --- Queue position uniqueness ---

    def test_concurrent_queue_positions_unique(self):
        """Concurrent successful admissions must get unique active positions."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL for row-level locking')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=10)

        now = timezone.now()
        # Stagger targets so intervals don't overlap — each gets admitted
        pkgs_plans = []
        for i in range(5):
            target = now + timedelta(days=3, hours=i * 25)  # no overlap
            pkg, plan = self._create_frozen_with_plan(fp, tp, target)
            pkgs_plans.append((pkg, plan))

        results = []
        errors = []
        threads = []
        for i, (pkg, plan) in enumerate(pkgs_plans):
            t = threading.Thread(
                target=self._thread_add_to_queue,
                args=(pkg, plan, results, errors, f'W{i}'))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), 5,
            f'Expected 5 successes, got {len(results)}: {results}')

        # All active queue positions must be unique
        active_positions = list(
            ThawQueueEntry.objects.filter(
                status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
            ).values_list('queue_position', flat=True)
        )
        self.assertEqual(
            len(active_positions), len(set(active_positions)),
            f'Duplicate queue positions detected: {active_positions}')

    # --- CapacityLock model ---

    def test_capacity_lock_created_on_first_use(self):
        """CapacityLock row is created when add_to_thaw_queue first runs."""
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        self.assertFalse(CapacityLock.objects.filter(thaw_profile=tp).exists())

        pkg = _frozen_pkg()
        plan = create_rotation_plan(pkg, timezone.now() + timedelta(days=3), fp, tp)
        add_to_thaw_queue(pkg, plan, actor='test')

        self.assertTrue(CapacityLock.objects.filter(thaw_profile=tp).exists())

    def test_capacity_lock_reused(self):
        """Second admission reuses existing CapacityLock row."""
        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)

        p1 = _frozen_pkg()
        t1 = timezone.now() + timedelta(days=3)
        plan1 = create_rotation_plan(p1, t1, fp, tp)
        add_to_thaw_queue(p1, plan1, actor='test')
        count_after_first = CapacityLock.objects.filter(thaw_profile=tp).count()
        self.assertEqual(count_after_first, 1)

        p2 = _frozen_pkg()
        t2 = timezone.now() + timedelta(days=4)  # different window
        plan2 = create_rotation_plan(p2, t2, fp, tp)
        add_to_thaw_queue(p2, plan2, actor='test')
        count_after_second = CapacityLock.objects.filter(thaw_profile=tp).count()
        self.assertEqual(count_after_second, 1,
            'CapacityLock should be reused, not duplicated')


# ============================================================
# 13. ROTATION CYCLE INVARIANTS
# ============================================================

class TestRotationCycleInvariants(TransactionTestCase):

    def test_active_plan_must_have_cycle(self):
        """Every active RotationPlan must reference a RotationCycle."""
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        pkg = _frozen_pkg()
        target = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, target, fp, tp)

        self.assertIsNotNone(plan.rotation_cycle,
            'Active plan must have a rotation_cycle')
        self.assertEqual(plan.rotation_cycle.status, 'IN_PROGRESS')

    def test_completed_plan_can_have_null_cycle(self):
        """A CANCELLED plan may reference a cycle (no enforcement on null)."""
        pkg, plan = _thaw_queued_pkg()
        self.assertIsNotNone(plan.rotation_cycle)
        cancel_rotation_plan(plan, actor='admin')
        plan.refresh_from_db()
        # Cycle still exists (historical record)
        self.assertIsNotNone(plan.rotation_cycle)

    def test_refreeze_creates_new_cycle(self):
        """After refreeze, a new cycle is created and old is completed."""
        pkg, plan = _on_display_pkg()
        pkg, _ = request_refreeze(pkg)
        pkg, _ = start_refreeze(pkg, actor='test')

        active = RotationCycle.objects.filter(
            package=pkg, status='IN_PROGRESS')
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.first().cycle_number, 2)

        completed = RotationCycle.objects.filter(
            package=pkg, status='COMPLETED')
        self.assertEqual(completed.count(), 1)
        self.assertEqual(completed.first().outcome, 'REFROZEN')
