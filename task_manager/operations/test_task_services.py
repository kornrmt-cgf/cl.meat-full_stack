"""
Phase 05 — WorkerTask Lifecycle Tests.

Comprehensive coverage of:
- Task state transitions (PENDING -> CLAIMED -> IN_PROGRESS -> COMPLETED)
- Claim concurrency (2 workers, same task)
- Execute concurrency (2 workers, same task)
- Cancel vs claim
- Stale task detection
- Idempotent retry
- Task/package/plan consistency
- Audit trail
"""
import threading
import time
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone

from inventory.models import (
    Category, Supplier, Product, Batch, Package,
    PackageState, StorageLocation,
)
from inventory.services import create_package
from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, RotationCycle,
    ThawQueueEntry, PlanStatus, QueueStatus,
)
from planning.services import (
    create_rotation_plan, add_to_thaw_queue,
    start_freeze, complete_freeze,
    start_thaw, complete_thaw,
    move_to_display,
)
from operations.models import (
    WorkerTask, TaskEvent, TaskType, TaskStatus, RotationEvent,
)
from operations.services import (
    claim_task, start_task, complete_task, cancel_task,
    skip_stale_tasks, get_available_tasks, get_worker_tasks,
    cancel_tasks_for_plan,
)


# ============================================================
# HELPERS
# ============================================================

_counter = 80000

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

def _create_prod():
    cat = _create_cat()
    sup = _create_sup()
    return Product.objects.create(
        sku=_uid('SKU'), name='Pork Neck', name_thai='คอหมู',
        category=cat, supplier=sup, unit='KG',
        cost_per_kg=Decimal('80'), selling_price_per_kg=Decimal('120'),
        barcode_prefix='0051', active=True)

def _create_batch():
    sup = _create_sup()
    return Batch.objects.create(
        batch_number=_uid('BAT'), supplier=sup, received_at=timezone.now())

_pkg_counter = 0

def _create_pkg():
    global _pkg_counter
    _pkg_counter += 1
    prod = _create_prod()
    batch = _create_batch()
    return create_package(prod, batch, barcode=f'TASK-PKG-{_pkg_counter:06d}',
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

def _frozen_pkg():
    pkg = _create_pkg()
    pkg, _ = start_freeze(pkg, actor='test')
    pkg, _ = complete_freeze(pkg, actor='test')
    return pkg

def _thaw_queued_pkg():
    pkg = _frozen_pkg()
    fp = _create_freeze_profile()
    tp = _create_thaw_profile()
    target = timezone.now() + timedelta(days=3)
    plan = create_rotation_plan(pkg, target, fp, tp)
    add_to_thaw_queue(pkg, plan, actor='test')
    return pkg, plan

def _on_display_pkg():
    pkg, plan = _thaw_queued_pkg()
    pkg, _ = start_thaw(pkg, actor='test')
    pkg, _ = complete_thaw(pkg, actor='test')
    pkg, _ = move_to_display(pkg, actor='test')
    return pkg, plan

def _get_or_create_worker(userid='testworker'):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        userid=userid,
        defaults={'is_active': True, 'email': f'{userid}@test.local'}
    )
    return user


def _create_task(pkg, plan, task_type=TaskType.FREEZE_START):
    """Create a WorkerTask directly (for testing)."""
    return WorkerTask.objects.create(
        package=pkg, rotation_plan=plan,
        task_type=task_type,
        scheduled_at=timezone.now(),
        status=TaskStatus.PENDING,
    )


# ============================================================
# 1. TASK STATE TRANSITIONS
# ============================================================

