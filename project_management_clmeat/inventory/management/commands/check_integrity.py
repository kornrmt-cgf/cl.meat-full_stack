"""
Management command to check and repair data integrity issues.

Usage:
  python manage.py check_integrity          # Check only
  python manage.py check_integrity --repair # Check and repair
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from inventory.models import Package, PackageState
from planning.models import RotationPlan, ThawQueueEntry, QueueStatus, PlanStatus, FreezeProfile, ThawProfile


class Command(BaseCommand):
    help = 'Check and repair data integrity issues for packages'

    def add_arguments(self, parser):
        parser.add_argument(
            '--repair', action='store_true',
            help='Attempt to repair detected issues'
        )

    def handle(self, *args, **options):
        repair = options['repair']
        issues = []

        # 1. Check THAW_QUEUED without RotationPlan
        for pkg in Package.objects.filter(current_state=PackageState.THAW_QUEUED):
            has_plan = RotationPlan.objects.filter(package=pkg).exists()
            has_queue = ThawQueueEntry.objects.filter(package=pkg).exists()
            if not has_plan:
                issues.append({
                    'type': 'THAW_QUEUED_NO_PLAN',
                    'package': pkg,
                    'has_queue': has_queue,
                    'description': f'Package {pkg.pk} ({pkg.display_name}) is THAW_QUEUED but has no RotationPlan',
                    'repair_action': 'transition_to_frozen' if has_queue else 'transition_to_frozen',
                })

        # 2. Check THAWING without RotationPlan
        for pkg in Package.objects.filter(current_state=PackageState.THAWING):
            has_plan = RotationPlan.objects.filter(package=pkg).exists()
            has_queue = ThawQueueEntry.objects.filter(package=pkg).exists()
            if not has_plan:
                issues.append({
                    'type': 'THAWING_NO_PLAN',
                    'package': pkg,
                    'has_queue': has_queue,
                    'description': f'Package {pkg.pk} ({pkg.display_name}) is THAWING but has no RotationPlan',
                    'repair_action': 'cancel_queue_transition_to_frozen',
                })

        # 3. Check THAW_QUEUED with plan but no queue entry
        for pkg in Package.objects.filter(current_state=PackageState.THAW_QUEUED):
            has_plan = RotationPlan.objects.filter(package=pkg).exists()
            has_queue = ThawQueueEntry.objects.filter(
                package=pkg,
                status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
            ).exists()
            if has_plan and not has_queue:
                issues.append({
                    'type': 'THAW_QUEUED_NO_QUEUE_ENTRY',
                    'package': pkg,
                    'has_plan': has_plan,
                    'description': f'Package {pkg.pk} ({pkg.display_name}) is THAW_QUEUED with plan but no active queue entry',
                    'repair_action': 'create_queue_entry',
                })

        # 4. Check packages with plans that should be FROZEN but aren't
        for plan in RotationPlan.objects.filter(status=PlanStatus.PLANNED):
            pkg = plan.package
            if pkg.current_state not in [PackageState.FROZEN, PackageState.READY_FOR_THAW,
                                         PackageState.THAW_QUEUED, PackageState.THAWING,
                                         PackageState.READY_FOR_SALE, PackageState.ON_DISPLAY]:
                issues.append({
                    'type': 'PLAN_STATE_MISMATCH',
                    'package': pkg,
                    'description': f'Plan {plan.pk} for Package {pkg.pk} ({pkg.display_name}) '
                                   f'but package state is {pkg.current_state}',
                    'repair_action': 'cancel_plan',
                })

        # Report
        self.stdout.write(self.style.WARNING(f'\n=== Data Integrity Check ===\n'))
        if not issues:
            self.stdout.write(self.style.SUCCESS('No integrity issues found.\n'))
            return

        for i, issue in enumerate(issues, 1):
            self.stdout.write(self.style.ERROR(
                f'{i}. [{issue["type"]}] {issue["description"]}'
            ))

        if repair:
            self.stdout.write(self.style.WARNING(f'\n=== Attempting Repair ===\n'))
            repaired = 0
            for issue in issues:
                try:
                    self._repair_issue(issue)
                    repaired += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✅ Repaired: {issue["description"]}'
                    ))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(
                        f'  ❌ Failed: {issue["description"]} — {e}'
                    ))
            self.stdout.write(self.style.SUCCESS(
                f'\nRepaired {repaired}/{len(issues)} issues.\n'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'\nFound {len(issues)} issues. Run with --repair to attempt repair.\n'
            ))

    def _repair_issue(self, issue):
        """Repair a single integrity issue."""
        pkg = issue['package']
        action = issue['repair_action']

        if action == 'transition_to_frozen':
            # Cancel any queue entries, transition back to FROZEN
            with transaction.atomic():
                ThawQueueEntry.objects.filter(
                    package=pkg,
                    status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
                ).update(status=QueueStatus.CANCELLED)
                # Transition THAW_QUEUED -> PACKED -> FREEZING -> FROZEN via direct state update
                # We do a controlled reset since the normal transition path doesn't allow this
                pkg.current_state = PackageState.FROZEN
                pkg.save(update_fields=['current_state', 'updated_at'])
                from operations.models import RotationEvent
                RotationEvent.objects.create(
                    package=pkg,
                    event_type='DATA_INTEGRITY_REPAIR',
                    from_state='THAW_QUEUED',
                    to_state='FROZEN',
                    timestamp=timezone.now(),
                    actor='SYSTEM_INTEGRITY_CHECK',
                    reason='Data integrity repair: package was THAW_QUEUED without RotationPlan',
                )

        elif action == 'cancel_queue_transition_to_frozen':
            # THAWING without plan — reset to FROZEN
            with transaction.atomic():
                ThawQueueEntry.objects.filter(
                    package=pkg,
                    status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED, QueueStatus.STARTED]
                ).update(status=QueueStatus.CANCELLED)
                pkg.current_state = PackageState.FROZEN
                pkg.save(update_fields=['current_state', 'updated_at'])
                from operations.models import RotationEvent
                RotationEvent.objects.create(
                    package=pkg,
                    event_type='DATA_INTEGRITY_REPAIR',
                    from_state='THAWING',
                    to_state='FROZEN',
                    timestamp=timezone.now(),
                    actor='SYSTEM_INTEGRITY_CHECK',
                    reason='Data integrity repair: package was THAWING without RotationPlan',
                )

        elif action == 'create_queue_entry':
            # Has plan but package is already THAW_QUEUED — create queue entry directly
            from django.db.models import Max
            plan = RotationPlan.objects.filter(package=pkg).first()
            if plan:
                max_pos = ThawQueueEntry.objects.filter(
                    status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
                ).aggregate(Max('queue_position'))['queue_position__max'] or 0
                ThawQueueEntry.objects.create(
                    package=pkg,
                    rotation_plan=plan,
                    queue_position=max_pos + 1,
                    planned_start_at=plan.planned_thaw_start_at,
                    target_ready_at=plan.target_ready_at,
                    status=QueueStatus.QUEUED
                )
                from operations.models import RotationEvent
                RotationEvent.objects.create(
                    package=pkg,
                    event_type='DATA_INTEGRITY_REPAIR',
                    from_state='THAW_QUEUED',
                    to_state='THAW_QUEUED',
                    timestamp=timezone.now(),
                    actor='SYSTEM_INTEGRITY_CHECK',
                    reason='Data integrity repair: created missing ThawQueueEntry for THAW_QUEUED package',
                )

        elif action == 'cancel_plan':
            with transaction.atomic():
                plan = RotationPlan.objects.filter(package=pkg).first()
                if plan:
                    plan.status = PlanStatus.CANCELLED
                    plan.save(update_fields=['status', 'updated_at'])
