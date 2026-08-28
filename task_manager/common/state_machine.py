"""
Centralized State Machine for Package Lifecycle.

All package state changes MUST go through transition_package().
This is the single source of truth for state transitions.

States:
    PACKED → FREEZING → FROZEN → READY_FOR_THAW → THAW_QUEUED →
    THAWING → READY_FOR_SALE → ON_DISPLAY → {REFREEZE_PENDING, PROCESSING, DISCARDED} →
    COMPLETED

Design Principles:
    - Every transition is validated against the transition table
    - Every transition creates an audit event (RotationEvent)
    - Required schedules/plans are validated before transition
    - Manual overrides are supported via CUSTOM mode
"""
from django.db import transaction
from django.utils import timezone


# ============================================================
# VALID STATE TRANSITIONS TABLE
# ============================================================

TRANSITIONS = {
    'PACKED':           ['FREEZING'],
    'FREEZING':         ['FROZEN'],
    'FROZEN':           ['READY_FOR_THAW'],
    'READY_FOR_THAW':   ['THAW_QUEUED'],
    'THAW_QUEUED':      ['THAWING', 'PACKED'],  # cancel → back to PACKED
    'THAWING':          ['READY_FOR_SALE'],
    'READY_FOR_SALE':   ['ON_DISPLAY'],
    'ON_DISPLAY':       ['REFREEZE_PENDING', 'PROCESSING', 'DISCARDED'],
    'REFREEZE_PENDING': ['FREEZING'],
    'PROCESSING':       ['COMPLETED'],
    'DISCARDED':        ['COMPLETED'],
    'COMPLETED':        [],  # terminal state
}

ALL_STATES = list(TRANSITIONS.keys())


# ============================================================
# EXCEPTIONS
# ============================================================

class InvalidTransitionError(Exception):
    """Raised when a state transition is not allowed."""
    pass


class TransitionValidationError(Exception):
    """Raised when transition validation fails (missing schedule, etc.)."""
    pass


# ============================================================
# PUBLIC API
# ============================================================

def can_transition(from_state, to_state):
    """
    Check if a transition is allowed.

    Args:
        from_state: Current state string
        to_state: Target state string

    Returns:
        bool: True if transition is allowed
    """
    allowed = TRANSITIONS.get(from_state, [])
    return to_state in allowed


def get_allowed_transitions(from_state):
    """
    Get list of states that from_state can transition to.

    Args:
        from_state: Current state string

    Returns:
        list: Allowed target states
    """
    return TRANSITIONS.get(from_state, [])


def is_terminal(state):
    """Check if a state is terminal (no further transitions)."""
    return state == 'COMPLETED'


def transition_package(package, target_state, actor='', reason='', metadata=None):
    """
    Execute a state transition for a package.

    This is the SINGLE ENTRY POINT for all state changes.

    Args:
        package: Package instance (must have current_state field)
        target_state: Target state string
        actor: User/actor performing the transition
        reason: Optional reason for the transition
        metadata: Optional dict of additional data

    Returns:
        Updated Package instance

    Raises:
        InvalidTransitionError: If transition is not allowed
        TransitionValidationError: If validation fails
    """
    from inventory.models import PackageState

    # Validate states are valid
    valid_states = [choice[0] for choice in PackageState.choices]
    if package.current_state not in valid_states:
        raise InvalidTransitionError(f"Invalid current state: {package.current_state}")
    if target_state not in valid_states:
        raise InvalidTransitionError(f"Invalid target state: {target_state}")

    # Validate transition is allowed
    if not can_transition(package.current_state, target_state):
        raise InvalidTransitionError(
            f"Cannot transition from {package.current_state} to {target_state}. "
            f"Allowed: {TRANSITIONS.get(package.current_state, [])}"
        )

    # Validate required prerequisites
    _validate_transition_requirements(package, target_state)

    with transaction.atomic():
        old_state = package.current_state

        # Update package state
        package.current_state = target_state
        package.save(update_fields=['current_state', 'updated_at'])

        # Create audit event
        _create_rotation_event(package, old_state, target_state, actor, reason, metadata)

        # Auto-complete related worker tasks
        _auto_complete_worker_tasks(package, old_state, target_state, actor)

    return package


