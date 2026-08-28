"""
Worker Mode Views — barcode-first, touch-friendly, large buttons.
"""
from django.shortcuts import render
from django.http import JsonResponse
from django.utils import timezone
from datetime import datetime, timedelta

from operations.models import WorkerTask, TaskStatus
from common.worker_actions import get_state_urgency, ACTIONS


def worker_home(request):
    """Worker home / scan page — the main screen for workers."""
    return render(request, 'worker/scan.html')


def worker_urgent(request):
    """Urgent tasks — action required now."""
    now = timezone.now()
    
    tasks = WorkerTask.objects.filter(
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE]
    ).select_related('package', 'package__product').order_by('scheduled_at')
    
    urgent_tasks = []
    for task in tasks:
        urgency = get_state_urgency(task.package)
        time_until = task.scheduled_at - now
        
        if task.scheduled_at < now:
            time_display = f'เกิน {int(abs(time_until).total_seconds() // 60)} น.'
            is_overdue = True
        else:
            hours = int(time_until.total_seconds() // 3600)
            minutes = int((time_until.total_seconds() % 3600) // 60)
            if hours > 0:
                time_display = f'อีก {hours}ชม. {minutes}น.'
            else:
                time_display = f'อีก {minutes} น.'
            is_overdue = False
        
        urgent_tasks.append({
            'task': task,
            'urgency': urgency,
            'time_display': time_display,
            'is_overdue': is_overdue,
        })
    
    return render(request, 'worker/urgent.html', {
        'urgent_tasks': urgent_tasks,
    })


def worker_today(request):
    """Today's tasks — card-based layout for workers."""
    bangkok_today = timezone.localtime(timezone.now()).date()
    now = timezone.now()
    
    start = timezone.make_aware(datetime.combine(bangkok_today, datetime.min.time()))
    end = start + timedelta(days=1)
    tasks = WorkerTask.objects.filter(
        scheduled_at__gte=start,
        scheduled_at__lt=end,
    ).select_related('package', 'package__product').order_by('scheduled_at')
    
    categorized = {
        'overdue': [],
        'due_now': [],
        'upcoming': [],
        'completed': [],
    }
    
    for task in tasks:
        urgency = get_state_urgency(task.package)
        time_until = task.scheduled_at - now
        
        if task.status == TaskStatus.COMPLETED:
            categorized['completed'].append({
                'task': task,
                'urgency': urgency,
            })
        elif task.scheduled_at < now:
            categorized['overdue'].append({
                'task': task,
                'urgency': urgency,
                'time_display': f'เกิน {int(abs(time_until).total_seconds() // 60)} น.',
            })
        elif time_until <= timedelta(minutes=30):
            categorized['due_now'].append({
                'task': task,
                'urgency': urgency,
                'time_display': task.scheduled_at.strftime('%H:%M'),
            })
        else:
            categorized['upcoming'].append({
                'task': task,
                'urgency': urgency,
                'time_display': task.scheduled_at.strftime('%H:%M'),
            })
    
    return render(request, 'worker/today.html', {
        'categorized': categorized,
        'total': tasks.count(),
    })