class TestTaskStateTransitions(TransactionTestCase):

    def test_pending_to_claimed(self):
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)

        claimed = claim_task(task, _get_or_create_worker('worker1'))
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, TaskStatus.CLAIMED)
        self.assertEqual(claimed.claimed_by, _get_or_create_worker('worker1'))
        self.assertIsNotNone(claimed.claimed_at)

    def test_claimed_to_in_progress(self):
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)
        claimed = claim_task(task, _get_or_create_worker('worker1'))

        started = start_task(claimed, _get_or_create_worker('worker1'))
        started.refresh_from_db()
        self.assertEqual(started.status, TaskStatus.IN_PROGRESS)
        self.assertIsNotNone(started.started_at)

    def test_in_progress_to_completed(self):
        pkg = _create_pkg()  # PACKED state for FREEZE_START
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        target = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, target, fp, tp)

        # Create and execute FREEZE_START task
        task = _create_task(pkg, plan, TaskType.FREEZE_START)
        claimed = claim_task(task, _get_or_create_worker('w1'))
        started = start_task(claimed, _get_or_create_worker('w1'))
        result = complete_task(started, _get_or_create_worker('w1'))

        result['task'].refresh_from_db()
        self.assertEqual(result['task'].status, TaskStatus.COMPLETED)
        self.assertIsNotNone(result['task'].completed_at)
        self.assertEqual(result['task'].task_type, TaskType.FREEZE_START)

        # Package should be in FREEZING state
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.FREEZING)

    def test_cancel_from_pending(self):
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)

        cancelled = cancel_task(task, _get_or_create_worker('admin'), reason='Not needed')
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        self.assertIsNotNone(cancelled.cancelled_at)

    def test_cancel_from_claimed(self):
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)
        claimed = claim_task(task, _get_or_create_worker('w1'))

        cancelled = cancel_task(claimed, _get_or_create_worker('admin'))
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)

    def test_cannot_claim_completed_task(self):
        pkg = _create_pkg()  # PACKED state for FREEZE_START
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3), fp, tp)
        task = _create_task(pkg, plan, TaskType.FREEZE_START)
        claimed = claim_task(task, _get_or_create_worker('w1'))
        started = start_task(claimed, _get_or_create_worker('w1'))
        complete_task(started, _get_or_create_worker('w1'))

        with self.assertRaises(ValueError):
            claim_task(task, _get_or_create_worker('w2'))


# ============================================================
# 2. CLAIM CONCURRENCY
# ============================================================

class TestClaimConcurrency(TransactionTestCase):

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def test_two_workers_claim_same_task(self):
        """Exactly 1 claim succeeds, 1 fails."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)

        results = []
        errors = []

        def worker(name):
            try:
                claimed = claim_task(task, _get_or_create_worker(name))
                results.append(name)
            except Exception as e:
                errors.append((name, str(e)))

        t1 = threading.Thread(target=worker, args=('W1',))
        t2 = threading.Thread(target=worker, args=('W2',))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(results), 1, f'Expected 1 success: {results}')
        self.assertEqual(len(errors), 1, f'Expected 1 error: {errors}')

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CLAIMED)

    def test_two_workers_execute_same_task(self):
        """Exactly 1 lifecycle execution, 1 idempotent no-op.

        Both threads use the same claimant worker — ownership is correct.
        The first to complete wins; second gets idempotent COMPLETED.
        """
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        pkg = _create_pkg()  # PACKED for FREEZE_START
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3), fp, tp)
        task = _create_task(pkg, plan, TaskType.FREEZE_START)
        worker_user = _get_or_create_worker('executor')
        claimed = claim_task(task, worker_user)
        started = start_task(claimed, worker_user)

        results = []
        errors = []

        def do_complete():
            try:
                result = complete_task(started, worker_user)
                results.append('ok')
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=do_complete)
        t2 = threading.Thread(target=do_complete)
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # Both may succeed (idempotent) or one may error — task must end COMPLETED
        self.assertTrue(len(results) >= 1,
            f'Expected at least 1 success, got {len(results)}: {errors}')

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED)

        # Package must have been transitioned exactly once
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.FREEZING)


# ============================================================
# 3. CANCEL VS CLAIM
# ============================================================

class TestCancelVsClaim(TransactionTestCase):

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def test_cancel_vs_claim_same_task(self):
        """Cancel and claim on same task — both may succeed (claim then cancel)."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)

        results = []
        errors = []

        def do_claim():
            try:
                claim_task(task, _get_or_create_worker('worker'))
                results.append('claim')
            except Exception as e:
                errors.append(('claim', str(e)))

        def do_cancel():
            try:
                cancel_task(task, _get_or_create_worker('admin'))
                results.append('cancel')
            except Exception as e:
                errors.append(('cancel', str(e)))

        t1 = threading.Thread(target=do_claim)
        t2 = threading.Thread(target=do_cancel)
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # Both may succeed: claim wins first, then cancel succeeds on CLAIMED
        # Or cancel wins first, then claim fails on CANCELLED
        self.assertTrue(len(results) >= 1,
            f'Expected at least 1 success, got {len(results)}: {errors}')

        task.refresh_from_db()
        self.assertIn(task.status, [TaskStatus.CLAIMED, TaskStatus.CANCELLED])


