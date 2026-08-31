"""
Planning Services — rotation plan lifecycle.

Core: create plan, generate tasks, manage queue, cancel.
Duration calculation: profile-based AUTO mode + CUSTOM overrides.

Phase 04: freeze/thaw lifecycle services.
All lifecycle mutations are atomic and create audit events.

══════════════════════════════════════════════════════════════
LIFECYCLE
══════════════════════════════════════════════════════════════

  PACKED → start_freeze → FREEZING
  FREEZING → complete_freeze → FROZEN
  FROZEN → add_to_thaw_queue → READY_FOR_THAW → THAW_QUEUED
  THAW_QUEUED → start_thaw → THAWING
  THAWING → complete_thaw → READY_FOR_SALE
  READY_FOR_SALE → move_to_display → ON_DISPLAY
  ON_DISPLAY → request_refreeze → REFREEZE_PENDING
  REFREEZE_PENDING → start_refreeze → FREEZING (new cycle)
  ON_DISPLAY → sell / process → COMPLETED
  ON_DISPLAY → discard → COMPLETED

Cancellation:
  THAW_QUEUED → remove_from_thaw_queue → PACKED
"""
from django.db import transaction, IntegrityError
from django.db.models import Max
from django.utils import timezone
from datetime import timedelta

from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, RotationCycle, PlanStatus,
    ThawQueueEntry, QueueStatus, CapacityLock,
)
from inventory.models import Package, PackageState

# ── Stable queue ordering rule ──
# Used by: add_to_thaw_queue, _recalculate_queue_positions, queue listing.
# Guarantees deterministic order even when planned_start_at is identical.
QUEUE_ORDERING = ['planned_start_at', 'created_at', 'pk']


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

    Locks the Package row to serialize concurrent plan creation for the same
    package.  Two concurrent calls for the same package will see the same
    state — exactly one will find no active plan and proceed.
    """
    # Lock package row to serialize concurrent plan-creation for same package.
    # Acquire the lock, then refresh the original parameter so the caller
    # sees the latest state (important when called from _thaw_queued_pkg etc.).
    Package.objects.select_for_update().get(pk=package.pk)
    package.refresh_from_db()

    if package.current_state not in (PackageState.PACKED, PackageState.FROZEN):
        raise ValueError(f"Package must be PACKED or FROZEN, got {package.current_state}")
    # Allow new plan only if no active plan exists for this package
    active_plans = RotationPlan.objects.filter(
        package=package,
        status__in=[PlanStatus.PLANNED, PlanStatus.READY, PlanStatus.IN_PROGRESS]
    )
    if active_plans.exists():
        raise ValueError("Package already has an active rotation plan.")

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

    # Find or create rotation cycle
    cycle = _get_or_create_cycle(package)

    plan = RotationPlan.objects.create(
        package=package,
        rotation_cycle=cycle,
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
    Add a package to the thaw queue with serialized capacity admission.

    Lock protocol (two-phase lock — Package first, then CapacityLock):
        1. Lock Package row (SELECT FOR UPDATE) — serializes per-package checks
        2. Refresh and verify: package is FROZEN, not already in queue
        3. Acquire CapacityLock for the profile — serializes capacity admission
        4. Re-check interval overlaps inside capacity lock
        5. Create queue entry + transition package
        6. Release all locks on COMMIT / rollback on any failure

    Raises:
        ValueError: If preconditions fail or capacity exceeded
    """
    from common.state_machine import (
        transition_package, can_transition,
    )

    if rotation_plan is None:
        raise ValueError("rotation_plan is required")

    profile = rotation_plan.thaw_profile

    # ── STEP 1: Lock Package row (serializes per-package identity checks) ──
    #  Acquire the row lock, then refresh the ORIGINAL package parameter
    #  so the caller's reference also sees the latest state.
    Package.objects.select_for_update().get(pk=package.pk)
    package.refresh_from_db()  # updates the object the caller also references

    # ── STEP 2: Verify preconditions UNDER package lock ──
    if package.current_state != PackageState.FROZEN:
        raise ValueError(f"Must be FROZEN, got {package.current_state}")
    if ThawQueueEntry.objects.filter(
        package=package,
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
    ).exists():
        raise ValueError("Already in thaw queue")

    # ── STEP 3: Acquire capacity lock (serializes all admissions for this profile) ──
    lock = _acquire_capacity_lock(profile)

    # ── STEP 4: Re-check interval overlaps INSIDE the capacity lock ──
    new_start = rotation_plan.planned_thaw_start_at
    new_end = rotation_plan.target_ready_at
    overlaps = check_thaw_interval_overlap(profile, new_start, new_end, exclude_package=package)
    if len(overlaps) >= profile.thaw_capacity:
        raise ValueError(
            f"Thaw capacity exceeded: {len(overlaps)}/{profile.thaw_capacity} "
            f"slots occupied during [{new_start} — {new_end}]"
        )

    # ── STEP 5: Transition FROZEN → READY_FOR_THAW ──
    if can_transition(package.current_state, 'READY_FOR_THAW'):
        transition_package(package, 'READY_FOR_THAW', actor=actor)

    # ── STEP 6: Create entry + recalculate positions ──
    entry = ThawQueueEntry.objects.create(
        package=package, rotation_plan=rotation_plan,
        rotation_cycle=rotation_plan.rotation_cycle,
        queue_position=0,  # placeholder — recalculated immediately below
        planned_start_at=rotation_plan.planned_thaw_start_at,
        target_ready_at=rotation_plan.target_ready_at,
        status=QueueStatus.QUEUED,
    )

    _recalculate_queue_positions(profile=profile)
    entry.refresh_from_db()

    # ── STEP 7: Transition READY_FOR_THAW → THAW_QUEUED ──
    transition_package(package, 'THAW_QUEUED', actor=actor)
    return entry


