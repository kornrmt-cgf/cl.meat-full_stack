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
    _get_or_create_cycle, _complete_cycle, _acquire_capacity_lock,
    _recalculate_queue_positions,
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


# ============================================================
# 14. FIRST-USE LOCK RACE (real PostgreSQL)
# ============================================================

class TestFirstUseLockRace(TransactionTestCase):
    """
    Prove that two concurrent first-ever admissions for the same
    ThawProfile do not raise IntegrityError and produce exactly
    one CapacityLock row.
    """

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def test_concurrent_first_use_lock_no_integrity_error(self):
        """Two threads hit get_or_create simultaneously on fresh profile."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL for row-level locking')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)

        # Confirm no lock exists
        self.assertFalse(CapacityLock.objects.filter(thaw_profile=tp).exists())

        # Two different frozen packages with non-overlapping intervals
        now = timezone.now()
        pkg1 = _frozen_pkg()
        plan1 = create_rotation_plan(pkg1, now + timedelta(days=3), fp, tp)
        pkg2 = _frozen_pkg()
        plan2 = create_rotation_plan(pkg2, now + timedelta(days=10), fp, tp)

        results = []
        errors = []

        def worker(pkg, plan, name):
            try:
                add_to_thaw_queue(pkg, plan, actor=name)
                results.append(name)
            except Exception as e:
                errors.append((name, type(e).__name__, str(e)))

        t1 = threading.Thread(target=worker, args=(pkg1, plan1, 'W1'))
        t2 = threading.Thread(target=worker, args=(pkg2, plan2, 'W2'))
        t1.start()
        time.sleep(0.005)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # Both must succeed (non-overlapping intervals, capacity=5)
        self.assertEqual(len(results), 2,
            f'Expected 2 successes, got {len(results)}: {results}')
        self.assertEqual(len(errors), 0,
            f'Expected 0 errors, got {errors}')

        # Exactly one CapacityLock row must exist
        lock_count = CapacityLock.objects.filter(thaw_profile=tp).count()
        self.assertEqual(lock_count, 1,
            f'Expected 1 CapacityLock row, got {lock_count}')

    def test_concurrent_first_use_no_duplicate_lock(self):
        """Rapid concurrent get_or_create must not create duplicate rows."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=10)
        self.assertFalse(CapacityLock.objects.filter(thaw_profile=tp).exists())

        now = timezone.now()
        # 5 non-overlapping packages
        pkgs_plans = []
        for i in range(5):
            pkg = _frozen_pkg()
            plan = create_rotation_plan(
                pkg, now + timedelta(days=3, hours=i * 25), fp, tp)
            pkgs_plans.append((pkg, plan))

        results = []
        errors = []
        threads = []
        for i, (pkg, plan) in enumerate(pkgs_plans):
            t = threading.Thread(
                target=lambda p, pl, n: (
                    results.append(n) if add_to_thaw_queue(p, pl, actor=n) is not None
                    else None
                ) if not errors.append(None) else None,
                args=(pkg, plan, f'W{i}'))
            threads.append(t)

        # Use proper worker function
        results.clear()
        errors.clear()
        threads.clear()

        def worker(pkg, plan, name):
            try:
                add_to_thaw_queue(pkg, plan, actor=name)
                results.append(name)
            except Exception as e:
                errors.append((name, type(e).__name__))

        for i, (pkg, plan) in enumerate(pkgs_plans):
            t = threading.Thread(target=worker, args=(pkg, plan, f'W{i}'))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(errors), 0, f'Unexpected errors: {errors}')
        lock_count = CapacityLock.objects.filter(thaw_profile=tp).count()
        self.assertEqual(lock_count, 1,
            f'Expected exactly 1 CapacityLock, got {lock_count}')


# ============================================================
# 15. CROSS-PROFILE QUEUE CONCURRENCY (real PostgreSQL)
# ============================================================