# ============================================================
# TRANSITION VALIDATION
# ============================================================

def _validate_transition_requirements(package, target_state):
    """
    Validate that required prerequisites exist for the transition.

    Rules:
        - THAW_QUEUED: must have a RotationPlan
        - THAWING: must have a RotationPlan and be in thaw queue
        - READY_FOR_SALE: must have completed thaw
        - ON_DISPLAY: must be READY_FOR_SALE
    """
    if target_state == 'THAW_QUEUED':
        from planning.models import RotationPlan
        if not RotationPlan.objects.filter(package=package).exists():
            raise TransitionValidationError(
                "Cannot queue for thaw: package has no rotation plan. "
                "Create a rotation plan first."
            )

    elif target_state == 'THAWING':
        from planning.models import RotationPlan, ThawQueueEntry
        if not RotationPlan.objects.filter(package=package).exists():
            raise TransitionValidationError(
                "Cannot start thawing: package has no rotation plan."
            )
        from planning.models import QueueStatus
        if not ThawQueueEntry.objects.filter(
            package=package,
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
        ).exists():
            raise TransitionValidationError(
                "Cannot start thawing: package is not in the thaw queue."
            )

    elif target_state == 'READY_FOR_SALE':
        from planning.models import ThawQueueEntry, QueueStatus
        if not ThawQueueEntry.objects.filter(
            package=package,
            status=QueueStatus.COMPLETED
        ).exists():
            raise TransitionValidationError(
                "Cannot mark ready for sale: thaw not completed."
            )

    elif target_state == 'ON_DISPLAY':
        if package.current_state != 'READY_FOR_SALE':
            raise TransitionValidationError(
                "Cannot move to display: package is not READY_FOR_SALE."
            )


# ============================================================
# AUDIT TRAIL
# ============================================================

def _create_rotation_event(package, from_state, to_state, actor, reason, metadata):
    """Create a RotationEvent audit record."""
    from operations.models import RotationEvent
    RotationEvent.objects.create(
        package=package,
        event_type='STATE_TRANSITION',
        from_state=from_state,
        to_state=to_state,
        timestamp=timezone.now(),
        actor=actor,
        reason=reason or '',
        metadata=metadata or {},
    )


def _auto_complete_worker_tasks(package, from_state, to_state, actor):
    """
    Auto-complete related WorkerTasks when a state transition occurs.

    Maps (from_state, to_state) → task_type to auto-complete.
    """
    from operations.models import WorkerTask, TaskEvent, TaskStatus

    task_type_mapping = {
        ('PACKED', 'FREEZING'):          'FREEZE_START',
        ('FREEZING', 'FROZEN'):          'FREEZE_CHECK',
        ('THAW_QUEUED', 'THAWING'):      'THAW_START',
        ('THAWING', 'READY_FOR_SALE'):   'THAW_COMPLETE',
        ('READY_FOR_SALE', 'ON_DISPLAY'): 'MOVE_TO_DISPLAY',
        ('ON_DISPLAY', 'REFREEZE_PENDING'): 'REFREEZE',
    }

    task_type = task_type_mapping.get((from_state, to_state))
    if not task_type:
        return

    task = WorkerTask.objects.filter(
        package=package,
        task_type=task_type,
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    ).order_by('scheduled_at').first()

    if task:
        task.status = TaskStatus.COMPLETED
        task.completed_at = timezone.now()
        # Handle actor: User instance or string
        if hasattr(actor, 'pk'):
            task.completed_by = actor
        task.save(update_fields=['status', 'completed_at', 'updated_at'])

        actor_name = str(actor) if actor else 'system'
        TaskEvent.objects.create(
            task=task,
            event_type='TASK_COMPLETED',
            timestamp=timezone.now(),
            actor=actor_name,
            notes=f'State transition: {from_state} → {to_state}',
        )
