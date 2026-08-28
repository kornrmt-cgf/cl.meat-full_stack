"""
Operations Business Logic Services.
"""
from django.db import transaction
from django.utils import timezone
from datetime import datetime, timedelta
from .models import WorkerTask, TaskEvent, TaskType, TaskStatus, RotationEvent
from inventory.models import Package, PackageState
from planning.models import RotationPlan, ThawQueueEntry, QueueStatus


def generate_worker_tasks(rotation_plan):
    """
    Generate worker tasks for a rotation plan.
    
    Args:
        rotation_plan: RotationPlan instance
        
    Returns:
        list: Created WorkerTask instances
    """
    tasks = []
    package = rotation_plan.package
    
    # Freeze start task
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=rotation_plan,
        task_type=TaskType.FREEZE_START,
        scheduled_at=rotation_plan.planned_freeze_start_at,
        status=TaskStatus.PENDING
    ))
    
    # Freeze check task (2 hours after freeze start)
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=rotation_plan,
        task_type=TaskType.FREEZE_CHECK,
        scheduled_at=rotation_plan.planned_freeze_start_at + timedelta(hours=2),
        status=TaskStatus.PENDING
    ))
    
    # Move to thaw queue task
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=rotation_plan,
        task_type=TaskType.MOVE_TO_THAW_QUEUE,
        scheduled_at=rotation_plan.planned_thaw_queue_at,
        status=TaskStatus.PENDING
    ))
    
    # Thaw start task
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=rotation_plan,
        task_type=TaskType.THAW_START,
        scheduled_at=rotation_plan.planned_thaw_start_at,
        status=TaskStatus.PENDING
    ))
    
    # Thaw check task (halfway through thaw)
    thaw_check_time = rotation_plan.planned_thaw_start_at + (rotation_plan.thaw_duration / 2)
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=rotation_plan,
        task_type=TaskType.THAW_CHECK,
        scheduled_at=thaw_check_time,
        status=TaskStatus.PENDING
    ))
    
    # Thaw complete task
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=rotation_plan,
        task_type=TaskType.THAW_COMPLETE,
        scheduled_at=rotation_plan.target_ready_at,
        status=TaskStatus.PENDING
    ))
    
    # Move to display task
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=rotation_plan,
        task_type=TaskType.MOVE_TO_DISPLAY,
        scheduled_at=rotation_plan.target_ready_at + timedelta(minutes=15),
        status=TaskStatus.PENDING
    ))
    
    # Bulk create
    created_tasks = WorkerTask.objects.bulk_create(tasks)
    
    return created_tasks


@transaction.atomic
def complete_task(task, actor, notes=''):
    """
    Mark a worker task as completed.
    
    Automatically triggers package state transitions based on task type:
    - FREEZE_START completed → PACKED → FREEZING (+ FROZEN if freeze time elapsed)
    - FREEZE_CHECK completed → FREEZING → FROZEN (if freeze time elapsed)
    - MOVE_TO_THAW_QUEUE completed → FROZEN → READY_FOR_THAW → THAW_QUEUED
    - THAW_START completed → THAW_QUEUED → THAWING
    - THAW_COMPLETE completed → THAWING → READY_FOR_SALE
    - MOVE_TO_DISPLAY completed → READY_FOR_SALE → ON_DISPLAY
    - REFREEZE completed → ON_DISPLAY → REFREEZE_PENDING
    
    Args:
        task: WorkerTask instance
        actor: User completing the task
        notes: Optional notes
        
    Returns:
        dict with 'task' and 'transitions' (list of state changes made)
    """
    if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]:
        raise ValueError(f"Task is already {task.status}")
    
    task.status = TaskStatus.COMPLETED
    task.completed_at = timezone.now()
    task.completed_by = actor
    task.notes = notes
    task.save(update_fields=['status', 'completed_at', 'completed_by', 'notes', 'updated_at'])
    
    # Create task event
    TaskEvent.objects.create(
        task=task,
        event_type='TASK_COMPLETED',
        timestamp=timezone.now(),
        actor=actor,
        notes=notes
    )
    
    # Auto-transition package state based on task type
    transitions = _auto_transition_on_task_complete(task, actor)
    
    return {'task': task, 'transitions': transitions}


