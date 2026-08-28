"""
Planning Services — rotation plan lifecycle.

Core: create plan, generate tasks, manage queue, cancel.
Duration calculation: profile-based AUTO mode + CUSTOM overrides.
"""
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from datetime import timedelta

from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, PlanStatus,
    ThawQueueEntry, QueueStatus,
)
from inventory.models import Package, PackageState


# ── Duration calculation (AUTO mode) ──

def calculate_freeze_duration(package, profile):
    """Weight-based freeze duration from profile. Buffer always added."""
    w = float(package.weight)
    if w <= 0.5:
        d = profile.minimum_duration
    elif w <= 1.0:
        d = profile.default_duration
    else:
        d = timedelta(seconds=int(profile.default_duration.total_seconds() * 1.2))
    return d + profile.buffer_duration


def calculate_thaw_duration(package, profile):
    """Weight-interpolated thaw duration from profile. Buffer always added.

    At w = t*2 exactly, interpolation gives default_duration.
    Above t*2, scaling gives default_duration * scale_factor.
    This is a known discontinuity: operator should verify scale_factor
    is appropriate for their use case.
    """
    w = float(package.weight)
    t = float(profile.weight_threshold_kg)
    s = float(profile.weight_scale_factor)
    if w <= t:
        d = profile.minimum_duration
    elif w <= t * 2:
        frac = (w - t) / t
        d = timedelta(seconds=int(
            profile.minimum_duration.total_seconds()
            + frac * (profile.default_duration.total_seconds() - profile.minimum_duration.total_seconds())
        ))
    else:
        d = timedelta(seconds=int(profile.default_duration.total_seconds() * s))
    return d + profile.buffer_duration


# ── Plan creation ──

@transaction.atomic
def create_rotation_plan(package, target_ready_at, freeze_profile, thaw_profile,
                         freeze_override=None, thaw_override=None,
                         override_reason='', actor=''):
    """
    Create a rotation plan.  AUTO calculates from profiles;
    CUSTOM uses overrides when provided.
    """
    if package.current_state not in (PackageState.PACKED, PackageState.FROZEN):
        raise ValueError(f"Package must be PACKED or FROZEN, got {package.current_state}")
    if RotationPlan.objects.filter(package=package).exists():
        raise ValueError("Package already has a rotation plan.")

    freeze_dur = calculate_freeze_duration(package, freeze_profile)
    thaw_dur = calculate_thaw_duration(package, thaw_profile)
    is_override = False
    if freeze_override:
        freeze_dur = freeze_override
        is_override = True
    if thaw_override:
        thaw_dur = thaw_override
        is_override = True

    thaw_start = target_ready_at - thaw_dur
    thaw_queue = thaw_start - timedelta(minutes=30)
    freeze_end = thaw_start - timedelta(minutes=15)
    freeze_start = freeze_end - freeze_dur

    if freeze_start <= timezone.now():
        raise ValueError("Target ready time is too soon — freeze start would be in the past.")

    plan = RotationPlan.objects.create(
        package=package,
        target_ready_at=target_ready_at,
        planned_thaw_start_at=thaw_start,
        planned_thaw_queue_at=thaw_queue,
        planned_freeze_start_at=freeze_start,
        planned_freeze_end_at=freeze_end,
        freeze_profile=freeze_profile,
        thaw_profile=thaw_profile,
        freeze_duration=freeze_dur,
        thaw_duration=thaw_dur,
        freeze_override=freeze_override,
        thaw_override=thaw_override,
        override_reason=override_reason,
        overridden_by=actor if is_override else '',
        overridden_at=timezone.now() if is_override else None,
        status=PlanStatus.PLANNED,
    )
    generate_worker_tasks(plan)
    return plan


# ── Task generation ──

