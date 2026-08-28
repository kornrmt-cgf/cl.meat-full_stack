"""
Planning Business Logic Services.

All calculation logic is centralized here.
"""
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
from .models import (
    FreezeProfile, ThawProfile, RotationPlan, PlanStatus,
    ThawQueueEntry, QueueStatus
)
from inventory.models import Package, PackageState
from common.time_service import now


def calculate_freeze_duration(package, freeze_profile):
    """
    Calculate freeze duration based on package weight and profile.
    
    Args:
        package: Package instance
        freeze_profile: FreezeProfile instance
        
    Returns:
        timedelta: Calculated freeze duration
    """
    weight_kg = float(package.weight)
    
    if weight_kg <= 0.5:
        duration = freeze_profile.minimum_duration
    elif weight_kg <= 1.0:
        duration = freeze_profile.default_duration
    else:
        # Large packages: add 20% buffer to default
        secs = int(freeze_profile.default_duration.total_seconds() * 1.2)
        duration = timedelta(seconds=secs)
    
    duration += freeze_profile.buffer_duration
    return duration


def calculate_thaw_duration(package, thaw_profile):
    """
    Calculate thaw duration based on package weight and profile.
    
    Delegates to thaw_service for configurable calculation.
    
    Args:
        package: Package instance
        thaw_profile: ThawProfile instance
        
    Returns:
        timedelta: Calculated thaw duration
    """
    from .thaw_service import calculate_thaw_duration as _calc
    return _calc(package, thaw_profile)


def calculate_rotation_plan(package, target_ready_at, freeze_profile, thaw_profile):
    """
    Calculate rotation plan timings from target ready time.
    
    This is the CORE PLANNING CALCULATION.
    
    Args:
        package: Package instance
        target_ready_at: datetime when package should be ready
        freeze_profile: FreezeProfile to use
        thaw_profile: ThawProfile to use
        
    Returns:
        dict: Calculated plan timings
    """
    # Calculate durations
    freeze_duration = calculate_freeze_duration(package, freeze_profile)
    thaw_duration = calculate_thaw_duration(package, thaw_profile)
    
    # Calculate backwards from target_ready_at
    # Ready → Thaw complete → Thaw start → Queue → Freeze complete → Freeze start
    
    thaw_start_at = target_ready_at - thaw_duration
    thaw_queue_at = thaw_start_at - timedelta(minutes=30)  # Queue 30 min before thaw
    freeze_end_at = thaw_start_at - timedelta(minutes=15)  # Freeze ends 15 min before thaw
    freeze_start_at = freeze_end_at - freeze_duration
    
    return {
        'target_ready_at': target_ready_at,
        'planned_thaw_start_at': thaw_start_at,
        'planned_thaw_queue_at': thaw_queue_at,
        'planned_freeze_start_at': freeze_start_at,
        'planned_freeze_end_at': freeze_end_at,
        'freeze_duration': freeze_duration,
        'thaw_duration': thaw_duration,
    }


@transaction.atomic
def create_rotation_plan(package, target_ready_at, freeze_profile, thaw_profile, actor=''):
    """
    Create a rotation plan for a package.
    
    Args:
        package: Package instance (must be FROZEN)
        target_ready_at: datetime when package should be ready
        freeze_profile: FreezeProfile to use
        thaw_profile: ThawProfile to use
        actor: User creating the plan
        
    Returns:
        RotationPlan instance
        
    Raises:
        ValueError: If validation fails
    """
    # Validate package state (PACKED or FROZEN eligible)
    if package.current_state not in [PackageState.PACKED, PackageState.FROZEN]:
        raise ValueError(
            f"Package must be PACKED or FROZEN to create rotation plan. "
            f"Current state: {package.current_state}"
        )
    
    # Check if package already has a plan
    if RotationPlan.objects.filter(package=package).exists():
        raise ValueError(
            "Package already has a rotation plan. "
            "Cancel existing plan first."
        )
    
    # Calculate plan
    plan_data = calculate_rotation_plan(package, target_ready_at, freeze_profile, thaw_profile)
    
    # Validate calculated times are in the future
    if plan_data['planned_freeze_start_at'] <= now():
        raise ValueError(
            "Calculated freeze start time is in the past. "
            "Target ready time is too soon for current configuration."
        )
    
    # Create plan
    plan = RotationPlan.objects.create(
        package=package,
        target_ready_at=plan_data['target_ready_at'],
        planned_thaw_start_at=plan_data['planned_thaw_start_at'],
        planned_thaw_queue_at=plan_data['planned_thaw_queue_at'],
        planned_freeze_start_at=plan_data['planned_freeze_start_at'],
        planned_freeze_end_at=plan_data['planned_freeze_end_at'],
        freeze_profile=freeze_profile,
        thaw_profile=thaw_profile,
        freeze_duration=plan_data['freeze_duration'],
        thaw_duration=plan_data['thaw_duration'],
        status=PlanStatus.PLANNED
    )
    
    # Generate worker tasks
    generate_worker_tasks(plan)
    
    return plan


