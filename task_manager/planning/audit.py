"""
Audit Trail — records who did what, when, why, and whether it was automatic.
"""
from django.utils import timezone
from operations.models import RotationEvent, TaskEvent
from inventory.models import StockMovement


class Audit:
    """Logs lifecycle events. All methods are stateless."""

    @staticmethod
    def state_change(package, from_state, to_state, actor='', reason='',
                     automatic=False):
        RotationEvent.objects.create(
            package=package,
            event_type='STATE_TRANSITION',
            from_state=from_state,
            to_state=to_state,
            timestamp=timezone.now(),
            actor=actor or 'system',
            reason=reason,
            metadata={'automatic': automatic},
        )

    @staticmethod
    def override(package, from_state, to_state, actor, reason):
        RotationEvent.objects.create(
            package=package,
            event_type='MANUAL_OVERRIDE',
            from_state=from_state,
            to_state=to_state,
            timestamp=timezone.now(),
            actor=actor,
            reason=reason,
            metadata={'automatic': False},
        )

    @staticmethod
    def plan_action(plan, action, actor='', reason=''):
        RotationEvent.objects.create(
            package=plan.package,
            event_type=action,
            from_state=plan.status,
            to_state=plan.status,
            timestamp=timezone.now(),
            actor=actor or 'system',
            reason=reason,
            metadata={'plan_id': plan.id, 'automatic': False},
        )

    @staticmethod
    def movement(package, movement_type, actor='', reason='',
                 from_location=None, to_location=None):
        StockMovement.objects.create(
            package=package,
            movement_type=movement_type,
            from_location=from_location,
            to_location=to_location,
            weight_at_movement=package.weight,
            actor=actor or 'system',
            reason=reason,
        )


def package_trail(package, limit=50):
    """Chronological audit trail for a package."""
    return [
        {
            'when': e.timestamp,
            'what': e.event_type,
            'from': e.from_state,
            'to': e.to_state,
            'who': e.actor,
            'why': e.reason,
            'automatic': e.metadata.get('automatic', False),
        }
        for e in RotationEvent.objects.filter(package=package)
            .order_by('timestamp')[:limit]
    ]
