"""
Timezone and time utilities for Fresh Meat Rotation Planner.
"""
from django.utils import timezone
from datetime import datetime, timedelta


def now():
    """Get current timezone-aware datetime."""
    return timezone.now()


def _to_local(dt):
    """Convert datetime to Asia/Bangkok local time."""
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    bangkok = timezone.get_current_timezone()
    return timezone.localtime(dt, bangkok)


def format_display(dt):
    """Format datetime for display: DD/MM/YYYY HH:mm (Bangkok time)."""
    if dt is None:
        return '-'
    local = _to_local(dt)
    return local.strftime('%d/%m/%Y %H:%M')


def format_time_only(dt):
    """Format time only: HH:mm (Bangkok time)."""
    if dt is None:
        return '-'
    local = _to_local(dt)
    return local.strftime('%H:%M')


def format_date_only(dt):
    """Format date only: DD/MM/YYYY (Bangkok time)."""
    if dt is None:
        return '-'
    local = _to_local(dt)
    return local.strftime('%d/%m/%Y')


def parse_datetime(dt_string):
    """Parse datetime string in DD/MM/YYYY HH:mm format."""
    naive = datetime.strptime(dt_string, '%d/%m/%Y %H:%M')
    return timezone.make_aware(naive)


def get_start_of_day(dt=None):
    """Get start of day (00:00:00) for given datetime."""
    if dt is None:
        dt = now()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def get_end_of_day(dt=None):
    """Get end of day (23:59:59) for given datetime."""
    if dt is None:
        dt = now()
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def is_overdue(scheduled_at, tolerance_minutes=0):
    """Check if a scheduled time is overdue."""
    if scheduled_at is None:
        return False
    return now() > (scheduled_at + timedelta(minutes=tolerance_minutes))


def get_time_until(target_dt):
    """Get time remaining until target datetime."""
    if target_dt is None:
        return None
    delta = target_dt - now()
    return delta


def format_duration(duration):
    """Format timedelta as human-readable string."""
    if duration is None:
        return '-'
    total_seconds = int(duration.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