def _auto_transition_on_task_complete(task, actor):
    """
    Auto-transition package state when a worker task is completed.
    
    Returns list of transitions made: [('FROM_STATE', 'TO_STATE'), ...]
    """
    from common.state_machine import transition_package, InvalidTransitionError, TransitionValidationError
    from planning.models import ThawQueueEntry, QueueStatus
    
    package = task.package
    transitions = []
    
    if task.task_type == TaskType.FREEZE_START:
        # PACKED → FREEZING
        if package.current_state == 'PACKED':
            try:
                transition_package(package, 'FREEZING', actor=actor,
                                  reason='FREEZE_START task completed')
                transitions.append(('PACKED', 'FREEZING'))
            except (InvalidTransitionError, TransitionValidationError):
                pass
            
            # Check if freeze schedule says freeze should be complete by now
            if (task.rotation_plan and
                    task.rotation_plan.planned_freeze_end_at and
                    task.rotation_plan.planned_freeze_end_at <= timezone.now()):
                try:
                    # Refresh from DB
                    package.refresh_from_db()
                    transition_package(package, 'FROZEN', actor=actor,
                                      reason='Freeze schedule complete')
                    transitions.append(('FREEZING', 'FROZEN'))
                except (InvalidTransitionError, TransitionValidationError):
                    pass
    
    elif task.task_type == TaskType.FREEZE_CHECK:
        # Check if freeze is complete based on schedule
        if (package.current_state == 'FREEZING' and task.rotation_plan and
                task.rotation_plan.planned_freeze_end_at and
                task.rotation_plan.planned_freeze_end_at <= timezone.now()):
            try:
                transition_package(package, 'FROZEN', actor=actor,
                                  reason='FREEZE_CHECK confirmed freeze complete')
                transitions.append(('FREEZING', 'FROZEN'))
            except (InvalidTransitionError, TransitionValidationError):
                pass
    
    elif task.task_type == TaskType.MOVE_TO_THAW_QUEUE:
        # FROZEN → READY_FOR_THAW → THAW_QUEUED
        if package.current_state == 'FROZEN':
            try:
                transition_package(package, 'READY_FOR_THAW', actor=actor,
                                  reason='MOVE_TO_THAW_QUEUE task completed')
                transitions.append(('FROZEN', 'READY_FOR_THAW'))
            except (InvalidTransitionError, TransitionValidationError):
                pass
            try:
                package.refresh_from_db()
                transition_package(package, 'THAW_QUEUED', actor=actor,
                                  reason='Queued for thaw')
                transitions.append(('READY_FOR_THAW', 'THAW_QUEUED'))
            except (InvalidTransitionError, TransitionValidationError):
                pass
    
    elif task.task_type == TaskType.THAW_START:
        # THAW_QUEUED → THAWING
        if package.current_state == 'THAW_QUEUED':
            try:
                transition_package(package, 'THAWING', actor=actor,
                                  reason='THAW_START task completed')
                transitions.append(('THAW_QUEUED', 'THAWING'))
            except (InvalidTransitionError, TransitionValidationError):
                pass
    
    elif task.task_type == TaskType.THAW_COMPLETE:
        # THAWING → READY_FOR_SALE
        if package.current_state == 'THAWING':
            # Mark queue entry as completed
            queue_entry = ThawQueueEntry.objects.filter(
                package=package,
                status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
            ).first()
            if queue_entry:
                queue_entry.status = QueueStatus.COMPLETED
                queue_entry.save(update_fields=['status', 'updated_at'])
            try:
                transition_package(package, 'READY_FOR_SALE', actor=actor,
                                  reason='THAW_COMPLETE task completed')
                transitions.append(('THAWING', 'READY_FOR_SALE'))
            except (InvalidTransitionError, TransitionValidationError):
                pass
    
    elif task.task_type == TaskType.MOVE_TO_DISPLAY:
        # READY_FOR_SALE → ON_DISPLAY
        if package.current_state == 'READY_FOR_SALE':
            try:
                transition_package(package, 'ON_DISPLAY', actor=actor,
                                  reason='MOVE_TO_DISPLAY task completed')
                transitions.append(('READY_FOR_SALE', 'ON_DISPLAY'))
            except (InvalidTransitionError, TransitionValidationError):
                pass
    
    elif task.task_type == TaskType.REFREEZE:
        # ON_DISPLAY → REFREEZE_PENDING
        if package.current_state == 'ON_DISPLAY':
            try:
                transition_package(package, 'REFREEZE_PENDING', actor=actor,
                                  reason='REFREEZE task completed')
                transitions.append(('ON_DISPLAY', 'REFREEZE_PENDING'))
            except (InvalidTransitionError, TransitionValidationError):
                pass
    
    return transitions


def get_todays_tasks():
    """Get all tasks scheduled for today (Bangkok time)."""
    bangkok_today = timezone.localtime(timezone.now()).date()
    # Use range filter to correctly match Bangkok date
    start = timezone.make_aware(datetime.combine(bangkok_today, datetime.min.time()))
    end = start + timedelta(days=1)
    return WorkerTask.objects.filter(
        scheduled_at__gte=start,
        scheduled_at__lt=end,
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('scheduled_at')


def get_overdue_tasks():
    """Get all overdue tasks."""
    return WorkerTask.objects.filter(
        scheduled_at__lt=timezone.now(),
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('scheduled_at')


def check_capacity(location_type):
    """
    Check available capacity for a location type.
    
    Args:
        location_type: Type of location (FREEZER, THAW_AREA, etc.)
        
    Returns:
        int: Available capacity
    """
    from inventory.models import StorageLocation
    
    location = StorageLocation.objects.filter(
        location_type=location_type,
        active=True
    ).first()
    
    if not location:
        return 0
    
    return location.available_capacity


def update_task_status():
    """
    Update task statuses based on current time.
    
    Marks overdue tasks as OVERDUE.
    """
    now = timezone.now()
    
    # Mark pending/in-progress tasks as overdue
    WorkerTask.objects.filter(
        scheduled_at__lt=now - timedelta(minutes=30),
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    ).update(status=TaskStatus.OVERDUE)


def get_task_history(days=7):
    """Get task history for the last N days."""
    start_date = timezone.now() - timedelta(days=days)
    
    return WorkerTask.objects.filter(
        created_at__gte=start_date,
        status__in=[TaskStatus.COMPLETED, TaskStatus.OVERDUE, TaskStatus.CANCELLED]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('-completed_at')
