"""
Operations Selectors: Read-only queries and data access.
"""
from django.db.models import Q, Count
from datetime import datetime, timedelta
from django.utils import timezone
from .models import WorkerTask, TaskStatus, TaskType, RotationEvent, TaskEvent
from inventory.models import Package


def get_tasks_for_date(target_date):
    """Get tasks for a specific date."""
    return WorkerTask.objects.filter(
        scheduled_at__date=target_date
    ).select_related('package', 'package__product', 'rotation_plan')


def get_tasks_by_status(status):
    """Get tasks by status."""
    return WorkerTask.objects.filter(
        status=status
    ).select_related('package', 'package__product', 'rotation_plan')


def get_upcoming_tasks(hours=24):
    """Get tasks due in the next N hours."""
    now = timezone.now()
    end_time = now + timedelta(hours=hours)
    
    return WorkerTask.objects.filter(
        scheduled_at__range=(now, end_time),
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('scheduled_at')


def get_task_detail(task_id):
    """Get task with all related data."""
    return WorkerTask.objects.select_related(
        'package', 'package__product', 'package__batch',
        'rotation_plan', 'rotation_plan__freeze_profile', 'rotation_plan__thaw_profile'
    ).get(pk=task_id)


def get_task_events(task_id):
    """Get events for a specific task."""
    return TaskEvent.objects.filter(task_id=task_id).order_by('timestamp')


def get_rotation_events(package_id=None, days=7):
    """Get rotation events, optionally filtered by package."""
    start_date = timezone.now() - timedelta(days=days)
    
    queryset = RotationEvent.objects.filter(
        created_at__gte=start_date
    ).select_related('package', 'package__product')
    
    if package_id:
        queryset = queryset.filter(package_id=package_id)
    
    return queryset.order_by('-timestamp')


def get_task_stats():
    """Get task statistics."""
    return WorkerTask.objects.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status=TaskStatus.PENDING)),
        in_progress=Count('id', filter=Q(status=TaskStatus.IN_PROGRESS)),
        completed=Count('id', filter=Q(status=TaskStatus.COMPLETED)),
        overdue=Count('id', filter=Q(status=TaskStatus.OVERDUE)),
    )