@transaction.atomic
def generate_worker_tasks(plan):
    """Create 7 worker tasks from a rotation plan."""
    from operations.models import WorkerTask, TaskType, TaskStatus

    plan.worker_tasks.all().delete()
    pkg = plan.package
    t = plan.thaw_duration
    tasks = [
        WorkerTask(package=pkg, rotation_plan=plan, task_type=TaskType.FREEZE_START,
                   scheduled_at=plan.planned_freeze_start_at, status=TaskStatus.PENDING),
        WorkerTask(package=pkg, rotation_plan=plan, task_type=TaskType.FREEZE_CHECK,
                   scheduled_at=plan.planned_freeze_start_at + timedelta(hours=2),
                   status=TaskStatus.PENDING),
        WorkerTask(package=pkg, rotation_plan=plan, task_type=TaskType.MOVE_TO_THAW_QUEUE,
                   scheduled_at=plan.planned_thaw_queue_at, status=TaskStatus.PENDING),
        WorkerTask(package=pkg, rotation_plan=plan, task_type=TaskType.THAW_START,
                   scheduled_at=plan.planned_thaw_start_at, status=TaskStatus.PENDING),
        WorkerTask(package=pkg, rotation_plan=plan, task_type=TaskType.THAW_CHECK,
                   scheduled_at=plan.planned_thaw_start_at + (t // 2),
                   status=TaskStatus.PENDING),
        WorkerTask(package=pkg, rotation_plan=plan, task_type=TaskType.THAW_COMPLETE,
                   scheduled_at=plan.target_ready_at, status=TaskStatus.PENDING),
        WorkerTask(package=pkg, rotation_plan=plan, task_type=TaskType.MOVE_TO_DISPLAY,
                   scheduled_at=plan.target_ready_at + timedelta(minutes=15),
                   status=TaskStatus.PENDING),
    ]
    return WorkerTask.objects.bulk_create(tasks)


# ── Thaw queue ──

@transaction.atomic
def add_to_thaw_queue(package, rotation_plan, actor=''):
    """
    Add a package to the thaw queue.

    Both transitions (FROZEN → READY_FOR_THAW → THAW_QUEUED) must succeed.
    If either fails, the entire operation rolls back — no partial queue record.

    Raises:
        ValueError: If preconditions are not met
        InvalidTransitionError: If state transition is not allowed
        TransitionValidationError: If transition validation fails (no plan, etc.)
    """
    from common.state_machine import (
        transition_package, can_transition,
    )

    if rotation_plan is None:
        raise ValueError("rotation_plan is required")
    if package.current_state != PackageState.FROZEN:
        raise ValueError(f"Must be FROZEN, got {package.current_state}")
    if ThawQueueEntry.objects.filter(
        package=package,
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
    ).exists():
        raise ValueError("Already in thaw queue")

    # Transition FROZEN → READY_FOR_THAW
    # Never skip — if this fails, the entire operation must fail.
    if can_transition(package.current_state, 'READY_FOR_THAW'):
        transition_package(package, 'READY_FOR_THAW', actor=actor)

    max_pos = ThawQueueEntry.objects.filter(
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
    ).aggregate(Max('queue_position'))['queue_position__max'] or 0

    entry = ThawQueueEntry.objects.create(
        package=package, rotation_plan=rotation_plan,
        queue_position=max_pos + 1,
        planned_start_at=rotation_plan.planned_thaw_start_at,
        target_ready_at=rotation_plan.target_ready_at,
        status=QueueStatus.QUEUED,
    )

    # Transition READY_FOR_THAW → THAW_QUEUED
    # If this fails, transaction rolls back — queue entry removed too.
    transition_package(package, 'THAW_QUEUED', actor=actor)
    return entry


# ── Cancel queue entry ──

@transaction.atomic
def remove_from_thaw_queue(entry, actor='', reason=''):
    """
    Cancel a thaw queue entry and transition the package back to PACKED.

    All steps happen in one transaction:
    1. Mark queue entry CANCELLED
    2. Transition package THAW_QUEUED → PACKED
    3. Recalculate active queue positions

    If any step fails, the entire operation rolls back.

    Raises:
        ValueError: If entry cannot be cancelled
        InvalidTransitionError: If package state transition fails
    """
    from common.state_machine import transition_package

    if entry.status not in [QueueStatus.QUEUED, QueueStatus.READY_TO_START]:
        raise ValueError(
            f"Cannot cancel queue entry: status is {entry.status}"
        )

    package = entry.package
    if package.current_state != PackageState.THAW_QUEUED:
        raise ValueError(
            f"Package state is {package.current_state}, expected THAW_QUEUED"
        )

    # Step 1: Mark queue entry CANCELLED
    entry.status = QueueStatus.CANCELLED
    entry.save(update_fields=['status', 'updated_at'])

    # Step 2: Transition package THAW_QUEUED → PACKED
    transition_package(package, 'PACKED', actor=actor,
                      reason=reason or 'Cancelled from thaw queue')

    # Step 3: Recalculate active queue positions
    _recalculate_queue_positions()

    return entry


def _recalculate_queue_positions():
    """
    Recalculate queue positions for active entries.

    Positions are assigned sequentially by planned_start_at.
    Cancelled/completed entries are excluded.
    """
    active_entries = ThawQueueEntry.objects.filter(
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
    ).order_by('planned_start_at')

    for idx, entry in enumerate(active_entries, start=1):
        if entry.queue_position != idx:
            entry.queue_position = idx
            entry.save(update_fields=['queue_position'])


# ── Cancel ──

def _cancel_queue_entries_for_plan(plan, actor='', reason=''):
    """
    Cancel all active queue entries for a plan WITHOUT its own transaction.

    Must be called within an existing transaction.atomic block.
    If any entry fails to cancel, the entire outer transaction rolls back.
    """
    from common.state_machine import transition_package

    active_entries = list(ThawQueueEntry.objects.filter(
        rotation_plan=plan,
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
    ))

    for entry in active_entries:
        package = entry.package
        if package.current_state != PackageState.THAW_QUEUED:
            raise ValueError(
                f"Cannot cancel queue entry for {package}: "
                f"state is {package.current_state}, expected THAW_QUEUED"
            )
        entry.status = QueueStatus.CANCELLED
        entry.save(update_fields=['status', 'updated_at'])
        transition_package(package, 'PACKED', actor=actor,
                          reason=reason or 'Plan cancelled')

    if active_entries:
        _recalculate_queue_positions()


@transaction.atomic
def cancel_rotation_plan(plan, actor='', reason=''):
    """
    Cancel a rotation plan and its associated queue entries.

    All cancellations happen in one transaction:
    - If any queue entry fails to cancel, everything rolls back.
    - No partial cancellation is possible.
    """
    from operations.models import TaskStatus

    plan.status = PlanStatus.CANCELLED
    plan.save(update_fields=['status', 'updated_at'])

    # Cancel pending/in-progress worker tasks
    plan.worker_tasks.filter(
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    ).update(status=TaskStatus.CANCELLED)

    # Cancel active queue entries — all-or-nothing within this transaction
    _cancel_queue_entries_for_plan(plan, actor=actor,
                                  reason=reason or 'Plan cancelled')

    from planning.audit import Audit
    Audit.plan_action(plan, 'PLAN_CANCELLED', actor=actor, reason=reason)
    return plan


# ── Profile matching ──

def get_best_thaw_profile(product=None):
    if product:
        code = product.category.code if product.category else ''
        p = ThawProfile.objects.filter(category=code, active=True).first()
        if p:
            return p
    return ThawProfile.objects.filter(category='', active=True).first() or \
           ThawProfile.objects.filter(active=True).first()


def get_best_freeze_profile():
    return FreezeProfile.objects.filter(active=True).first()


# ── Interval overlap detection ──

def check_interval_overlap(start_a, end_a, start_b, end_b):
    """
    Check if two time intervals overlap.

    Uses inclusive-exclusive boundaries: [start, end)
    Two intervals overlap when start_a < end_b AND start_b < end_a.

    Args:
        start_a: Start of interval A
        end_a: End of interval A
        start_b: Start of interval B
        end_b: End of interval B

    Returns:
        bool: True if intervals overlap
    """
    return start_a < end_b and start_b < end_a


def check_thaw_capacity_at_time(profile, target_time, exclude_package=None):
    """
    Check thaw capacity at a specific point in time.

    Counts active thaw queue entries that overlap with the target time.
    An entry overlaps if: entry.planned_start_at <= target_time < entry.target_ready_at

    Args:
        profile: ThawProfile with thaw_capacity
        target_time: datetime to check
        exclude_package: Package to exclude from count

    Returns:
        dict: {available, current_count, max_capacity}
    """
    active_entries = ThawQueueEntry.objects.filter(
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED],
        planned_start_at__lte=target_time,
        target_ready_at__gt=target_time,
    )
    if exclude_package:
        active_entries = active_entries.exclude(package=exclude_package)

    current_count = active_entries.count()
    max_capacity = profile.thaw_capacity

    return {
        'available': current_count < max_capacity,
        'current_count': current_count,
        'max_capacity': max_capacity,
    }


def check_thaw_interval_overlap(new_start, new_end, exclude_package=None):
    """
    Check if a new thaw interval overlaps with any active thaw operations.

    Args:
        new_start: Planned thaw start time
        new_end: Target ready time
        exclude_package: Package to exclude from check

    Returns:
        list: Overlapping entries (empty if no conflict)
    """
    active = ThawQueueEntry.objects.filter(
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED],
    )
    if exclude_package:
        active = active.exclude(package=exclude_package)

    overlaps = []
    for entry in active:
        if check_interval_overlap(new_start, new_end, entry.planned_start_at, entry.target_ready_at):
            overlaps.append(entry)
    return overlaps
