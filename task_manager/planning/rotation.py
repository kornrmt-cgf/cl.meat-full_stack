"""
Rotation Decision Engine.

Single responsibility: given all packages, decide what action is needed and why.
"""
from datetime import timedelta
from django.utils import timezone

from inventory.models import Package, PackageState
from planning.models import RotationPlan, PlanStatus


# ============================================================
# PRIORITY
# ============================================================

class Priority:
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    LABELS = {1: '🔴', 2: '🟠', 3: '🟡', 4: '🟢'}


# ============================================================
# DECISION
# ============================================================

class Decision:
    """One actionable decision for one package."""

    def __init__(self, package, action, reason, priority,
                 target_ready_at=None, scheduled_at=None):
        self.package = package
        self.action = action
        self.reason = reason
        self.priority = priority
        self.target_ready_at = target_ready_at
        self.scheduled_at = scheduled_at

    def to_dict(self):
        return {
            'barcode': self.package.barcode,
            'product': self.package.product.name,
            'weight_kg': float(self.package.weight),
            'state': self.package.current_state,
            'action': self.action,
            'reason': self.reason,
            'priority': self.priority,
            'priority_icon': Priority.LABELS.get(self.priority, ''),
            'target_ready': self.target_ready_at.isoformat() if self.target_ready_at else None,
        }


# ============================================================
# ENGINE
# ============================================================

class RotationEngine:
    """Analyzes all packages and returns prioritised decisions."""

    def __init__(self):
        self.now = timezone.now()

    def get_decisions(self):
        """Return all decisions sorted by priority (most urgent first)."""
        # Pre-fetch plans for frozen/thawing packages to avoid N+1
        frozen_pkgs = list(Package.objects.filter(
            current_state=PackageState.FROZEN,
        ).select_related('product'))
        thawing_pkgs = list(Package.objects.filter(
            current_state=PackageState.THAWING,
        ).select_related('product'))
        pkg_ids = [p.id for p in frozen_pkgs] + [p.id for p in thawing_pkgs]
        plans_by_pkg = {
            rp.package_id: rp
            for rp in RotationPlan.objects.filter(package_id__in=pkg_ids)
        }

        d = []
        d.extend(self._packed_need_freeze())
        d.extend(self._frozen_need_thaw_queue(frozen_pkgs, plans_by_pkg))
        d.extend(self._thawing_approaching_done(thawing_pkgs, plans_by_pkg))
        d.extend(self._overdue_plans())
        d.sort(key=lambda x: x.priority)
        return d

    def get_thaw_candidates(self, limit=10):
        """Frozen packages that should enter the thaw queue next."""
        plans_by_pkg = {
            rp.package_id: rp
            for rp in RotationPlan.objects.filter(
                package__current_state=PackageState.FROZEN
            ).select_related('package')
        }
        frozen = Package.objects.filter(
            current_state=PackageState.FROZEN,
        ).select_related('product')

        candidates = []
        for pkg in frozen:
            plan = plans_by_pkg.get(pkg.id)
            if plan and plan.planned_thaw_queue_at:
                due_in = plan.planned_thaw_queue_at - self.now
                if due_in <= timedelta(hours=2):
                    p = Priority.CRITICAL if due_in <= timedelta(0) else Priority.HIGH
                    candidates.append(Decision(
                        pkg, 'MOVE_TO_THAW_QUEUE',
                        f'Planned queue at {plan.planned_thaw_queue_at.strftime("%H:%M")}',
                        p, plan.target_ready_at, plan.planned_thaw_queue_at,
                    ))
            else:
                candidates.append(Decision(
                    pkg, 'NEEDS_PLAN',
                    f'Frozen {pkg.packed_at.strftime("%d/%m %H:%M")} — no rotation plan',
                    Priority.LOW,
                ))

        candidates.sort(key=lambda x: (x.priority, x.scheduled_at or self.now + timedelta(days=365)))
        return candidates[:limit]

    # ── private checks ──

    def _packed_need_freeze(self):
        return [
            Decision(p, 'START_FREEZE', 'Packed but not in freezer', Priority.HIGH)
            for p in Package.objects.filter(current_state=PackageState.PACKED)
        ]

    def _frozen_need_thaw_queue(self, frozen_pkgs, plans_by_pkg):
        results = []
        for pkg in frozen_pkgs:
            plan = plans_by_pkg.get(pkg.id)
            if plan and plan.planned_thaw_queue_at:
                due_in = plan.planned_thaw_queue_at - self.now
                if timedelta(hours=-2) < due_in <= timedelta(hours=2):
                    p = Priority.CRITICAL if due_in <= timedelta(0) else Priority.HIGH
                    results.append(Decision(
                        pkg, 'MOVE_TO_THAW_QUEUE',
                        f'Thaw queue time reached ({plan.planned_thaw_queue_at.strftime("%H:%M")})',
                        p, plan.target_ready_at, plan.planned_thaw_queue_at,
                    ))
        return results

    def _thawing_approaching_done(self, thawing_pkgs, plans_by_pkg):
        results = []
        for pkg in thawing_pkgs:
            plan = plans_by_pkg.get(pkg.id)
            if plan and plan.target_ready_at - self.now <= timedelta(hours=1):
                results.append(Decision(
                    pkg, 'CHECK_THAW_COMPLETE',
                    f'Should be ready at {plan.target_ready_at.strftime("%H:%M")}',
                    Priority.HIGH, plan.target_ready_at,
                ))
        return results

    def _overdue_plans(self):
        return [
            Decision(
                plan.package, 'OVERDUE_PLAN',
                f'Target {plan.target_ready_at.strftime("%d/%m %H:%M")} passed',
                Priority.CRITICAL, plan.target_ready_at,
            )
            for plan in RotationPlan.objects.filter(
                status__in=[PlanStatus.PLANNED, PlanStatus.READY, PlanStatus.IN_PROGRESS],
                target_ready_at__lt=self.now,
            ).select_related('package')
        ]
