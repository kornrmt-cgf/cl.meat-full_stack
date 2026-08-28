"""
Timezone and time utilities for CL.MEAT system.
"""
from django.utils import timezone
from datetime import datetime, timedelta


def now():
    return timezone.now()


def _to_local(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return timezone.localtime(dt, timezone.get_current_timezone())


def format_display(dt):
    if dt is None:
        return '-'
    return _to_local(dt).strftime('%d/%m/%Y %H:%M')


def format_time_only(dt):
    if dt is None:
        return '-'
    return _to_local(dt).strftime('%H:%M')


def is_overdue(scheduled_at, tolerance_minutes=0):
    if scheduled_at is None:
        return False
    return now() > (scheduled_at + timedelta(minutes=tolerance_minutes))


def get_time_until(target_dt):
    if target_dt is None:
        return None
    return target_dt - now()


def format_duration(duration):
    if duration is None:
        return '-'
    total = int(duration.total_seconds())
    hours = total // 3600
    minutes = (total % 3600) // 60
    return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
