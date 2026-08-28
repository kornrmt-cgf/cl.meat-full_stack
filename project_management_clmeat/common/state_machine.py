"""
Centralized State Machine for Package State Transitions.

All state changes MUST go through transition_package().
This is the single source of truth for state transitions.
"""
from django.db import transaction
from django.utils import timezone


# Valid state transitions table
TRANSITIONS = {
    'PACKED': ['FREEZING'],
    'FREEZING': ['FROZEN'],
    'FROZEN': ['READY_FOR_THAW'],
    'READY_FOR_THAW': ['THAW_QUEUED'],
    'THAW_QUEUED': ['THAWING', 'PACKED'],  # cancel returns to PACKED
    'THAWING': ['READY_FOR_SALE'],
    'READY_FOR_SALE': ['ON_DISPLAY'],
    'ON_DISPLAY': ['REFREEZE_PENDING', 'PROCESSING', 'DISCARDED'],
    'REFREEZE_PENDING': ['FREEZING'],
    'PROCESSING': ['COMPLETED'],
    'DISCARDED': ['COMPLETED'],
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class TransitionValidationError(Exception):
    """Raised when transition validation fails (missing schedule, etc.)."""
    pass


def can_transition(from_state, to_state):
    """Check if a transition is allowed."""
    allowed = TRANSITIONS.get(from_state, [])
    return to_state in allowed


def transition_package(package, target_state, actor='', reason='', metadata=None):
    """
    Execute a state transition for a package.
    
    This is the SINGLE ENTRY POINT for all state changes.
    
    Args:
        package: Package instance
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
    from inventory.models import Package, PackageState
    from operations.models import RotationEvent, WorkerTask, TaskEvent
    
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
            f"Allowed transitions from {package.current_state}: {TRANSITIONS.get(package.current_state, [])}"
        )
    
    # Validate required schedules based on transition
    _validate_transition_requirements(package, target_state)
    
    with transaction.atomic():
        # Store old state
        old_state = package.current_state
        
        # Update package state
        package.current_state = target_state
        package.save(update_fields=['current_state', 'updated_at'])
        
        # Create audit event
        RotationEvent.objects.create(
            package=package,
            event_type=f'STATE_TRANSITION',
            from_state=old_state,
            to_state=target_state,
            timestamp=timezone.now(),
            actor=actor,
            reason=reason,
            metadata=metadata or {}
        )
        
        # Create task event if appropriate
        _create_task_event_for_transition(package, old_state, target_state, actor)
    
    return package


def _validate_transition_requirements(package, target_state):
    """Validate that required schedules exist for the transition."""
    from planning.models import RotationPlan, ThawQueueEntry
    
    # Validation rules for specific transitions
    if target_state == 'THAW_QUEUED':
        # Must have a rotation plan — enforces THAW_QUEUED => RotationPlan invariant
        if not RotationPlan.objects.filter(package=package).exists():
            raise TransitionValidationError(
                "Cannot queue for thaw: package has no rotation plan. "
                "Create a rotation plan first."
            )

    elif target_state == 'THAWING':
        # Must have a rotation plan
        if not RotationPlan.objects.filter(package=package).exists():
            raise TransitionValidationError(
                "Cannot start thawing: package has no rotation plan. "
                "Create a rotation plan first."
            )
        # Must be in thaw queue
        if not ThawQueueEntry.objects.filter(
            package=package,
            status__in=['QUEUED', 'READY_TO_START']
        ).exists():
            raise TransitionValidationError(
                "Cannot start thawing: package is not in the thaw queue. "
                "Add to thaw queue first."
            )
    
    elif target_state == 'READY_FOR_SALE':
        # Must have completed thaw
        if not ThawQueueEntry.objects.filter(
            package=package,
            status='COMPLETED'
        ).exists():
            raise TransitionValidationError(
                "Cannot mark ready for sale: thaw not completed."
            )
    
    elif target_state == 'ON_DISPLAY':
        # Must be READY_FOR_SALE
        if package.current_state != 'READY_FOR_SALE':
            raise TransitionValidationError(
                "Cannot move to display: package is not ready for sale."
            )


def _create_task_event_for_transition(package, from_state, to_state, actor):
    """Create task events for relevant transitions."""
    from operations.models import WorkerTask, TaskEvent
    
    # Map state transitions to task types
    task_type_mapping = {
        ('PACKED', 'FREEZING'): 'FREEZE_START',
        ('FREEZING', 'FROZEN'): 'FREEZE_COMPLETE',
        ('THAW_QUEUED', 'THAWING'): 'THAW_START',
        ('THAWING', 'READY_FOR_SALE'): 'THAW_COMPLETE',
        ('READY_FOR_SALE', 'ON_DISPLAY'): 'MOVE_TO_DISPLAY',
        ('ON_DISPLAY', 'REFREEZE_PENDING'): 'REFREEZE',
    }
    
    task_type = task_type_mapping.get((from_state, to_state))
    if task_type:
        # Find pending task of this type for this package
        task = WorkerTask.objects.filter(
            package=package,
            task_type=task_type,
            status='PENDING'
        ).first()
        
        if task:
            task.status = 'COMPLETED'
            task.completed_at = timezone.now()
            task.completed_by = actor
            task.save(update_fields=['status', 'completed_at', 'completed_by', 'updated_at'])
            
            TaskEvent.objects.create(
                task=task,
                event_type='TASK_COMPLETED',
                timestamp=timezone.now(),
                actor=actor,
                notes=f'State transition: {from_state} → {to_state}'
            )
