"""
Operations API Views: JSON responses for frontend.
"""
import json
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import timedelta
from .services import get_todays_tasks, complete_task
from .models import WorkerTask, TaskStatus
from common.time_service import format_display


@login_required
@require_http_methods(["GET"])
def tasks_today_api(request):
    """Get today's tasks."""
    tasks = get_todays_tasks()
    
    data = []
    for task in tasks:
        data.append({
            'id': task.pk,
            'package_name': task.package.display_name,
            'package_id': task.package.pk,
            'task_type': task.task_type,
            'task_type_display': task.get_task_type_display(),
            'scheduled_at': format_display(task.scheduled_at),
            'scheduled_at_iso': task.scheduled_at.isoformat() if task.scheduled_at else None,
            'status': task.status,
            'is_overdue': task.is_overdue,
        })
    
    return JsonResponse({'tasks': data})


@login_required
@require_http_methods(["GET"])
def all_tasks_api(request):
    """Get all pending/in-progress/overdue tasks (not just today)."""
    tasks = WorkerTask.objects.filter(
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('scheduled_at')
    
    now = timezone.now()
    today_start = timezone.make_aware(timezone.datetime.combine(now.date(), timezone.datetime.min.time()))
    today_end = today_start + timedelta(days=1)
    
    data = []
    for task in tasks:
        # Categorize
        if task.status == TaskStatus.OVERDUE:
            category = 'overdue'
        elif task.scheduled_at <= now:
            category = 'due'
        elif task.scheduled_at < today_end:
            category = 'today'
        else:
            category = 'future'
        
        data.append({
            'id': task.pk,
            'package_name': task.package.display_name,
            'package_id': task.package.pk,
            'task_type': task.task_type,
            'task_type_display': task.get_task_type_display(),
            'scheduled_at': format_display(task.scheduled_at),
            'scheduled_at_iso': task.scheduled_at.isoformat() if task.scheduled_at else None,
            'status': task.status,
            'category': category,
            'is_overdue': task.is_overdue,
        })
    
    return JsonResponse({'tasks': data, 'count': len(data)})


@require_http_methods(["GET"])
def task_detail_api(request, pk):
    """Get task detail."""
    try:
        task = WorkerTask.objects.select_related(
            'package', 'package__product', 'rotation_plan'
        ).get(pk=pk)
    except WorkerTask.DoesNotExist:
        return JsonResponse({'error': 'Task not found'}, status=404)
    
    data = {
        'id': task.pk,
        'package_name': task.package.display_name,
        'package_id': task.package.pk,
        'task_type': task.task_type,
        'task_type_display': task.get_task_type_display(),
        'scheduled_at': format_display(task.scheduled_at),
        'status': task.status,
        'completed_at': format_display(task.completed_at),
        'completed_by': task.completed_by,
        'notes': task.notes,
    }
    
    return JsonResponse(data)


@require_http_methods(["POST"])
def task_complete_api(request, pk):
    """Complete a task via API. Auto-transitions package state based on task type."""
    try:
        task = WorkerTask.objects.get(pk=pk)
    except WorkerTask.DoesNotExist:
        return JsonResponse({'error': 'Task not found'}, status=404)
    
    try:
        data = json.loads(request.body) if request.body else {}
        actor = data.get('actor', 'api')
        notes = data.get('notes', '')
        
        result = complete_task(task, actor, notes)
        transitions = result.get('transitions', [])
        
        # Reload task to get updated state
        task.refresh_from_db()
        package = task.package
        
        response = {
            'message': 'Task completed successfully',
            'task_status': task.status,
            'package_state': package.current_state,
            'transitions': [{'from': f, 'to': t} for f, t in transitions],
        }
        
        return JsonResponse(response)
        
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def freeze_start_api(request):
    """Start freezing a package."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    try:
        from inventory.models import Package
        from common.state_machine import transition_package
        
        package = Package.objects.get(pk=data['package_id'])
        transition_package(
            package, 'FREEZING',
            actor=data.get('actor', 'api'),
            reason=data.get('reason', 'Freeze started')
        )
        
        return JsonResponse({'message': 'Freeze started'})
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def freeze_complete_api(request):
    """Complete freezing a package."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    try:
        from inventory.models import Package
        from common.state_machine import transition_package
        
        package = Package.objects.get(pk=data['package_id'])
        transition_package(
            package, 'FROZEN',
            actor=data.get('actor', 'api'),
            reason=data.get('reason', 'Freeze completed')
        )
        
        return JsonResponse({'message': 'Freeze completed'})
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def thaw_start_api(request):
    """Start thawing a package."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    try:
        from inventory.models import Package
        from common.state_machine import transition_package
        
        package = Package.objects.get(pk=data['package_id'])
        transition_package(
            package, 'THAWING',
            actor=data.get('actor', 'api'),
            reason=data.get('reason', 'Thaw started')
        )
        
        return JsonResponse({'message': 'Thaw started'})
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def thaw_complete_api(request):
    """Complete thawing a package."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    try:
        from inventory.models import Package
        from common.state_machine import transition_package
        from planning.models import ThawQueueEntry, QueueStatus
        
        package = Package.objects.get(pk=data['package_id'])
        
        # Mark queue entry as completed first
        queue_entry = ThawQueueEntry.objects.filter(
            package=package,
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
        ).first()
        if queue_entry:
            queue_entry.status = QueueStatus.COMPLETED
            queue_entry.save(update_fields=['status', 'updated_at'])
        
        transition_package(
            package, 'READY_FOR_SALE',
            actor=data.get('actor', 'api'),
            reason=data.get('reason', 'Thaw completed')
        )
        
        return JsonResponse({'message': 'Thaw completed, ready for sale'})
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def display_start_api(request):
    """Move package to display."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    try:
        from inventory.models import Package
        from common.state_machine import transition_package
        
        package = Package.objects.get(pk=data['package_id'])
        transition_package(
            package, 'ON_DISPLAY',
            actor=data.get('actor', 'api'),
            reason=data.get('reason', 'Moved to display')
        )
        
        return JsonResponse({'message': 'Moved to display'})
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def display_refreeze_api(request):
    """Refreeze a package from display."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    try:
        from inventory.models import Package
        from common.state_machine import transition_package
        
        package = Package.objects.get(pk=data['package_id'])
        transition_package(
            package, 'REFREEZE_PENDING',
            actor=data.get('actor', 'api'),
            reason=data.get('reason', 'Refreeze requested')
        )
        
        return JsonResponse({'message': 'Refreeze pending'})
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)