@transaction.atomic
def add_to_thaw_queue(package, rotation_plan, actor=''):
    """
    Add a package to the thaw queue.
    
    This transitions the package through:
    FROZEN -> READY_FOR_THAW -> THAW_QUEUED
    
    Args:
        package: Package instance
        rotation_plan: RotationPlan instance
        actor: User adding to queue
        
    Returns:
        ThawQueueEntry instance
        
    Raises:
        ValueError: If validation fails
    """
    from common.state_machine import transition_package, TransitionValidationError
    
    # Validate package is frozen
    if package.current_state != PackageState.FROZEN:
        raise ValueError(
            f"Package must be FROZEN to add to thaw queue. "
            f"Current state: {package.current_state}"
        )
    
    # Check if already in queue
    if ThawQueueEntry.objects.filter(
        package=package,
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
    ).exists():
        raise ValueError("Package is already in the thaw queue")
    
    # Transition FROZEN -> READY_FOR_THAW
    try:
        transition_package(package, 'READY_FOR_THAW', actor=actor, reason='Added to thaw queue')
    except TransitionValidationError:
        pass  # If transition validation fails for other reasons, continue
    
    # Calculate next queue position
    max_position = ThawQueueEntry.objects.filter(
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
    ).aggregate(Max('queue_position'))['queue_position__max'] or 0
    
    next_position = max_position + 1
    
    # Create queue entry
    entry = ThawQueueEntry.objects.create(
        package=package,
        rotation_plan=rotation_plan,
        queue_position=next_position,
        planned_start_at=rotation_plan.planned_thaw_start_at,
        target_ready_at=rotation_plan.target_ready_at,
        status=QueueStatus.QUEUED
    )
    
    # Transition READY_FOR_THAW -> THAW_QUEUED
    try:
        transition_package(package, 'THAW_QUEUED', actor=actor, reason='Queued for thaw')
    except TransitionValidationError:
        pass
    
    return entry


@transaction.atomic
def remove_from_thaw_queue(entry, actor=''):
    """
    Remove a package from the thaw queue.
    
    Args:
        entry: ThawQueueEntry instance
        actor: User removing from queue
        
    Raises:
        ValueError: If entry cannot be removed
    """
    if entry.status not in [QueueStatus.QUEUED, QueueStatus.READY_TO_START]:
        raise ValueError(
            f"Cannot remove from queue: entry status is {entry.status}"
        )
    
    entry.status = QueueStatus.CANCELLED
    entry.save(update_fields=['status', 'updated_at'])
    
    # Recalculate queue positions
    recalculate_queue()


def recalculate_queue():
    """
    Recalculate queue positions after removal.
    """
    from django.db.models import F
    
    active_entries = ThawQueueEntry.objects.filter(
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
    ).order_by('planned_start_at')
    
    for idx, entry in enumerate(active_entries, start=1):
        entry.queue_position = idx
        entry.save(update_fields=['queue_position'])


def check_conflicts(target_ready_at, exclude_package=None, thaw_profile=None):
    """
    Check for scheduling conflicts.
    
    Args:
        target_ready_at: datetime to check
        exclude_package: Package to exclude from check
        thaw_profile: ThawProfile for capacity check (optional)
        
    Returns:
        list: List of conflict messages
    """
    conflicts = []
    
    # Check thaw capacity using configurable profile
    from .thaw_service import check_thaw_capacity
    capacity = check_thaw_capacity(
        thaw_profile=thaw_profile,
        target_time=target_ready_at
    )
    
    if not capacity['available']:
        conflicts.append(
            f"Thaw capacity exceeded: {capacity['current_count']}/{capacity['max_capacity']} slots used"
        )
    
    return conflicts


def generate_worker_tasks(plan):
    """
    Generate worker tasks from a rotation plan.
    
    Creates operational tasks that workers must execute:
    1. FREEZE_START — when to start freezing
    2. MOVE_TO_THAW_QUEUE — when to queue for thaw
    3. THAW_START — when to start thawing
    4. THAW_COMPLETE — when thaw should be done
    5. MOVE_TO_DISPLAY — when to move to display
    """
    from operations.models import WorkerTask, TaskType, TaskStatus
    
    package = plan.package
    tasks = []
    
    # Task 1: Freeze Start
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=plan,
        task_type=TaskType.FREEZE_START,
        scheduled_at=plan.planned_freeze_start_at,
        status=TaskStatus.PENDING,
    ))
    
    # Task 2: Move to Thaw Queue
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=plan,
        task_type=TaskType.MOVE_TO_THAW_QUEUE,
        scheduled_at=plan.planned_thaw_queue_at,
        status=TaskStatus.PENDING,
    ))
    
    # Task 3: Thaw Start
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=plan,
        task_type=TaskType.THAW_START,
        scheduled_at=plan.planned_thaw_start_at,
        status=TaskStatus.PENDING,
    ))
    
    # Task 4: Thaw Complete
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=plan,
        task_type=TaskType.THAW_COMPLETE,
        scheduled_at=plan.target_ready_at,
        status=TaskStatus.PENDING,
    ))
    
    # Task 5: Move to Display
    tasks.append(WorkerTask(
        package=package,
        rotation_plan=plan,
        task_type=TaskType.MOVE_TO_DISPLAY,
        scheduled_at=plan.target_ready_at,
        status=TaskStatus.PENDING,
    ))
    
    WorkerTask.objects.bulk_create(tasks)
    return tasks
