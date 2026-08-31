"""
Operations Services — WorkerTask lifecycle services.

Task state machine:
    PENDING -> CLAIMED -> IN_PROGRESS -> COMPLETED
    PENDING / CLAIMED / IN_PROGRESS -> CANCELLED
    PENDING / CLAIMED / IN_PROGRESS -> SKIPPED (stale tasks)

Lock hierarchy (globally consistent):
    WorkerTask row (SELECT FOR UPDATE) -> lifecycle service locks

WorkerTask must NOT become a source of truth for Package state.
Package state remains authoritative via the state machine.
Task execution calls lifecycle services; direct mutation is forbidden.
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from operations.models import WorkerTask, TaskEvent, TaskType, TaskStatus
from inventory.models import Package, PackageState


# ============================================================
# TASK DISCOVERY
# ============================================================

TASK_DISPATCH = {
    TaskType.FREEZE_START: '_dispatch_freeze_start',
    TaskType.FREEZE_CHECK: '_dispatch_freeze_check',
    TaskType.MOVE_TO_THAW_QUEUE: '_dispatch_move_to_thaw_queue',
    TaskType.THAW_START: '_dispatch_thaw_start',
    TaskType.THAW_CHECK: '_dispatch_thaw_check',
    TaskType.THAW_COMPLETE: '_dispatch_thaw_complete',
    TaskType.MOVE_TO_DISPLAY: '_dispatch_move_to_display',
}


def get_available_tasks(worker=None):
    """
    Get deterministic ordered list of available tasks.

    Order: scheduled_at, created_at, pk
    Filter: PENDING status only (CLAIMED tasks are being worked on).
    """
    tasks = WorkerTask.objects.filter(
        status=TaskStatus.PENDING
    ).select_related(
        'package', 'package__product', 'rotation_plan'
    ).order_by('scheduled_at', 'created_at', 'pk')
    return tasks


def get_worker_tasks(worker):
    """Get tasks currently claimed or in-progress by a specific worker."""
    return WorkerTask.objects.filter(
        claimed_by=worker,
        status__in=[TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]
    ).select_related('package', 'package__product', 'rotation_plan')


# ============================================================
# CLAIM
# ============================================================

@transaction.atomic
def claim_task(task, worker):
    """
    Atomically claim a PENDING task for a worker.

    Uses SELECT FOR UPDATE on the task row to serialize concurrent claims.
    Exactly one worker can claim a given task.

    Args:
        task: WorkerTask instance (or pk — will be re-fetched)
        worker: User instance or string identifier

    Returns:
        WorkerTask: the claimed task

    Raises:
        ValueError: if task is not claimable
    """
    # Lock the task row
    task = WorkerTask.objects.select_for_update().get(pk=task.pk)

    if task.status != TaskStatus.PENDING:
        raise ValueError(
            f"Cannot claim task: status is {task.status}, expected PENDING"
        )

    now = timezone.now()
    task.status = TaskStatus.CLAIMED
    task.claimed_at = now

    if hasattr(worker, 'pk'):
        task.claimed_by = worker

    task.save(update_fields=[
        'status', 'claimed_by', 'claimed_at', 'updated_at'
    ])

    _log_event(task, 'TASK_CLAIMED', actor=_actor_name(worker))
    return task


# ============================================================
# START
# ============================================================

@transaction.atomic
def start_task(task, worker):
    """
    Transition a CLAIMED task to IN_PROGRESS.

    Only the claiming worker may start the task.

    Args:
        task: WorkerTask instance
        worker: the worker who claimed the task

    Returns:
        WorkerTask

    Raises:
        ValueError: if task is not in CLAIMED state or wrong worker
    """
    task = WorkerTask.objects.select_for_update().get(pk=task.pk)

    if task.status != TaskStatus.CLAIMED:
        raise ValueError(
            f"Cannot start task: status is {task.status}, expected CLAIMED"
        )

    # Verify same worker who claimed
    if hasattr(worker, 'pk') and task.claimed_by_id and task.claimed_by_id != worker.pk:
        raise ValueError(
            "Cannot start task: claimed by different worker"
        )

    task.status = TaskStatus.IN_PROGRESS
    task.started_at = timezone.now()
    task.save(update_fields=['status', 'started_at', 'updated_at'])

    _log_event(task, 'TASK_STARTED', actor=_actor_name(worker))
    return task


# ============================================================
# COMPLETE
# ============================================================

@transaction.atomic
def complete_task(task, worker=None, notes='', **kwargs):
    """
    Complete a task by executing the corresponding lifecycle service.

    The task must be IN_PROGRESS (or CLAIMED). The lifecycle service is called
    inside the same transaction — if it fails, both task and
    package state are rolled back.

    Task completion is idempotent: if already COMPLETED, returns
    the task without re-executing.

    Args:
        task: WorkerTask instance
        worker: the worker completing the task (positional or 'actor' kwarg)
        notes: optional completion notes

    Returns:
        dict: {'task': WorkerTask, 'transitions': [...]}

    Raises:
        ValueError: if task cannot be completed
    """
    # Backward compat: accept actor= keyword
    if worker is None:
        worker = kwargs.get('actor', None)
    # Remember original object for in-place refresh at the end
    original_task = task
    task = WorkerTask.objects.select_for_update().get(pk=task.pk)
    now = timezone.now()

    # Idempotent: already completed
    if task.status == TaskStatus.COMPLETED:
        return {'task': task, 'transitions': []}

    # Backward compat: auto-claim and auto-start PENDING tasks
    if task.status == TaskStatus.PENDING:
        task.status = TaskStatus.CLAIMED
        task.claimed_at = now
        task.claimed_by = worker if hasattr(worker, 'pk') else None
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = now
        task.save(update_fields=['status', 'claimed_by', 'claimed_at', 'started_at', 'updated_at'])
    elif task.status == TaskStatus.CLAIMED:
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = now
        task.save(update_fields=['status', 'started_at', 'updated_at'])
    elif task.status != TaskStatus.IN_PROGRESS:
        raise ValueError(
            f"Cannot complete task: status is {task.status}"
        )

    # Stale detection: check package state matches expected
    _reject_stale_task(task)

    # Dispatch to lifecycle service
    transitions = _dispatch(task, worker)

    now = timezone.now()
    task.status = TaskStatus.COMPLETED
    task.completed_at = now
    task.notes = notes
    if hasattr(worker, 'pk'):
        task.completed_by = worker
    task.save(update_fields=[
        'status', 'completed_at', 'completed_by', 'notes', 'updated_at'
    ])

    _log_event(task, 'TASK_COMPLETED', actor=_actor_name(worker),
               notes=notes)

    # Refresh the original caller's object in-place so the test can
    # check the task state without re-fetching.
    original_task.refresh_from_db()

    return {'task': task, 'transitions': transitions}


# ============================================================
# CANCEL
# ============================================================

@transaction.atomic
def cancel_task(task, actor, reason=''):
    """
    Cancel a task (PENDING, CLAIMED, or IN_PROGRESS).

    Cancelled tasks must never execute lifecycle mutations.

    Args:
        task: WorkerTask instance
        actor: who cancelled
        reason: why

    Returns:
        WorkerTask

    Raises:
        ValueError: if task is already terminal
    """
    task = WorkerTask.objects.select_for_update().get(pk=task.pk)

    if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.SKIPPED]:
        raise ValueError(
            f"Cannot cancel task: status is {task.status}"
        )

    task.status = TaskStatus.CANCELLED
    task.cancelled_at = timezone.now()
    task.save(update_fields=['status', 'cancelled_at', 'updated_at'])

    _log_event(task, 'TASK_CANCELLED', actor=_actor_name(actor),
               notes=reason)
    return task


# ============================================================
# STALE DETECTION
# ============================================================

@transaction.atomic
def skip_stale_tasks(actor='system'):
    """
    Find and skip tasks whose package state no longer matches expected.

    Returns count of skipped tasks.
    """
    from common.state_machine import can_transition

    tasks = WorkerTask.objects.select_for_update().filter(
        status__in=[TaskStatus.PENDING, TaskStatus.CLAIMED]
    ).select_related('package')

    skipped = 0
    for task in tasks:
        if _is_stale(task):
            task.status = TaskStatus.SKIPPED
            task.save(update_fields=['status', 'updated_at'])
            _log_event(task, 'TASK_SKIPPED_STALE', actor=actor,
                       notes=f'Package state: {task.package.current_state}')
            skipped += 1

    return skipped


def _is_stale(task):
    """Check if a task is stale (package state no longer matches expected)."""
    # Refresh from DB to avoid stale FK cache
    pkg = Package.objects.get(pk=task.package_id)
    expected = _expected_package_state(task.task_type)

    if expected is None:
        return False

    # Task is stale if package is already past the expected state
    return pkg.current_state != expected


def _reject_stale_task(task):
    """Raise ValueError if task is stale. Called before execution."""
    if _is_stale(task):
        raise ValueError(
            f"Task is stale: expected package state "
            f"{_expected_package_state(task.task_type)}, "
            f"actual: {task.package.current_state}"
        )


def _expected_package_state(task_type):
    """Return the expected package state for a given task type."""
    return {
        TaskType.FREEZE_START: PackageState.PACKED,
        TaskType.FREEZE_CHECK: PackageState.FREEZING,
        TaskType.MOVE_TO_THAW_QUEUE: PackageState.FROZEN,
        TaskType.THAW_START: PackageState.THAW_QUEUED,
        TaskType.THAW_CHECK: PackageState.THAWING,
        TaskType.THAW_COMPLETE: PackageState.THAWING,
        TaskType.MOVE_TO_DISPLAY: PackageState.READY_FOR_SALE,
    }.get(task_type)


# ============================================================
# DISPATCH — calls lifecycle services, NOT direct state mutation
# ============================================================

def _dispatch(task, worker):
    """Dispatch task to the appropriate lifecycle service."""
    handler_name = TASK_DISPATCH.get(task.task_type)
    if handler_name is None:
        return []  # task types without lifecycle dispatch

    handler = globals()[handler_name]
    return handler(task, worker)


def _dispatch_freeze_start(task, worker):
    from planning.services import start_freeze
    actor = _actor_name(worker)
    start_freeze(task.package, actor=actor, reason=f'Task {task.pk}')
    return [('PACKED', 'FREEZING')]


def _dispatch_freeze_check(task, worker):
    from planning.services import complete_freeze
    actor = _actor_name(worker)
    complete_freeze(task.package, actor=actor, reason=f'Task {task.pk}')
    return [('FREEZING', 'FROZEN')]


def _dispatch_move_to_thaw_queue(task, worker):
    from planning.services import add_to_thaw_queue
    actor = _actor_name(worker)
    add_to_thaw_queue(task.package, task.rotation_plan, actor=actor)
    return [('FROZEN', 'THAW_QUEUED')]


def _dispatch_thaw_start(task, worker):
    from planning.services import start_thaw
    actor = _actor_name(worker)
    start_thaw(task.package, actor=actor, reason=f'Task {task.pk}')
    return [('THAW_QUEUED', 'THAWING')]


def _dispatch_thaw_check(task, worker):
    # Thaw check is an inspection — no state transition
    _log_event(task, 'THAW_CHECK_PERFORMED', actor=_actor_name(worker))
    return []


def _dispatch_thaw_complete(task, worker):
    from planning.services import complete_thaw
    actor = _actor_name(worker)
    complete_thaw(task.package, actor=actor, reason=f'Task {task.pk}')
    return [('THAWING', 'READY_FOR_SALE')]


def _dispatch_move_to_display(task, worker):
    from planning.services import move_to_display
    actor = _actor_name(worker)
    move_to_display(task.package, actor=actor, reason=f'Task {task.pk}')
    return [('READY_FOR_SALE', 'ON_DISPLAY')]


# ============================================================
# PLAN CANCELLATION INTEGRATION
# ============================================================

@transaction.atomic
def cancel_tasks_for_plan(plan, actor='system', reason=''):
    """
    Cancel all non-terminal tasks for a rotation plan.

    Must be called within an existing transaction (from cancel_rotation_plan).
    """
    tasks = WorkerTask.objects.select_for_update().filter(
        rotation_plan=plan,
        status__in=[TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]
    )

    count = 0
    for task in tasks:
        task.status = TaskStatus.CANCELLED
        task.cancelled_at = timezone.now()
        task.save(update_fields=['status', 'cancelled_at', 'updated_at'])
        _log_event(task, 'TASK_CANCELLED_PLAN', actor=actor, notes=reason)
        count += 1

    return count


# ============================================================
# HELPERS
# ============================================================

def _actor_name(actor):
    if hasattr(actor, 'pk'):
        return str(actor)
    return str(actor) if actor else 'system'


def _log_event(task, event_type, actor='system', notes=''):
    TaskEvent.objects.create(
        task=task,
        event_type=event_type,
        timestamp=timezone.now(),
        actor=actor,
        notes=notes,
    )


# ============================================================
# QUERIES (backward-compatible)
# ============================================================

def get_todays_tasks():
    """Get all tasks scheduled for today (Bangkok time)."""
    from datetime import datetime, timedelta
    bangkok_today = timezone.localtime(timezone.now()).date()
    start = timezone.make_aware(datetime.combine(bangkok_today, datetime.min.time()))
    end = start + timedelta(days=1)
    return WorkerTask.objects.filter(
        scheduled_at__gte=start, scheduled_at__lt=end,
        status__in=[TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('scheduled_at')


def get_overdue_tasks():
    """Get all overdue tasks."""
    from datetime import timedelta
    return WorkerTask.objects.filter(
        scheduled_at__lt=timezone.now(),
        status__in=[TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('scheduled_at')


def update_task_status():
    """Mark tasks as overdue if they are 30+ minutes past scheduled time."""
    from datetime import timedelta
    WorkerTask.objects.filter(
        scheduled_at__lt=timezone.now() - timedelta(minutes=30),
        status__in=[TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]
    ).update(status=TaskStatus.OVERDUE)


def get_task_history(days=7):
    """Get completed/overdue/cancelled tasks for the last N days."""
    from datetime import timedelta
    start_date = timezone.now() - timedelta(days=days)
    return WorkerTask.objects.filter(
        created_at__gte=start_date,
        status__in=[TaskStatus.COMPLETED, TaskStatus.OVERDUE, TaskStatus.CANCELLED]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('-completed_at')