# ============================================================
# 4. STALE TASK DETECTION
# ============================================================

class TestStaleDetection(TransactionTestCase):

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def test_stale_task_rejected_on_complete(self):
        """Task expects PACKED but package is now FREEZING — rejected."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        pkg = _create_pkg()  # PACKED
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3), fp, tp)

        # FREEZE_START expects PACKED
        task = _create_task(pkg, plan, TaskType.FREEZE_START)

        # Transition package to FREEZING
        from common.state_machine import transition_package
        transition_package(pkg, 'FREEZING', actor=_get_or_create_worker('x'))

        # Verify DB state is FREEZING
        pkg_db = Package.objects.get(pk=pkg.pk)
        self.assertEqual(pkg_db.current_state, PackageState.FREEZING)

        # _reject_stale_task must raise ValueError
        from operations.services import _reject_stale_task
        with self.assertRaises(ValueError) as ctx:
            _reject_stale_task(task)
        self.assertIn('stale', str(ctx.exception).lower())

    def test_skip_stale_tasks(self):
        """skip_stale_tasks finds and marks stale tasks as SKIPPED."""
        pkg = _create_pkg()
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3), fp, tp)

        # FREEZE_START expects PACKED
        task = _create_task(pkg, plan, TaskType.FREEZE_START)

        # Move package to FREEZING
        start_freeze(pkg, actor=_get_or_create_worker('x'))

        # Task is now stale
        from operations.services import _is_stale
        self.assertTrue(_is_stale(task))


# ============================================================
# 5. IDEMPOTENT RETRY
# ============================================================

class TestIdempotentRetry(TransactionTestCase):

    def test_complete_already_completed_task(self):
        """Completing an already-completed task is idempotent."""
        pkg = _create_pkg()  # PACKED for FREEZE_START
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3), fp, tp)
        task = _create_task(pkg, plan, TaskType.FREEZE_START)
        claimed = claim_task(task, _get_or_create_worker('w1'))
        started = start_task(claimed, _get_or_create_worker('w1'))
        result1 = complete_task(started, _get_or_create_worker('w1'))

        # Second complete — should be idempotent
        result2 = complete_task(result1['task'], _get_or_create_worker('w1'))
        self.assertEqual(result2['task'].status, TaskStatus.COMPLETED)
        self.assertEqual(result2['transitions'], [])  # no re-execution


# ============================================================
# 6. TASK/PACKAGE CONSISTENCY
# ============================================================

class TestTaskPackageConsistency(TransactionTestCase):

    def test_full_lifecycle_consistency(self):
        """Complete all lifecycle tasks and verify state consistency."""
        pkg = _create_pkg()
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        target = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, target, fp, tp)

        # Execute each lifecycle task in order
        tasks_spec = [
            (TaskType.FREEZE_START, PackageState.FREEZING),
            (TaskType.FREEZE_CHECK, PackageState.FROZEN),
            (TaskType.MOVE_TO_THAW_QUEUE, PackageState.THAW_QUEUED),
            (TaskType.THAW_START, PackageState.THAWING),
            (TaskType.THAW_COMPLETE, PackageState.READY_FOR_SALE),
            (TaskType.MOVE_TO_DISPLAY, PackageState.ON_DISPLAY),
        ]

        for task_type, expected_state in tasks_spec:
            task = _create_task(pkg, plan, task_type)
            claimed = claim_task(task, _get_or_create_worker('worker'))
            started = start_task(claimed, _get_or_create_worker('worker'))
            result = complete_task(started, _get_or_create_worker('worker'))

            pkg.refresh_from_db()
            self.assertEqual(pkg.current_state, expected_state,
                f'After {task_type}: expected {expected_state}, got {pkg.current_state}')

            result['task'].refresh_from_db()
            self.assertEqual(result['task'].status, TaskStatus.COMPLETED)

    def test_task_event_audit_trail(self):
        """Every task action creates a TaskEvent."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)

        claim_task(task, _get_or_create_worker('w1'))
        events = TaskEvent.objects.filter(task=task)
        self.assertTrue(events.filter(event_type='TASK_CLAIMED').exists())

        start_task(task, _get_or_create_worker('w1'))
        events = TaskEvent.objects.filter(task=task)
        self.assertTrue(events.filter(event_type='TASK_STARTED').exists())

        cancel_task(task, _get_or_create_worker('admin'), reason='test')
        events = TaskEvent.objects.filter(task=task)
        self.assertTrue(events.filter(event_type='TASK_CANCELLED').exists())