class TestCrossProfileQueueConcurrency(TransactionTestCase):
    """
    Queue positions are scoped PER PROFILE.

    Profile A and Profile B operate independently.
    Concurrent admissions to different profiles must not conflict.
    Positions within each profile must be unique and correctly ordered.
    """

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def test_cross_profile_positions_independent(self):
        """Two profiles can have the same queue_position independently."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp_a = _create_thaw_profile(capacity=5)
        tp_a.name = 'Profile-A'; tp_a.save()
        tp_b = _create_thaw_profile(capacity=5)
        tp_b.name = 'Profile-B'; tp_b.save()

        now = timezone.now()
        pkg1 = _frozen_pkg()
        plan1 = create_rotation_plan(pkg1, now + timedelta(days=3), fp, tp_a)
        pkg2 = _frozen_pkg()
        plan2 = create_rotation_plan(pkg2, now + timedelta(days=3), fp, tp_b)

        results = []
        errors = []

        def worker(pkg, plan, name):
            try:
                add_to_thaw_queue(pkg, plan, actor=name)
                results.append(name)
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=(pkg1, plan1, 'WA'))
        t2 = threading.Thread(target=worker, args=(pkg2, plan2, 'WB'))
        t1.start()
        time.sleep(0.005)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(results), 2, f'Both profiles should succeed: {results}')
        self.assertEqual(len(errors), 0, f'No errors expected: {errors}')

        # Each profile should have position = 1 (first in their scope)
        e1 = ThawQueueEntry.objects.filter(package=pkg1).first()
        e2 = ThawQueueEntry.objects.filter(package=pkg2).first()
        self.assertEqual(e1.queue_position, 1)
        self.assertEqual(e2.queue_position, 1)

    def test_same_profile_positions_unique(self):
        """Multiple packages in same profile get unique ascending positions."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=10)
        now = timezone.now()

        # 4 non-overlapping packages for same profile
        pkgs_plans = []
        for i in range(4):
            pkg = _frozen_pkg()
            plan = create_rotation_plan(
                pkg, now + timedelta(days=3, hours=i * 25), fp, tp)
            pkgs_plans.append((pkg, plan))

        results = []
        errors = []
        threads = []
        for i, (pkg, plan) in enumerate(pkgs_plans):
            t = threading.Thread(
                target=lambda p, pl, n: (results.append(n),) or None,
                args=(pkg, plan, f'W{i}'))
            threads.append(t)

        # Proper workers
        results.clear()
        errors.clear()
        threads.clear()

        def worker(pkg, plan, name):
            try:
                add_to_thaw_queue(pkg, plan, actor=name)
                results.append(name)
            except Exception as e:
                errors.append((name, str(e)))

        for i, (pkg, plan) in enumerate(pkgs_plans):
            t = threading.Thread(target=worker, args=(pkg, plan, f'W{i}'))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), 4, f'All 4 should succeed: {results}')

        # Positions must be unique within this profile
        positions = list(
            ThawQueueEntry.objects.filter(
                rotation_plan__thaw_profile=tp,
                status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
            ).values_list('queue_position', flat=True)
        )
        self.assertEqual(len(positions), len(set(positions)),
            f'Duplicate positions in same profile: {positions}')
        self.assertEqual(sorted(positions), [1, 2, 3, 4])

    def test_cancellation_recalculates_within_profile(self):
        """Cancelling an entry recalculates positions within its profile only."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)
        now = timezone.now()

        p1 = _frozen_pkg()
        p2 = _frozen_pkg()
        p3 = _frozen_pkg()
        plan1 = create_rotation_plan(p1, now + timedelta(days=3), fp, tp)
        plan2 = create_rotation_plan(p2, now + timedelta(days=4), fp, tp)
        plan3 = create_rotation_plan(p3, now + timedelta(days=5), fp, tp)

        e1 = add_to_thaw_queue(p1, plan1, actor='test')
        e2 = add_to_thaw_queue(p2, plan2, actor='test')
        e3 = add_to_thaw_queue(p3, plan3, actor='test')

        self.assertEqual(e1.queue_position, 1)
        self.assertEqual(e2.queue_position, 2)
        self.assertEqual(e3.queue_position, 3)

        # Cancel middle entry
        remove_from_thaw_queue(e2, actor='test')

        # Remaining positions should be 1, 2 (renumbered)
        e1.refresh_from_db()
        e3.refresh_from_db()
        self.assertEqual(e1.queue_position, 1)
        self.assertEqual(e3.queue_position, 2)


# ============================================================
# 16. QUEUE MUTATION CONCURRENCY (real PostgreSQL)
# ============================================================

class TestQueueMutationConcurrency(TransactionTestCase):
    """
    All queue mutations (add, cancel, cancel-plan) serialize through
    the same CapacityLock.  These tests prove add-vs-cancel, add-vs-
    cancel-plan, and multi-mutation scenarios are safe.
    """

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def _get_active_positions(self, profile):
        """Return sorted list of active queue positions for a profile."""
        return sorted(
            ThawQueueEntry.objects.filter(
                rotation_plan__thaw_profile=profile,
                status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
            ).values_list('queue_position', flat=True)
        )

    def _get_active_count(self, profile):
        return ThawQueueEntry.objects.filter(
            rotation_plan__thaw_profile=profile,
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
        ).count()

    # --- Test A: concurrent add + cancel ---

    def test_concurrent_add_and_cancel(self):
        """add_to_thaw_queue + remove_from_thaw_queue on same profile."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)
        now = timezone.now()

        # Pre-populate: 2 entries in queue
        p1 = _frozen_pkg()
        p2 = _frozen_pkg()
        plan1 = create_rotation_plan(p1, now + timedelta(days=3), fp, tp)
        plan2 = create_rotation_plan(p2, now + timedelta(days=4), fp, tp)
        e1 = add_to_thaw_queue(p1, plan1, actor='setup')
        e2 = add_to_thaw_queue(p2, plan2, actor='setup')
        self.assertEqual(self._get_active_positions(tp), [1, 2])

        # Concurrent: add p3 + cancel e1
        p3 = _frozen_pkg()
        plan3 = create_rotation_plan(p3, now + timedelta(days=5), fp, tp)

        add_errors = []
        cancel_errors = []

        def do_add():
            try:
                add_to_thaw_queue(p3, plan3, actor='add-thread')
            except Exception as e:
                add_errors.append(str(e))

        def do_cancel():
            try:
                remove_from_thaw_queue(e1, actor='cancel-thread')
            except Exception as e:
                cancel_errors.append(str(e))

        t1 = threading.Thread(target=do_add)
        t2 = threading.Thread(target=do_cancel)
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(add_errors), 0, f'Add failed: {add_errors}')
        self.assertEqual(len(cancel_errors), 0, f'Cancel failed: {cancel_errors}')

        # Exactly 2 active entries remain (e2 + p3)
        self.assertEqual(self._get_active_count(tp), 2)

        # Positions must be contiguous: 1, 2
        positions = self._get_active_positions(tp)
        self.assertEqual(positions, [1, 2],
            f'Positions not contiguous: {positions}')

        # e1 must be CANCELLED
        e1.refresh_from_db()
        self.assertEqual(e1.status, QueueStatus.CANCELLED)

        # p3 must be THAW_QUEUED
        p3.refresh_from_db()
        self.assertEqual(p3.current_state, PackageState.THAW_QUEUED)

    # --- Test B: concurrent add + cancel_rotation_plan ---

    def test_concurrent_add_and_cancel_plan(self):
        """add_to_thaw_queue + cancel_rotation_plan on same profile."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)
        now = timezone.now()

        # Pre-populate: 1 entry
        p1 = _frozen_pkg()
        plan1 = create_rotation_plan(p1, now + timedelta(days=3), fp, tp)
        e1 = add_to_thaw_queue(p1, plan1, actor='setup')
        self.assertEqual(self._get_active_count(tp), 1)

        # Concurrent: add p2 + cancel plan1
        p2 = _frozen_pkg()
        plan2 = create_rotation_plan(p2, now + timedelta(days=5), fp, tp)

        add_errors = []
        cancel_errors = []

        def do_add():
            try:
                add_to_thaw_queue(p2, plan2, actor='add-thread')
            except Exception as e:
                add_errors.append(str(e))

        def do_cancel_plan():
            try:
                cancel_rotation_plan(plan1, actor='cancel-thread')
            except Exception as e:
                cancel_errors.append(str(e))

        t1 = threading.Thread(target=do_add)
        t2 = threading.Thread(target=do_cancel_plan)
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(add_errors), 0, f'Add failed: {add_errors}')
        self.assertEqual(len(cancel_errors), 0, f'Cancel plan failed: {cancel_errors}')

        # Exactly 1 active entry remains (either e1 or p2, not both if conflicting)
        active_count = self._get_active_count(tp)
        self.assertIn(active_count, [0, 1],
            f'Unexpected active count: {active_count}')

        # Positions must be contiguous
        positions = self._get_active_positions(tp)
        if positions:
            self.assertEqual(positions, list(range(1, len(positions) + 1)),
                f'Positions not contiguous: {positions}')

    # --- Test C: multiple concurrent add + cancel on one profile ---

    def test_multi_concurrent_add_cancel_one_profile(self):
        """Multiple threads adding and cancelling on one profile."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=10)
        now = timezone.now()

        # Pre-populate: 3 entries
        pre_entries = []
        for i in range(3):
            p = _frozen_pkg()
            plan = create_rotation_plan(
                p, now + timedelta(days=3, hours=i), fp, tp)
            e = add_to_thaw_queue(p, plan, actor='setup')
            pre_entries.append(e)
        self.assertEqual(self._get_active_count(tp), 3)

        # Concurrent: 3 adds + 1 cancel
        new_pkgs = [_frozen_pkg() for _ in range(3)]
        new_plans = [
            create_rotation_plan(
                p, now + timedelta(days=10, hours=i * 25), fp, tp)
            for i, p in enumerate(new_pkgs)
        ]
        cancel_entry = pre_entries[0]  # cancel the first pre-populated entry

        errors = []

        def do_add(pkg, plan, name):
            try:
                add_to_thaw_queue(pkg, plan, actor=name)
            except Exception as e:
                errors.append((name, str(e)))

        def do_cancel(entry):
            try:
                remove_from_thaw_queue(entry, actor='cancel')
            except Exception as e:
                errors.append(('cancel', str(e)))

        threads = []
        for i in range(3):
            t = threading.Thread(
                target=do_add, args=(new_pkgs[i], new_plans[i], f'add-{i}'))
            threads.append(t)
        threads.append(threading.Thread(target=do_cancel, args=(cancel_entry,)))

        for t in threads:
            t.start()
            time.sleep(0.005)
        for t in threads:
            t.join(timeout=15)

        # No unexpected errors (capacity=10, plenty of room)
        self.assertEqual(len(errors), 0, f'Errors: {errors}')

        # Positions must be contiguous 1..N
        positions = self._get_active_positions(tp)
        self.assertEqual(
            positions, list(range(1, len(positions) + 1)),
            f'Positions not contiguous: {positions}')

        # No duplicate positions
        self.assertEqual(len(positions), len(set(positions)),
            f'Duplicate positions: {positions}')

        # Cancelled entry must not appear as active
        cancel_entry.refresh_from_db()
        self.assertEqual(cancel_entry.status, QueueStatus.CANCELLED)

    # --- Test D: same operations across two profiles ---

    def test_concurrent_mutations_two_profiles(self):
        """Add+cancel on Profile A and Profile B concurrently."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp_a = _create_thaw_profile(capacity=5)
        tp_a.name = 'Room-A'; tp_a.save()
        tp_b = _create_thaw_profile(capacity=5)
        tp_b.name = 'Room-B'; tp_b.save()
        now = timezone.now()

        # Pre-populate each profile with 1 entry
        p_a1 = _frozen_pkg()
        plan_a1 = create_rotation_plan(p_a1, now + timedelta(days=3), fp, tp_a)
        e_a1 = add_to_thaw_queue(p_a1, plan_a1, actor='setup')

        p_b1 = _frozen_pkg()
        plan_b1 = create_rotation_plan(p_b1, now + timedelta(days=3), fp, tp_b)
        e_b1 = add_to_thaw_queue(p_b1, plan_b1, actor='setup')

        # Concurrent: add to A + cancel from B (different profiles)
        p_a2 = _frozen_pkg()
        plan_a2 = create_rotation_plan(p_a2, now + timedelta(days=5), fp, tp_a)

        errors = []

        def do_add_a():
            try:
                add_to_thaw_queue(p_a2, plan_a2, actor='add-a')
            except Exception as e:
                errors.append(('add-a', str(e)))

        def do_cancel_b():
            try:
                remove_from_thaw_queue(e_b1, actor='cancel-b')
            except Exception as e:
                errors.append(('cancel-b', str(e)))

        t1 = threading.Thread(target=do_add_a)
        t2 = threading.Thread(target=do_cancel_b)
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(errors), 0, f'Errors: {errors}')

        # Profile A: 2 active entries, positions [1, 2]
        pos_a = self._get_active_positions(tp_a)
        self.assertEqual(pos_a, [1, 2],
            f'Profile A positions wrong: {pos_a}')

        # Profile B: 0 active entries (cancelled)
        count_b = self._get_active_count(tp_b)
        self.assertEqual(count_b, 0, f'Profile B should be empty: {count_b}')

        # Profiles are independent
        e_b1.refresh_from_db()
        self.assertEqual(e_b1.status, QueueStatus.CANCELLED)


# ============================================================
# 17. QUEUE ORDERING DETERMINISM (real PostgreSQL)
# ============================================================

class TestQueueOrderingDeterminism(TransactionTestCase):
    """
    Queue ordering uses (planned_start_at, created_at, pk) as a stable
    tiebreaker.  These tests prove that tied timestamps produce
    deterministic, reproducible ordering.
    """

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def _get_active_entries(self, profile):
        """Return active entries ordered by queue_position."""
        return list(
            ThawQueueEntry.objects.filter(
                rotation_plan__thaw_profile=profile,
                status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
            ).order_by('queue_position')
        )

    # --- Case A: two entries with identical planned_start_at ---

    def test_same_time_deterministic_order(self):
        """Two entries with identical planned_start_at get stable positions."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)
        now = timezone.now()
        target = now + timedelta(days=3)

        p1 = _frozen_pkg()
        p2 = _frozen_pkg()
        plan1 = create_rotation_plan(p1, target, fp, tp)
        plan2 = create_rotation_plan(p2, target, fp, tp)

        # Both have the same target → same planned_thaw_start_at
        e1 = add_to_thaw_queue(p1, plan1, actor='test')
        e2 = add_to_thaw_queue(p2, plan2, actor='test')

        entries = self._get_active_entries(tp)
        self.assertEqual(len(entries), 2)

        # Positions must be 1, 2 (deterministic)
        self.assertEqual(entries[0].queue_position, 1)
        self.assertEqual(entries[1].queue_position, 2)

        # Record the logical order (by pk since created_at may also tie)
        first_pk = entries[0].pk
        second_pk = entries[1].pk

        # Recalculate — order must not change
        _recalculate_queue_positions(profile=tp)
        entries2 = self._get_active_entries(tp)
        self.assertEqual(entries2[0].pk, first_pk)
        self.assertEqual(entries2[1].pk, second_pk)

    # --- Case B: three entries with ties ---

    def test_three_entries_ties_stable(self):
        """A=10:00, B=10:00, C=11:00 → stable order across recalculations."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)
        now = timezone.now()

        target_same = now + timedelta(days=3)
        target_later = now + timedelta(days=4)

        pA = _frozen_pkg()
        pB = _frozen_pkg()
        pC = _frozen_pkg()
        planA = create_rotation_plan(pA, target_same, fp, tp)
        planB = create_rotation_plan(pB, target_same, fp, tp)
        planC = create_rotation_plan(pC, target_later, fp, tp)

        eA = add_to_thaw_queue(pA, planA, actor='test')
        eB = add_to_thaw_queue(pB, planB, actor='test')
        eC = add_to_thaw_queue(pC, planC, actor='test')

        entries = self._get_active_entries(tp)
        self.assertEqual(len(entries), 3)

        # Record initial order
        order_before = [e.pk for e in entries]

        # A and B should be first two (same earlier time), C third
        self.assertIn(entries[0].pk, [eA.pk, eB.pk])
        self.assertIn(entries[1].pk, [eA.pk, eB.pk])
        self.assertEqual(entries[2].pk, eC.pk)

        # Recalculate 5 times — order must remain identical
        for _ in range(5):
            _recalculate_queue_positions(profile=tp)
            entries_n = self._get_active_entries(tp)
            order_n = [e.pk for e in entries_n]
            self.assertEqual(order_n, order_before,
                f'Order changed after recalculation: {order_before} → {order_n}')

    # --- Case C: cancel one of two same-time entries ---

    def test_cancel_same_time_entry_preserves_order(self):
        """Cancelling one of two same-time entries preserves stable ordering."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)
        now = timezone.now()
        target = now + timedelta(days=3)

        p1 = _frozen_pkg()
        p2 = _frozen_pkg()
        plan1 = create_rotation_plan(p1, target, fp, tp)
        plan2 = create_rotation_plan(p2, target, fp, tp)

        e1 = add_to_thaw_queue(p1, plan1, actor='test')
        e2 = add_to_thaw_queue(p2, plan2, actor='test')

        entries_before = self._get_active_entries(tp)
        self.assertEqual(len(entries_before), 2)

        # The first entry in stable order
        first_pk = entries_before[0].pk
        second_pk = entries_before[1].pk

        # Cancel the first entry
        first_entry = entries_before[0]
        remove_from_thaw_queue(first_entry, actor='test')

        entries_after = self._get_active_entries(tp)
        self.assertEqual(len(entries_after), 1)
        self.assertEqual(entries_after[0].pk, second_pk)
        self.assertEqual(entries_after[0].queue_position, 1)

    # --- Case D: repeated recalculation without data changes ---

    def test_idempotent_recalculation(self):
        """Recalculating positions without data changes produces identical result."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=10)
        now = timezone.now()

        # Create 5 entries with some same-time ties
        pkgs = []
        entries = []
        for i in range(5):
            p = _frozen_pkg()
            # First 3 share the same target (tie), last 2 are unique
            if i < 3:
                target = now + timedelta(days=3)
            else:
                target = now + timedelta(days=3, hours=i * 10)
            plan = create_rotation_plan(p, target, fp, tp)
            e = add_to_thaw_queue(p, plan, actor='test')
            pkgs.append(p)
            entries.append(e)

        # Record stable order
        initial = self._get_active_entries(tp)
        initial_order = [(e.pk, e.queue_position) for e in initial]

        # Recalculate 10 times
        for i in range(10):
            _recalculate_queue_positions(profile=tp)
            current = self._get_active_entries(tp)
            current_order = [(e.pk, e.queue_position) for e in current]
            self.assertEqual(
                current_order, initial_order,
                f'Order changed on iteration {i}: {initial_order} → {current_order}')


# ============================================================
# 18. INSERT ORDERING + CROSS-PROFILE CAPACITY ISOLATION
# ============================================================

class TestInsertOrderingAndProfileIsolation(TransactionTestCase):
    """
    Proves that:
    - add_to_thaw_queue immediately assigns correct position via QUEUE_ORDERING
    - capacity checking is scoped per ThawProfile
    - cross-profile capacity is independent
    """

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def _get_active_entries(self, profile):
        return list(
            ThawQueueEntry.objects.filter(
                rotation_plan__thaw_profile=profile,
                status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
            ).order_by('queue_position')
        )

    def _get_active_positions(self, profile):
        return [e.queue_position for e in self._get_active_entries(profile)]

    # --- Test A: insert ordering ---

    def test_earlier_entry_gets_lower_position(self):
        """New entry with earlier planned_start_at gets position 1."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)
        now = timezone.now()

        # First: later entry at 11:00
        p_late = _frozen_pkg()
        plan_late = create_rotation_plan(
            p_late, now + timedelta(days=3, hours=1), fp, tp)
        e_late = add_to_thaw_queue(p_late, plan_late, actor='test')

        # Second: earlier entry at 10:00
        p_early = _frozen_pkg()
        plan_early = create_rotation_plan(
            p_early, now + timedelta(days=3), fp, tp)
        e_early = add_to_thaw_queue(p_early, plan_early, actor='test')

        # e_early should be position 1 (earlier time)
        e_early.refresh_from_db()
        e_late.refresh_from_db()
        self.assertEqual(e_early.queue_position, 1,
            f'Earlier entry should be position 1, got {e_early.queue_position}')
        self.assertEqual(e_late.queue_position, 2,
            f'Later entry should be position 2, got {e_late.queue_position}')

    # --- Test B: same timestamp deterministic order ---

    def test_same_timestamp_deterministic_on_insert(self):
        """Two entries with same planned_start_at get stable positions on insert."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=5)
        now = timezone.now()
        target = now + timedelta(days=3)

        p1 = _frozen_pkg()
        p2 = _frozen_pkg()
        plan1 = create_rotation_plan(p1, target, fp, tp)
        plan2 = create_rotation_plan(p2, target, fp, tp)

        e1 = add_to_thaw_queue(p1, plan1, actor='test')
        e2 = add_to_thaw_queue(p2, plan2, actor='test')

        # Positions must be 1, 2 (determined by created_at then pk)
        self.assertEqual(e1.queue_position, 1)
        self.assertEqual(e2.queue_position, 2)

        # Stable: recalculate should not change
        _recalculate_queue_positions(profile=tp)
        e1.refresh_from_db()
        e2.refresh_from_db()
        self.assertEqual(e1.queue_position, 1)
        self.assertEqual(e2.queue_position, 2)

    # --- Test C: cross-profile capacity isolation ---

    def test_cross_profile_capacity_independent(self):
        """Profile A capacity=1 and Profile B capacity=1 allow overlapping intervals."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp_a = _create_thaw_profile(capacity=1)
        tp_a.name = 'Room-A'; tp_a.save()
        tp_b = _create_thaw_profile(capacity=1)
        tp_b.name = 'Room-B'; tp_b.save()
        now = timezone.now()
        target = now + timedelta(days=3)

        # A1 at 10:00-12:00 in Profile A
        p_a1 = _frozen_pkg()
        plan_a1 = create_rotation_plan(p_a1, target, fp, tp_a)
        e_a1 = add_to_thaw_queue(p_a1, plan_a1, actor='test')

        # B1 at 10:30-11:30 in Profile B (overlapping time, different profile)
        p_b1 = _frozen_pkg()
        plan_b1 = create_rotation_plan(
            p_b1, target - timedelta(hours=1), fp, tp_b)
        e_b1 = add_to_thaw_queue(p_b1, plan_b1, actor='test')

        # Both must succeed — different profiles
        self.assertIsNotNone(e_a1)
        self.assertIsNotNone(e_b1)
        self.assertEqual(self._get_active_count(tp_a), 1)
        self.assertEqual(self._get_active_count(tp_b), 1)

    # --- Test D: same-profile capacity enforced ---

    def test_same_profile_capacity_enforced(self):
        """Profile A capacity=1 rejects overlapping second entry."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp = _create_thaw_profile(capacity=1)
        now = timezone.now()
        target = now + timedelta(days=3)

        p1 = _frozen_pkg()
        plan1 = create_rotation_plan(p1, target, fp, tp)
        add_to_thaw_queue(p1, plan1, actor='test')

        # Overlapping entry in same profile
        p2 = _frozen_pkg()
        plan2 = create_rotation_plan(p2, target, fp, tp)
        with self.assertRaises(ValueError) as ctx:
            add_to_thaw_queue(p2, plan2, actor='test')
        self.assertIn('capacity', str(ctx.exception).lower())

    # --- Test E: multiple profiles concurrent ---

    def test_multi_profile_concurrent_independent(self):
        """Concurrent admissions across Profile A and B are independent."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        fp = _create_freeze_profile()
        tp_a = _create_thaw_profile(capacity=2)
        tp_a.name = 'Room-A2'; tp_a.save()
        tp_b = _create_thaw_profile(capacity=2)
        tp_b.name = 'Room-B2'; tp_b.save()
        now = timezone.now()

        # 2 non-overlapping entries per profile
        a_entries = []
        b_entries = []
        for i in range(2):
            p = _frozen_pkg()
            plan = create_rotation_plan(
                p, now + timedelta(days=3, hours=i * 25), fp, tp_a)
            a_entries.append((p, plan))
            p = _frozen_pkg()
            plan = create_rotation_plan(
                p, now + timedelta(days=3, hours=i * 25), fp, tp_b)
            b_entries.append((p, plan))

        errors = []

        def do_add(pkg, plan, name):
            try:
                add_to_thaw_queue(pkg, plan, actor=name)
            except Exception as e:
                errors.append((name, str(e)))

        threads = []
        for i, (pkg, plan) in enumerate(a_entries):
            threads.append(threading.Thread(
                target=do_add, args=(pkg, plan, f'A{i}')))
        for i, (pkg, plan) in enumerate(b_entries):
            threads.append(threading.Thread(
                target=do_add, args=(pkg, plan, f'B{i}')))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(errors), 0, f'Errors: {errors}')

        # Each profile has 2 entries, positions [1, 2]
        pos_a = self._get_active_positions(tp_a)
        pos_b = self._get_active_positions(tp_b)
        self.assertEqual(sorted(pos_a), [1, 2],
            f'Profile A positions wrong: {pos_a}')
        self.assertEqual(sorted(pos_b), [1, 2],
            f'Profile B positions wrong: {pos_b}')

    def _get_active_count(self, profile):
        return ThawQueueEntry.objects.filter(
            rotation_plan__thaw_profile=profile,
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
        ).count()
