"""
Planning Selectors: Read-only queries and data access.
"""
from django.db.models import Q, Count
from datetime import datetime, timedelta
from .models import RotationPlan, PlanStatus, ThawQueueEntry, QueueStatus, FreezeProfile, ThawProfile
from inventory.models import Package, PackageState


def get_all_plans(status=None, product=None):
    """Get all rotation plans with optional filters."""
    queryset = RotationPlan.objects.select_related(
        'package', 'package__product', 'freeze_profile', 'thaw_profile'
    )
    
    if status:
        queryset = queryset.filter(status=status)
    if product:
        queryset = queryset.filter(package__product=product)
    
    return queryset


def get_plans_for_date(target_date):
    """Get plans for a specific date."""
    start_of_day = datetime.combine(target_date, datetime.min.time())
    end_of_day = datetime.combine(target_date, datetime.max.time())
    
    return RotationPlan.objects.filter(
        target_ready_at__range=(start_of_day, end_of_day)
    ).select_related('package', 'package__product')


def get_plans_for_date_range(start_date, end_date):
    """Get plans for a date range."""
    return RotationPlan.objects.filter(
        target_ready_at__date__range=(start_date, end_date)
    ).select_related('package', 'package__product')


def get_calendar_data(year, month, product=None):
    """
    Get calendar data for monthly planner.
    
    Returns dict with daily stats.
    """
    from calendar import monthrange
    
    _, days_in_month = monthrange(year, month)
    
    calendar_data = []
    
    for day in range(1, days_in_month + 1):
        date = datetime(year, month, day).date()
        plans = get_plans_for_date(date)
        
        if product:
            plans = plans.filter(package__product=product)
        
        required = plans.count()
        completed = plans.filter(status=PlanStatus.COMPLETED).count()
        
        calendar_data.append({
            'date': date,
            'required': required,
            'planned': required,  # All plans are planned
            'remaining': 0,
            'status': 'OK' if required == completed else ('AT_RISK' if required > 0 else ''),
            'plans': list(plans)
        })
    
    return calendar_data


def get_queue(status=None):
    """Get thaw queue entries."""
    queryset = ThawQueueEntry.objects.select_related(
        'package', 'package__product', 'rotation_plan'
    )
    
    if status:
        queryset = queryset.filter(status=status)
    
    return queryset.order_by('queue_position')


def get_package_timeline(package):
    """
    Get timeline for a package.
    
    Returns list of events in chronological order.
    """
    from operations.models import RotationEvent
    
    events = RotationEvent.objects.filter(package=package).order_by('timestamp')
    
    timeline = []
    for event in events:
        timeline.append({
            'event_type': event.event_type,
            'from_state': event.from_state,
            'to_state': event.to_state,
            'timestamp': event.timestamp,
            'actor': event.actor,
            'reason': event.reason,
        })
    
    return timeline


def get_available_packages(product=None):
    """
    Get packages available for planning.
    
    Returns packages in FROZEN state without existing plans.
    """
    queryset = Package.objects.filter(
        current_state=PackageState.FROZEN
    ).exclude(
        rotation_plan__isnull=False
    ).select_related('product', 'batch')
    
    if product:
        queryset = queryset.filter(product=product)
    
    return queryset


def get_active_profiles():
    """Get active freeze and thaw profiles."""
    freeze_profiles = FreezeProfile.objects.filter(active=True)
    thaw_profiles = ThawProfile.objects.filter(active=True)
    return freeze_profiles, thaw_profiles


def get_plan_stats():
    """Get plan statistics."""
    return RotationPlan.objects.aggregate(
        total=Count('id'),
        draft=Count('id', filter=Q(status=PlanStatus.DRAFT)),
        planned=Count('id', filter=Q(status=PlanStatus.PLANNED)),
        in_progress=Count('id', filter=Q(status=PlanStatus.IN_PROGRESS)),
        completed=Count('id', filter=Q(status=PlanStatus.COMPLETED)),
        at_risk=Count('id', filter=Q(status=PlanStatus.AT_RISK)),
    )