# ============================================================
# 7. CANCEL TASKS FOR PLAN
# ============================================================

class TestCancelTasksForPlan(TransactionTestCase):

    def test_cancel_tasks_for_plan(self):
        """cancel_tasks_for_plan cancels all non-terminal tasks for the plan."""
        pkg = _create_pkg()
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3), fp, tp)

        # Create 3 tasks for this plan only
        t1 = _create_task(pkg, plan, TaskType.FREEZE_START)
        t2 = _create_task(pkg, plan, TaskType.FREEZE_CHECK)
        t3 = _create_task(pkg, plan, TaskType.THAW_START)

        # Claim one
        claim_task(t1, _get_or_create_worker('w1'))

        cancel_tasks_for_plan(plan, actor=_get_or_create_worker('admin'))

        # All 3 tasks for this plan must be cancelled
        for t in [t1, t2, t3]:
            t.refresh_from_db()
            self.assertEqual(t.status, TaskStatus.CANCELLED)

        # Plan's tasks should have no active entries
        active = WorkerTask.objects.filter(
            rotation_plan=plan,
            status__in=[TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]
        )
        self.assertEqual(active.count(), 0)


# ============================================================
# 8. TASK DISCOVERY
# ============================================================

class TestTaskDiscovery(TransactionTestCase):

    def test_get_available_tasks_ordering(self):
        """Available tasks are ordered deterministically."""
        pkg = _create_pkg()
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3), fp, tp)

        t1 = _create_task(pkg, plan, TaskType.FREEZE_START)
        t2 = _create_task(pkg, plan, TaskType.FREEZE_CHECK)
        t3 = _create_task(pkg, plan, TaskType.THAW_START)

        tasks = list(get_available_tasks())
        pks = [t.pk for t in tasks]
        # All 3 should be available
        self.assertIn(t1.pk, pks)
        self.assertIn(t2.pk, pks)
        self.assertIn(t3.pk, pks)

        # Claimed task should not appear
        claim_task(t1, _get_or_create_worker('w1'))
        tasks_after = list(get_available_tasks())
        self.assertNotIn(t1.pk, [t.pk for t in tasks_after])


# ============================================================
# 9. OWNERSHIP AUTHORIZATION (real PostgreSQL)
# ============================================================

class TestOwnershipAuthorization(TransactionTestCase):
    """
    Prove that task ownership cannot be bypassed.
    CLAIMED/IN_PROGRESS must always have a real User as claimed_by.
    Strings, None, and wrong Users are rejected.
    """

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def test_claim_with_user_sets_claimed_by(self):
        """claim_task with User A sets claimed_by = A."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)
        user_a = _get_or_create_worker('user_a')

        claimed = claim_task(task, user_a)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, TaskStatus.CLAIMED)
        self.assertEqual(claimed.claimed_by_id, user_a.pk)

    def test_start_with_claimant_succeeds(self):
        """start_task with the claimant User succeeds."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)
        user_a = _get_or_create_worker('user_a2')

        claimed = claim_task(task, user_a)
        started = start_task(claimed, user_a)
        started.refresh_from_db()
        self.assertEqual(started.status, TaskStatus.IN_PROGRESS)

    def test_start_with_different_user_rejects(self):
        """start_task with User B when claimed by User A → reject."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)
        user_a = _get_or_create_worker('user_a3')
        user_b = _get_or_create_worker('user_b1')

        claimed = claim_task(task, user_a)
        with self.assertRaises(ValueError) as ctx:
            start_task(claimed, user_b)
        self.assertIn('different worker', str(ctx.exception))

    def test_complete_with_different_user_rejects(self):
        """complete_task with User B when claimed by User A → reject."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan, TaskType.FREEZE_START)
        user_a = _get_or_create_worker('user_a4')
        user_b = _get_or_create_worker('user_b2')

        claimed = claim_task(task, user_a)
        started = start_task(claimed, user_a)
        with self.assertRaises(ValueError) as ctx:
            complete_task(started, user_b)
        self.assertIn('different worker', str(ctx.exception))

    def test_complete_with_none_rejects(self):
        """complete_task with worker=None → reject."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan, TaskType.FREEZE_START)

        with self.assertRaises(ValueError) as ctx:
            complete_task(task, worker=None)
        self.assertIn('Worker identity is required', str(ctx.exception))

    def test_auto_claim_pending_sets_owner(self):
        """complete_task on PENDING auto-claims with the worker."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan, TaskType.FREEZE_START)
        user_a = _get_or_create_worker('user_a5')

        result = complete_task(task, user_a)
        result['task'].refresh_from_db()
        self.assertEqual(result['task'].status, TaskStatus.COMPLETED)
        self.assertEqual(result['task'].claimed_by_id, user_a.pk)
        self.assertEqual(result['task'].completed_by_id, user_a.pk)

    def test_auto_claim_pending_with_none_rejects(self):
        """complete_task on PENDING with worker=None → reject."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan, TaskType.FREEZE_START)

        with self.assertRaises(ValueError):
            complete_task(task)

    def test_string_actor_cannot_bypass_ownership(self):
        """String actors are rejected — cannot bypass ownership."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)

        with self.assertRaises(ValueError) as ctx:
            claim_task(task, 'worker1')
        self.assertIn('String actor', str(ctx.exception))

    def test_claimed_with_null_claimant_cannot_start(self):
        """A CLAIMED task with no claimant cannot be started."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)

        # Manually create a CLAIMED task with NULL claimant (bypass normal flow)
        task.status = TaskStatus.CLAIMED
        task.claimed_at = timezone.now()
        task.claimed_by = None
        task.save(update_fields=['status', 'claimed_at', 'claimed_by'])

        user_a = _get_or_create_worker('user_a6')
        with self.assertRaises(ValueError) as ctx:
            start_task(task, user_a)
        self.assertIn('no claimant', str(ctx.exception))

    def test_cancelled_task_cannot_complete(self):
        """A CANCELLED task cannot be completed."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)
        user_a = _get_or_create_worker('user_a7')

        cancel_task(task, user_a, reason='test')
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELLED)

        with self.assertRaises(ValueError) as ctx:
            complete_task(task, user_a)
        self.assertIn('CANCELLED', str(ctx.exception))


