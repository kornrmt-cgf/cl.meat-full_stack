"""
Operations Services — worker task completion, status updates, and lifecycle management.
"""
from django.db import transaction
from django.utils import timezone
from datetime import datetime, timedelta

from operations.models import WorkerTask, TaskEvent, TaskType, TaskStatus, RotationEvent
from inventory.models import Package, PackageState


# ============================================================
# TASK COMPLETION
# ============================================================

@transaction.atomic
def complete_task(task, actor, notes=''):
    """
    Mark a worker task as completed.

    Automatically triggers package state transitions via the state machine.
    Returns dict with 'task' and 'transitions' (list of state changes).

    Args:
        task: WorkerTask instance
        actor: User instance, string username, or empty string
        notes: Optional notes
    """
    if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]:
        raise ValueError(f"Task is already {task.status}")

    task.status = TaskStatus.COMPLETED
    task.completed_at = timezone.now()
    task.notes = notes

    # Handle actor: User instance or string
    if hasattr(actor, 'pk'):
        task.completed_by = actor
    # If string, leave completed_by as None (it's a nullable FK)

    task.save(update_fields=['status', 'completed_at', 'notes', 'updated_at'])

    actor_name = str(actor) if actor else 'system'
    TaskEvent.objects.create(
        task=task, event_type='TASK_COMPLETED',
        timestamp=timezone.now(), actor=actor_name, notes=notes,
    )

    transitions = _auto_transition_on_task_complete(task, actor_name)
    return {'task': task, 'transitions': transitions}


def _auto_transition_on_task_complete(task, actor_name):
    """Auto-transition package state when a worker task is completed."""
    from common.state_machine import transition_package, InvalidTransitionError, TransitionValidationError
    from planning.models import ThawQueueEntry, QueueStatus

    package = task.package
    transitions = []

    transition_map = {
        TaskType.FREEZE_START: ('FREEZING', 'PACKED'),
        TaskType.FREEZE_CHECK: ('FROZEN', 'FREEZING'),
        TaskType.MOVE_TO_THAW_QUEUE: ('THAW_QUEUED', 'FROZEN'),
        TaskType.THAW_START: ('THAWING', 'THAW_QUEUED'),
        TaskType.THAW_COMPLETE: ('READY_FOR_SALE', 'THAWING'),
        TaskType.MOVE_TO_DISPLAY: ('ON_DISPLAY', 'READY_FOR_SALE'),
        TaskType.REFREEZE: ('REFREEZE_PENDING', 'ON_DISPLAY'),
    }

    if task.task_type in transition_map:
        target, expected_current = transition_map[task.task_type]
        if package.current_state == expected_current:
            try:
                transition_package(package, target, actor=actor_name,
                                  reason=f'{task.task_type} completed')
                transitions.append((expected_current, target))

                # Special: THAW_COMPLETE also marks queue entry
                if task.task_type == TaskType.THAW_COMPLETE:
                    queue_entry = ThawQueueEntry.objects.filter(
                        package=package,
                        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
                    ).first()
                    if queue_entry:
                        queue_entry.status = QueueStatus.COMPLETED
                        queue_entry.save(update_fields=['status', 'updated_at'])

            except (InvalidTransitionError, TransitionValidationError):
                pass

    # FREEZE_START special: if freeze schedule says freeze should be complete
    if task.task_type == TaskType.FREEZE_START:
        if (task.rotation_plan and task.rotation_plan.planned_freeze_end_at
                and task.rotation_plan.planned_freeze_end_at <= timezone.now()):
            try:
                package.refresh_from_db()
                if package.current_state == 'FREEZING':
                    transition_package(package, 'FROZEN', actor=actor_name, reason='Freeze schedule complete')
                    transitions.append(('FREEZING', 'FROZEN'))
            except (InvalidTransitionError, TransitionValidationError):
                pass

    return transitions


# ============================================================
# QUERIES
# ============================================================

def get_todays_tasks():
    """Get all tasks scheduled for today (Bangkok time)."""
    bangkok_today = timezone.localtime(timezone.now()).date()
    start = timezone.make_aware(datetime.combine(bangkok_today, datetime.min.time()))
    end = start + timedelta(days=1)
    return WorkerTask.objects.filter(
        scheduled_at__gte=start, scheduled_at__lt=end,
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('scheduled_at')


def get_overdue_tasks():
    """Get all overdue tasks."""
    return WorkerTask.objects.filter(
        scheduled_at__lt=timezone.now(),
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('scheduled_at')


def update_task_status():
    """Mark tasks as overdue if they are 30+ minutes past scheduled time."""
    WorkerTask.objects.filter(
        scheduled_at__lt=timezone.now() - timedelta(minutes=30),
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    ).update(status=TaskStatus.OVERDUE)


def get_task_history(days=7):
    """Get completed/overdue/cancelled tasks for the last N days."""
    start_date = timezone.now() - timedelta(days=days)
    return WorkerTask.objects.filter(
        created_at__gte=start_date,
        status__in=[TaskStatus.COMPLETED, TaskStatus.OVERDUE, TaskStatus.CANCELLED]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('-completed_at')