# ── Cancel queue entry ──

@transaction.atomic
def remove_from_thaw_queue(entry, actor='', reason=''):
    """
    Cancel a thaw queue entry and transition the package back to PACKED.

    Serializes through the same CapacityLock as add_to_thaw_queue:
        lock → validate → cancel → transition → recalculate → commit

    All steps happen in one transaction.
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

    # ── Acquire capacity lock (serializes add vs cancel for this profile) ──
    profile = entry.rotation_plan.thaw_profile
    _acquire_capacity_lock(profile)

    # Re-read entry under lock — a concurrent mutation may have changed it
    entry.refresh_from_db()
    if entry.status not in [QueueStatus.QUEUED, QueueStatus.READY_TO_START]:
        raise ValueError(
            f"Cannot cancel queue entry: status changed to {entry.status}"
        )

    # Step 1: Mark queue entry CANCELLED
    entry.status = QueueStatus.CANCELLED
    entry.save(update_fields=['status', 'updated_at'])

    # Step 2: Transition package THAW_QUEUED → PACKED
    transition_package(package, 'PACKED', actor=actor,
                      reason=reason or 'Cancelled from thaw queue')

    # Step 3: Recalculate active queue positions (scoped to profile)
    _recalculate_queue_positions(profile=profile)

    return entry


def _acquire_capacity_lock(profile):
    """
    Concurrency-safe CapacityLock acquisition.

    The first-ever admission for a ThawProfile must create the lock row.
    Two concurrent first-use requests must not raise an unhandled
    IntegrityError.  The approach:

      1. Create an explicit savepoint before get_or_create
      2. On IntegrityError → rollback to savepoint (recovers connection)
      3. Re-fetch the existing row
      4. SELECT FOR UPDATE on the (now-existing) row

    The final lock row is always the same single row per profile.
    PostgreSQL re-entrant SELECT FOR UPDATE on the same row in the
    same transaction succeeds immediately — safe for nested callers.
    """
    sid = transaction.savepoint()
    try:
        lock, _ = CapacityLock.objects.get_or_create(thaw_profile=profile)
    except IntegrityError:
        # Another thread won the creation race.
        # Rollback to our savepoint to recover the connection from
        # NEEDS_ROLLBACK state, then re-fetch the existing row.
        transaction.savepoint_rollback(sid)
        lock = CapacityLock.objects.get(thaw_profile=profile)

    # Acquire row-level lock (serializes all concurrent queue mutations)
    lock = CapacityLock.objects.select_for_update().get(pk=lock.pk)
    return lock


def _recalculate_queue_positions(profile=None):
    """
    Recalculate queue positions for active entries.

    Scope: PER PROFILE (business rule — each ThawProfile is a distinct
    thaw resource with its own queue ordering).

    Positions are assigned sequentially by planned_start_at within each
    profile.  Cancelled/completed entries are excluded.

    Args:
        profile: If provided, recalculate only for this profile.
                 If None, recalculate all profiles (used during cancellation
                 which already runs under the capacity lock for the entry's profile).
    """
    if profile is not None:
        active_entries = ThawQueueEntry.objects.filter(
            rotation_plan__thaw_profile=profile,
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
        ).order_by(*QUEUE_ORDERING)

        for idx, entry in enumerate(active_entries, start=1):
            if entry.queue_position != idx:
                entry.queue_position = idx
                entry.save(update_fields=['queue_position'])
    else:
        # Recalculate all profiles — group by profile, recalculate each
        from django.db.models import Q
        profiles_with_active = (
            ThawQueueEntry.objects.filter(
                status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
            ).values_list('rotation_plan__thaw_profile_id', flat=True).distinct()
        )
        for pid in profiles_with_active:
            profile_obj = ThawProfile.objects.get(pk=pid)
            _recalculate_queue_positions(profile=profile_obj)


# ── Cancel ──

def _cancel_queue_entries_for_plan(plan, actor='', reason=''):
    """
    Cancel all active queue entries for a plan WITHOUT its own transaction.

    Must be called within an existing transaction.atomic block.
    Acquires the CapacityLock for the plan's profile to serialize
    against concurrent add_to_thaw_queue() calls.
    If any entry fails to cancel, the entire outer transaction rolls back.
    """
    from common.state_machine import transition_package

    profile = plan.thaw_profile

    # ── Acquire capacity lock (serializes against concurrent add/cancel) ──
    _acquire_capacity_lock(profile)

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
        _recalculate_queue_positions(profile=profile)


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


def check_thaw_interval_overlap(profile, new_start, new_end, exclude_package=None):
    """
    Check if a new thaw interval overlaps with active thaw operations
    within the same ThawProfile.

    Business rule: each ThawProfile is a separate thaw resource with
    independent capacity.  Capacity checking must be scoped to one profile.

    Args:
        profile: ThawProfile — entries from other profiles are excluded
        new_start: Planned thaw start time
        new_end: Target ready time
        exclude_package: Package to exclude from check

    Returns:
        list: Overlapping entries within this profile (empty if no conflict)
    """
    active = ThawQueueEntry.objects.filter(
        rotation_plan__thaw_profile=profile,
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED],
    )
    if exclude_package:
        active = active.exclude(package=exclude_package)

    overlaps = []
    for entry in active:
        if check_interval_overlap(new_start, new_end, entry.planned_start_at, entry.target_ready_at):
            overlaps.append(entry)
    return overlaps


# ============================================================
# ROTATION CYCLE HELPERS
# ============================================================

def _get_or_create_cycle(package):
    """Get the current IN_PROGRESS cycle or create a new one.

    Each package starts with cycle_number=1.
    A new cycle is created when a refreeze occurs.
    """
    from planning.models import RotationCycle

    cycle = RotationCycle.objects.filter(
        package=package, status='IN_PROGRESS'
    ).order_by('-cycle_number').first()

    if cycle is None:
        last_num = RotationCycle.objects.filter(
            package=package
        ).order_by('-cycle_number').values_list('cycle_number', flat=True).first() or 0
        cycle = RotationCycle.objects.create(
            package=package,
            cycle_number=last_num + 1,
            status='IN_PROGRESS',
        )
    return cycle


def _complete_cycle(cycle, outcome, actor='', reason=''):
    """Mark a rotation cycle as completed with an outcome."""
    cycle.status = 'COMPLETED'
    cycle.outcome = outcome
    cycle.outcome_reason = reason
    cycle.outcome_actor = actor
    cycle.outcome_at = timezone.now()
    cycle.save(update_fields=[
        'status', 'outcome', 'outcome_reason',
        'outcome_actor', 'outcome_at', 'updated_at',
    ])


# ============================================================
# FREEZE LIFECYCLE SERVICES
# ============================================================

@transaction.atomic
def start_freeze(package, actor='', reason=''):
    """Start freezing a package: PACKED -> FREEZING.

    Creates or updates the RotationCycle freeze_started_at timestamp.
    Package must be in PACKED state.

    Args:
        package: Package in PACKED state
        actor: who started the freeze
        reason: why

    Returns:
        tuple: (package, cycle)

    Raises:
        ValueError: if package is not in PACKED state
    """
    from common.state_machine import transition_package
    package = Package.objects.select_for_update().get(pk=package.pk)

    if package.current_state != PackageState.PACKED:
        raise ValueError(
            f"Cannot start freeze: package is {package.current_state}, "
            f"must be PACKED"
        )

    transition_package(package, 'FREEZING', actor=actor,
                      reason=reason or 'Freeze started')

    cycle = _get_or_create_cycle(package)
    cycle.freeze_started_at = timezone.now()
    cycle.save(update_fields=['freeze_started_at', 'updated_at'])

    return package, cycle


@transaction.atomic
def complete_freeze(package, actor='', reason=''):
    """Complete freezing: FREEZING -> FROZEN.

    Updates RotationCycle freeze_completed_at.
    Package must be in FREEZING state.

    Args:
        package: Package in FREEZING state
        actor: who completed the freeze
        reason: why

    Returns:
        tuple: (package, cycle)
    """
    from common.state_machine import transition_package
    package = Package.objects.select_for_update().get(pk=package.pk)

    if package.current_state != PackageState.FREEZING:
        raise ValueError(
            f"Cannot complete freeze: package is {package.current_state}, "
            f"must be FREEZING"
        )

    transition_package(package, 'FROZEN', actor=actor,
                      reason=reason or 'Freeze completed')

    cycle = RotationCycle.objects.filter(
        package=package, status='IN_PROGRESS'
    ).order_by('-cycle_number').first()
    if cycle:
        cycle.freeze_completed_at = timezone.now()
        cycle.save(update_fields=['freeze_completed_at', 'updated_at'])

    return package, cycle


# ============================================================
# THAW LIFECYCLE SERVICES
# ============================================================

@transaction.atomic
def start_thaw(package, actor='', reason=''):
    """Start thawing: THAW_QUEUED -> THAWING.

    Updates RotationCycle thaw_started_at.
    Updates ThawQueueEntry status to STARTED.

    Args:
        package: Package in THAW_QUEUED state
        actor: who started the thaw
        reason: why

    Returns:
        tuple: (package, cycle)
    """
    from common.state_machine import transition_package
    package = Package.objects.select_for_update().get(pk=package.pk)

    if package.current_state != PackageState.THAW_QUEUED:
        raise ValueError(
            f"Cannot start thaw: package is {package.current_state}, "
            f"must be THAW_QUEUED"
        )

    transition_package(package, 'THAWING', actor=actor,
                      reason=reason or 'Thaw started')

    # Update queue entry status
    ThawQueueEntry.objects.filter(
        package=package,
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
    ).update(status=QueueStatus.STARTED)

    cycle = RotationCycle.objects.filter(
        package=package, status='IN_PROGRESS'
    ).order_by('-cycle_number').first()
    if cycle:
        cycle.thaw_started_at = timezone.now()
        cycle.save(update_fields=['thaw_started_at', 'updated_at'])

    return package, cycle


@transaction.atomic
def complete_thaw(package, actor='', reason=''):
    """Complete thawing: THAWING -> READY_FOR_SALE.

    Updates RotationCycle thaw_completed_at.
    Marks queue entry COMPLETED.

    Args:
        package: Package in THAWING state
        actor: who completed the thaw
        reason: why

    Returns:
        tuple: (package, cycle)
    """
    from common.state_machine import transition_package
    package = Package.objects.select_for_update().get(pk=package.pk)

    if package.current_state != PackageState.THAWING:
        raise ValueError(
            f"Cannot complete thaw: package is {package.current_state}, "
            f"must be THAWING"
        )

    # Mark queue entry COMPLETED first (state machine validates this for READY_FOR_SALE)
    ThawQueueEntry.objects.filter(
        package=package,
        status=QueueStatus.STARTED
    ).update(status=QueueStatus.COMPLETED)

    transition_package(package, 'READY_FOR_SALE', actor=actor,
                      reason=reason or 'Thaw completed')

    cycle = RotationCycle.objects.filter(
        package=package, status='IN_PROGRESS'
    ).order_by('-cycle_number').first()
    if cycle:
        cycle.thaw_completed_at = timezone.now()
        cycle.save(update_fields=['thaw_completed_at', 'updated_at'])

    return package, cycle


# ============================================================
# DISPLAY LIFECYCLE SERVICES
# ============================================================

@transaction.atomic
def move_to_display(package, actor='', reason=''):
    """Move to display: READY_FOR_SALE -> ON_DISPLAY.

    Updates RotationCycle display_started_at.

    Args:
        package: Package in READY_FOR_SALE state
        actor: who moved it
        reason: why

    Returns:
        tuple: (package, cycle)
    """
    from common.state_machine import transition_package
    package = Package.objects.select_for_update().get(pk=package.pk)

    if package.current_state != PackageState.READY_FOR_SALE:
        raise ValueError(
            f"Cannot move to display: package is {package.current_state}, "
            f"must be READY_FOR_SALE"
        )

    transition_package(package, 'ON_DISPLAY', actor=actor,
                      reason=reason or 'Moved to display')

    cycle = RotationCycle.objects.filter(
        package=package, status='IN_PROGRESS'
    ).order_by('-cycle_number').first()
    if cycle:
        cycle.display_started_at = timezone.now()
        cycle.save(update_fields=['display_started_at', 'updated_at'])

    return package, cycle


# ============================================================
# REFREEZE LIFECYCLE SERVICES
# ============================================================

@transaction.atomic
def request_refreeze(package, actor='', reason=''):
    """Request refreeze: ON_DISPLAY -> REFREEZE_PENDING.

    Records display_ended_at on the current cycle.

    Args:
        package: Package in ON_DISPLAY state
        actor: who requested refreeze
        reason: why

    Returns:
        tuple: (package, cycle)
    """
    from common.state_machine import transition_package
    package = Package.objects.select_for_update().get(pk=package.pk)

    if package.current_state != PackageState.ON_DISPLAY:
        raise ValueError(
            f"Cannot request refreeze: package is {package.current_state}, "
            f"must be ON_DISPLAY"
        )

    transition_package(package, 'REFREEZE_PENDING', actor=actor,
                      reason=reason or 'Refreeze requested')

    cycle = RotationCycle.objects.filter(
        package=package, status='IN_PROGRESS'
    ).order_by('-cycle_number').first()
    if cycle:
        cycle.display_ended_at = timezone.now()
        cycle.save(update_fields=['display_ended_at', 'updated_at'])

    return package, cycle


@transaction.atomic
def start_refreeze(package, actor='', reason=''):
    """Start refreeze: REFREEZE_PENDING -> FREEZING.

    Completes the current cycle as REFROZEN and creates a new cycle.
    History is append-only: old cycle timestamps are preserved.

    Args:
        package: Package in REFREEZE_PENDING state
        actor: who started the refreeze
        reason: why

    Returns:
        tuple: (package, new_cycle)
    """
    from common.state_machine import transition_package
    from planning.models import RotationCycle

    package = Package.objects.select_for_update().get(pk=package.pk)

    if package.current_state != PackageState.REFREEZE_PENDING:
        raise ValueError(
            f"Cannot start refreeze: package is {package.current_state}, "
            f"must be REFREEZE_PENDING"
        )

    # Complete current cycle as REFROZEN
    old_cycle = RotationCycle.objects.filter(
        package=package, status='IN_PROGRESS'
    ).order_by('-cycle_number').first()
    if old_cycle:
        _complete_cycle(old_cycle, 'REFROZEN', actor=actor, reason=reason)

    # Mark old rotation plan as COMPLETED so a new plan can be created
    RotationPlan.objects.filter(
        package=package,
        status__in=[PlanStatus.PLANNED, PlanStatus.READY, PlanStatus.IN_PROGRESS]
    ).update(status=PlanStatus.COMPLETED)

    transition_package(package, 'FREEZING', actor=actor,
                      reason=reason or 'Refreeze started')

    # Create new cycle
    new_cycle = _get_or_create_cycle(package)
    new_cycle.freeze_started_at = timezone.now()
    new_cycle.save(update_fields=['freeze_started_at', 'updated_at'])

    return package, new_cycle


# ============================================================
# COMPLETION SERVICES (sell/discard from any valid state)
# ============================================================

@transaction.atomic
def complete_sale(package, actor='', reason=''):
    """Complete sale: ON_DISPLAY -> PROCESSING -> COMPLETED.

    Marks the current cycle as SOLD.
    """
    from inventory.services import sell_package

    # sell_package handles state transitions and audit
    sell_package(package, actor=actor, reason=reason)

    # Complete cycle
    cycle = RotationCycle.objects.filter(
        package=package, status='IN_PROGRESS'
    ).order_by('-cycle_number').first()
    if cycle:
        _complete_cycle(cycle, 'SOLD', actor=actor, reason=reason)

    return package


@transaction.atomic
def complete_discard(package, actor='', reason=''):
    """Complete discard: ON_DISPLAY -> DISCARDED -> COMPLETED.

    Marks the current cycle as DISCARDED.
    """
    from inventory.services import discard_package

    discard_package(package, actor=actor, reason=reason)

    cycle = RotationCycle.objects.filter(
        package=package, status='IN_PROGRESS'
    ).order_by('-cycle_number').first()
    if cycle:
        _complete_cycle(cycle, 'DISCARDED', actor=actor, reason=reason)

    return package