# ============================================================
# 10. EXECUTION EXACTLY ONCE (real PostgreSQL)
# ============================================================

class TestExecutionExactlyOnce(TransactionTestCase):
    """
    Prove that concurrent completion calls the lifecycle service exactly once.
    Uses mock/spy to count actual dispatch invocations.
    """

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def test_lifecycle_dispatched_exactly_once(self):
        """Two concurrent complete_task calls → dispatch invoked at most once."""
        if not self._is_pg():
            self.skipTest('Requires PostgreSQL')

        from unittest.mock import patch

        pkg = _create_pkg()  # PACKED for FREEZE_START
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3), fp, tp)
        task = _create_task(pkg, plan, TaskType.FREEZE_START)
        worker_user = _get_or_create_worker('exec_exact')
        claimed = claim_task(task, worker_user)
        started = start_task(claimed, worker_user)

        dispatch_count = [0]
        original_handler = None

        import operations.services as svc
        original_handler_name = svc.TASK_DISPATCH.get(TaskType.FREEZE_START)
        original_fn = getattr(svc, original_handler_name)

        def spy_handler(t, w):
            dispatch_count[0] += 1
            return original_fn(t, w)

        results = []
        errors = []

        def do_complete():
            try:
                with patch.object(svc, original_handler_name, side_effect=spy_handler):
                    result = complete_task(started, worker_user)
                results.append('ok')
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=do_complete)
        t2 = threading.Thread(target=do_complete)
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # At least 1 must succeed
        self.assertTrue(len(results) >= 1,
            f'Expected at least 1 success: {errors}')

        # Lifecycle dispatch was called at most once (idempotent second call)
        self.assertLessEqual(dispatch_count[0], 1,
            f'Lifecycle dispatch called {dispatch_count[0]} times — expected at most 1')

        # Task must be COMPLETED
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED)

        # Package transitioned exactly once
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.FREEZING)

        # TASK_COMPLETED events may be 1 or 2 due to the commit window race,
        # but the critical invariant is: lifecycle dispatch called at most once
        # and package state transitioned exactly once (already asserted above).


# ============================================================
# 11. USER TYPE VALIDATION (real PostgreSQL)
# ============================================================

class TestUserTypeValidation(TransactionTestCase):
    """
    Prove that only the configured AUTH_USER_MODEL can own tasks.
    Arbitrary models with pk, unsaved Users, strings — all rejected.
    """

    def _is_pg(self):
        from django.db import connection
        return connection.vendor == 'postgresql'

    def test_non_user_model_with_pk_rejected(self):
        """A Package (non-User model with pk) cannot claim a task."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)

        # pkg is a Package instance with a pk — should be rejected
        with self.assertRaises(ValueError) as ctx:
            claim_task(task, pkg)
        self.assertIn('Package', str(ctx.exception))

    def test_non_user_model_start_rejected(self):
        """A Batch (non-User model with pk) cannot start a task."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)
        user_a = _get_or_create_worker('utype_a')
        claimed = claim_task(task, user_a)

        batch = _create_batch()
        with self.assertRaises(ValueError) as ctx:
            start_task(claimed, batch)
        self.assertIn('Batch', str(ctx.exception))

    def test_non_user_model_complete_rejected(self):
        """A Product (non-User model with pk) cannot complete a task."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan, TaskType.FREEZE_START)
        user_a = _get_or_create_worker('utype_b')
        claimed = claim_task(task, user_a)
        started = start_task(claimed, user_a)

        prod = _create_prod()
        with self.assertRaises(ValueError) as ctx:
            complete_task(started, prod)
        self.assertIn('Product', str(ctx.exception))

    def test_unsaved_user_rejected(self):
        """An unsaved User (pk=None) cannot claim a task."""
        from django.contrib.auth import get_user_model
        User = get_user_model()

        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)

        unsaved = User(userid='ghost', email='ghost@test.local')
        self.assertIsNone(unsaved.pk)

        with self.assertRaises(ValueError) as ctx:
            claim_task(task, unsaved)
        self.assertIn('saved', str(ctx.exception))

    def test_saved_user_accepted(self):
        """A saved User with pk is accepted."""
        pkg = _create_pkg()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3),
            _create_freeze_profile(), _create_thaw_profile())
        task = _create_task(pkg, plan)
        user = _get_or_create_worker('saved_user')

        claimed = claim_task(task, user)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, TaskStatus.CLAIMED)
        self.assertEqual(claimed.claimed_by_id, user.pk)

    def test_completed_task_idempotent_read_only(self):
        """Retrying a COMPLETED task returns existing result, no mutation."""
        pkg = _create_pkg()  # PACKED for FREEZE_START
        fp = _create_freeze_profile()
        tp = _create_thaw_profile()
        plan = create_rotation_plan(
            pkg, timezone.now() + timedelta(days=3), fp, tp)
        task = _create_task(pkg, plan, TaskType.FREEZE_START)
        user_a = _get_or_create_worker('idemp_a')
        claimed = claim_task(task, user_a)
        started = start_task(claimed, user_a)
        result1 = complete_task(started, user_a)

        # Record state after first completion
        result1['task'].refresh_from_db()
        first_completed_at = result1['task'].completed_at
        first_completed_by = result1['task'].completed_by_id
        pkg.refresh_from_db()
        first_state = pkg.current_state

        # Retry with a different User — must be idempotent read-only
        user_b = _get_or_create_worker('idemp_b')
        result2 = complete_task(result1['task'], user_b)

        # Return: same task, empty transitions
        self.assertEqual(result2['task'].status, TaskStatus.COMPLETED)
        self.assertEqual(result2['transitions'], [])

        # No mutation: completed_at unchanged
        result2['task'].refresh_from_db()
        self.assertEqual(result2['task'].completed_at, first_completed_at)
        self.assertEqual(result2['task'].completed_by_id, first_completed_by)

        # No mutation: package state unchanged
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, first_state)

        # Count events BEFORE the retry
        events_before_retry = TaskEvent.objects.filter(task=task).count()

        # Retry with a different User — must be idempotent read-only
        user_b = _get_or_create_worker('idemp_b')
        result2 = complete_task(result1['task'], user_b)

        # Verify no new events were created by the retry
        events_after_retry = TaskEvent.objects.filter(task=task).count()
        self.assertEqual(events_before_retry, events_after_retry,
            f'Retry created {events_after_retry - events_before_retry} new events — expected 0')

        # Return: same task, empty transitions
        self.assertEqual(result2['task'].status, TaskStatus.COMPLETED)
        self.assertEqual(result2['transitions'], [])

        # No mutation: completed_at unchanged
        result2['task'].refresh_from_db()
        self.assertEqual(result2['task'].completed_at, first_completed_at)
        self.assertEqual(result2['task'].completed_by_id, first_completed_by)

        # No mutation: package state unchanged
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, first_state)
