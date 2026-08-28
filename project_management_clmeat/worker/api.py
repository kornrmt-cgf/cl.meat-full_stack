"""
Worker API — barcode-first endpoints for worker operations.

Core workflow:
SCAN BARCODE → GET PACKAGE INFO + NEXT ACTION → PERFORM ACTION → CONFIRM → NEXT SCAN
"""
import json
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db import transaction
from datetime import timedelta


@login_required
@require_http_methods(["GET"])
def scan_barcode(request):
    """
    Scan a barcode and return package info + next action.
    
    GET /api/worker/scan/?barcode=TH-PK-001
    
    Returns:
        - Package info (product, weight, batch, location, state)
        - Next action (button label, API endpoint, target state)
        - Urgency level
        - Current task (if any)
        - Timeline
    """
    barcode = request.GET.get('barcode', '').strip()
    
    if not barcode:
        return JsonResponse({
            'success': False,
            'error': 'กรุณาสแกนบาร์โค้ด'
        })
    
    from common.worker_actions import get_scan_result
    result = get_scan_result(barcode)
    
    if not result['success']:
        return JsonResponse(result)
    
    package = result['package']
    action = result['action']
    urgency = result['urgency']
    current_task = result['current_task']
    timeline = result['timeline']
    
    # Format response
    response = {
        'success': True,
        'package': {
            'id': package.pk,
            'product_name': package.product.name,
            'product_sku': package.product.sku,
            'category': package.product.category,
            'weight': str(package.weight),
            'barcode': package.barcode,
            'batch_number': package.batch.batch_number,
            'current_state': package.current_state,
            'state_display': package.get_current_state_display(),
            'storage_location': package.storage_location.name if package.storage_location else None,
            'packed_at': package.packed_at.strftime('%d/%m/%Y %H:%M') if package.packed_at else None,
        },
        'action': None,
        'urgency': {
            'level': urgency['level'],
            'color': urgency['color'],
            'label': urgency['label'],
            'icon': urgency['icon'],
        },
        'current_task': None,
        'timeline': [],
    }
    
    # Format action
    if action:
        if action.get('button_label'):
            response['action'] = {
                'button_label': action['button_label'],
                'api_endpoint': action['api_endpoint'],
                'target_state': action['target_state'],
                'icon': action.get('icon', ''),
                'description': action.get('description', ''),
                'worker_prompt': action.get('worker_prompt', ''),
            }
        elif action.get('actions'):
            # Multiple actions (e.g., ON_DISPLAY)
            response['action'] = {
                'multiple': True,
                'options': [
                    {
                        'button_label': a['button_label'],
                        'api_endpoint': a['api_endpoint'],
                        'target_state': a['target_state'],
                        'icon': a.get('icon', ''),
                        'description': a.get('description', ''),
                    }
                    for a in action['actions']
                ],
                'worker_prompt': action.get('worker_prompt', ''),
            }
    
    # Format current task
    if current_task:
        response['current_task'] = {
            'id': current_task.pk,
            'task_type': current_task.task_type,
            'task_type_display': current_task.get_task_type_display(),
            'scheduled_at': current_task.scheduled_at.strftime('%d/%m/%Y %H:%M'),
            'status': current_task.status,
            'is_overdue': current_task.is_overdue,
        }
    
    # Format timeline
    for event in timeline:
        response['timeline'].append({
            'from_state': event['from_state'],
            'to_state': event['to_state'],
            'timestamp': event['timestamp'].strftime('%d/%m/%Y %H:%M'),
            'actor': event['actor'],
            'reason': event['reason'],
        })
    
    return JsonResponse(response)


@login_required
@require_http_methods(["POST"])
def execute_action(request):
    """
    Execute a worker action (state transition + task completion).
    
    POST /api/worker/action/
    {
        "barcode": "TH-PK-001",
        "action_endpoint": "/api/tasks/freeze/start/",
        "actor": "สมชาย",
        "notes": "",
        "temperature": -16.5  (optional, for temp checks)
    }
    
    Returns:
        - Updated package state
        - Completed task (if applicable)
        - Audit trail confirmation
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    barcode = data.get('barcode', '').strip()
    action_endpoint = data.get('action_endpoint', '')
    # CRITICAL: Use authenticated user identity, never trust client input
    if request.user.is_authenticated:
        actor = request.user.get_full_name() or request.user.username
    else:
        actor = 'anonymous'
    notes = data.get('notes', '')
    temperature = data.get('temperature')
    
    if not barcode:
        return JsonResponse({'error': 'กรุณาสแกนบาร์โค้ด'}, status=400)
    
    if not action_endpoint:
        return JsonResponse({'error': 'ไม่ได้ระบุการดำเนินการ'}, status=400)
    
    # Find package
    from inventory.models import Package
    try:
        package = Package.objects.select_related('product').get(barcode=barcode)
    except Package.DoesNotExist:
        return JsonResponse({'error': 'ไม่พบสินค้าในระบบ'}, status=404)
    
    # Map action endpoints to actual operations
    try:
        with transaction.atomic():
            result = _execute_action_by_endpoint(
                package=package,
                action_endpoint=action_endpoint,
                actor=actor,
                notes=notes,
                temperature=temperature,
            )
            
            # Log temperature if provided
            if temperature is not None and package.storage_location:
                _log_temperature(package.storage_location, temperature, actor)
            
            return JsonResponse(result)
    
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'error': f'เกิดข้อผิดพลาด: {str(e)}'}, status=500)


def _execute_action_by_endpoint(package, action_endpoint, actor, notes='', temperature=None):
    """Route action to the correct service function."""
    from common.state_machine import transition_package
    from planning.models import ThawQueueEntry, QueueStatus
    from operations.models import WorkerTask, TaskEvent, TaskType, TaskStatus
    
    state = package.current_state
    result = {'message': '', 'new_state': '', 'task_completed': False}
    
    if action_endpoint == '/api/tasks/freeze/start/':
        # PACKED → FREEZING or REFREEZE_PENDING → FREEZING
        transition_package(package, 'FREEZING', actor=actor, reason='เริ่มแช่แข็ง')
        result['message'] = 'เริ่มแช่แข็งสำเร็จ'
        result['new_state'] = 'FREEZING'
        
    elif action_endpoint == '/api/tasks/freeze/complete/':
        # FREEZING → FROZEN
        transition_package(package, 'FROZEN', actor=actor, reason='แช่แข็งเสร็จสิ้น')
        result['message'] = 'แช่แข็งเสร็จสิ้น'
        result['new_state'] = 'FROZEN'
        
    elif action_endpoint == '/api/tasks/thaw/queue/':
        # FROZEN → THAW_QUEUED (MUST have rotation plan)
        from planning.services import add_to_thaw_queue
        from planning.models import RotationPlan
        
        # Check package state first
        if package.current_state != 'FROZEN':
            raise ValueError(
                f'สินค้าต้องอยู่ในสถานะ FROZEN เพื่อใส่คิวละลาย '
                f'(สถานะปัจจุบัน: {package.get_current_state_display()})'
            )
        
        # Check rotation plan exists
        rotation_plan = RotationPlan.objects.filter(package=package).first()
        if not rotation_plan:
            raise ValueError(
                'สินค้าชิ้นนี้ยังไม่มีแผนงาน RotationPlan '
                'กรุณาสร้างแผนงานก่อนใส่คิวละลาย'
            )
        
        add_to_thaw_queue(package, rotation_plan, actor=actor)
        result['message'] = 'ใส่คิวละลายน้ำแข็งสำเร็จ'
        result['new_state'] = 'THAW_QUEUED'
        
    elif action_endpoint == '/api/tasks/thaw/start/':
        # THAW_QUEUED → THAWING
        queue_entry = ThawQueueEntry.objects.filter(
            package=package,
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
        ).first()
        if queue_entry:
            queue_entry.status = QueueStatus.STARTED
            queue_entry.save(update_fields=['status'])
        transition_package(package, 'THAWING', actor=actor, reason='เริ่มละลายน้ำแข็ง')
        result['message'] = 'เริ่มละลายน้ำแข็งสำเร็จ'
        result['new_state'] = 'THAWING'
        
    elif action_endpoint == '/api/tasks/thaw/complete/':
        # THAWING → READY_FOR_SALE
        queue_entry = ThawQueueEntry.objects.filter(
            package=package,
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED]
        ).first()
        if queue_entry:
            queue_entry.status = QueueStatus.COMPLETED
            queue_entry.save(update_fields=['status'])
        transition_package(package, 'READY_FOR_SALE', actor=actor, reason='ละลายน้ำแข็งเสร็จ')
        result['message'] = 'ละลายน้ำแข็งเสร็จสิ้น — พร้อมขาย'
        result['new_state'] = 'READY_FOR_SALE'
        
    elif action_endpoint == '/api/tasks/display/start/':
        # READY_FOR_SALE → ON_DISPLAY
        transition_package(package, 'ON_DISPLAY', actor=actor, reason='วางจำหน่าย')
        result['message'] = 'ย้ายไปวางจำหน่ายสำเร็จ'
        result['new_state'] = 'ON_DISPLAY'
        
    elif action_endpoint == '/api/tasks/display/refreeze/':
        # ON_DISPLAY → REFREEZE_PENDING
        transition_package(package, 'REFREEZE_PENDING', actor=actor, reason='กลับแช่แข็ง')
        result['message'] = 'รอการแช่แข็ง'
        result['new_state'] = 'REFREEZE_PENDING'
        
    elif action_endpoint == '/api/tasks/display/process/':
        # ON_DISPLAY → PROCESSING
        transition_package(package, 'PROCESSING', actor=actor, reason='แปรรูป')
        result['message'] = 'นำไปแปรรูป'
        result['new_state'] = 'PROCESSING'
        
    elif action_endpoint == '/api/tasks/display/discard/':
        # ON_DISPLAY → DISCARDED
        transition_package(package, 'DISCARDED', actor=actor, reason='ทิ้งสินค้า')
        result['message'] = 'ทิ้งสินค้าแล้ว'
        result['new_state'] = 'DISCARDED'
        
    elif action_endpoint == '/api/tasks/process/complete/':
        # PROCESSING → COMPLETED
        transition_package(package, 'COMPLETED', actor=actor, reason='แปรรูปเสร็จ')
        result['message'] = 'แปรรูปเสร็จสิ้น'
        result['new_state'] = 'COMPLETED'
        
    elif action_endpoint == '/api/tasks/discard/complete/':
        # DISCARDED → COMPLETED
        transition_package(package, 'COMPLETED', actor=actor, reason='ยืนยันทิ้ง')
        result['message'] = 'ยืนยันการทิ้งเสร็จสิ้น'
        result['new_state'] = 'COMPLETED'
        
    else:
        raise ValueError(f'ไม่รู้จัก action endpoint: {action_endpoint}')
    
    # Auto-complete related pending tasks
    task_type_map = {
        'FREEZE_START': 'FREEZING',
        'FREEZE_CHECK': 'FROZEN',
        'MOVE_TO_THAW_QUEUE': 'THAW_QUEUED',
        'THAW_START': 'THAWING',
        'THAW_CHECK': 'READY_FOR_SALE',
        'THAW_COMPLETE': 'READY_FOR_SALE',
        'MOVE_TO_DISPLAY': 'ON_DISPLAY',
    }
    
    # Complete any pending tasks that match this transition
    completed_tasks = WorkerTask.objects.filter(
        package=package,
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    )
    for task in completed_tasks:
        task.status = TaskStatus.COMPLETED
        task.completed_at = timezone.now()
        task.completed_by = actor
        task.notes = notes
        task.save(update_fields=['status', 'completed_at', 'completed_by', 'notes', 'updated_at'])
        TaskEvent.objects.create(
            task=task,
            event_type='TASK_COMPLETED',
            timestamp=timezone.now(),
            actor=actor,
            notes=f'Auto-completed via worker action'
        )
        result['task_completed'] = True
    
    result['package'] = {
        'id': package.pk,
        'product_name': package.product.name,
        'weight': str(package.weight),
        'barcode': package.barcode,
        'current_state': package.current_state,
        'state_display': package.get_current_state_display(),
    }
    
    return result


def _log_temperature(location, temperature, actor):
    """Log a temperature reading."""
    from inventory.models import TemperatureLog, StorageLocation
    
    # Get target temp from location's freeze profile
    target = None
    min_allowed = None
    max_allowed = None
    
    if location.location_type == 'FREEZER':
        target = -18.0
        min_allowed = -25.0
        max_allowed = -15.0
    elif location.location_type == 'THAW_AREA':
        target = 2.0
        min_allowed = 0.0
        max_allowed = 5.0
    elif location.location_type == 'DISPLAY':
        target = 4.0
        min_allowed = 0.0
        max_allowed = 8.0
    
    status = 'OK'
    if min_allowed is not None and temperature < min_allowed:
        status = 'CRITICAL'
    elif max_allowed is not None and temperature > max_allowed:
        status = 'CRITICAL'
    elif max_allowed is not None and temperature > (max_allowed - 2):
        status = 'WARNING'
    elif min_allowed is not None and temperature < (min_allowed + 2):
        status = 'WARNING'
    
    TemperatureLog.objects.create(
        location=location,
        actual_temperature=temperature,
        target_temperature=target,
        min_allowed=min_allowed,
        max_allowed=max_allowed,
        status=status,
        source='MANUAL',
        recorded_by=actor,
    )


@require_http_methods(["GET"])
def urgent_tasks(request):
    """
    Get all urgent tasks across all packages.
    Used for the worker urgent tasks dashboard.
    """
    from operations.models import WorkerTask, TaskStatus
    from common.worker_actions import get_next_action, get_state_urgency
    
    now = timezone.now()
    
    tasks = WorkerTask.objects.filter(
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE]
    ).select_related('package', 'package__product', 'rotation_plan').order_by('scheduled_at')
    
    result = []
    for task in tasks:
        urgency = get_state_urgency(task.package)
        action = get_next_action(task.package)
        
        # Time info
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
        
        action_label = None
        action_endpoint = None
        if action:
            if action.get('button_label'):
                action_label = action['button_label']
                action_endpoint = action['api_endpoint']
            elif action.get('actions'):
                action_label = action['actions'][0]['button_label']
                action_endpoint = action['actions'][0]['api_endpoint']
        
        result.append({
            'task_id': task.pk,
            'package_id': task.package.pk,
            'package_name': task.package.display_name,
            'barcode': task.package.barcode,
            'product_name': task.package.product.name,
            'weight': str(task.package.weight),
            'task_type': task.task_type,
            'task_type_display': task.get_task_type_display(),
            'scheduled_at': task.scheduled_at.strftime('%H:%M'),
            'package_state': task.package.current_state,
            'urgency': urgency,
            'action_label': action_label,
            'action_endpoint': action_endpoint,
            'time_display': time_display,
            'is_overdue': is_overdue,
        })
    
    return JsonResponse({'tasks': result})


@require_http_methods(["GET"])
def worker_stats(request):
    """Get summary stats for the worker dashboard."""
    from operations.models import WorkerTask, TaskStatus
    
    now = timezone.now()
    today = now.date()
    
    # Count by urgency
    all_pending = WorkerTask.objects.filter(
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE]
    )
    
    overdue = all_pending.filter(scheduled_at__lt=now).count()
    due_now = all_pending.filter(
        scheduled_at__date=today,
        scheduled_at__gte=now - timedelta(minutes=30),
        scheduled_at__lte=now
    ).count()
    due_soon = all_pending.filter(
        scheduled_at__date=today,
        scheduled_at__gt=now,
        scheduled_at__lte=now + timedelta(hours=2)
    ).count()
    today_total = all_pending.filter(scheduled_at__date=today).count()
    
    return JsonResponse({
        'overdue': overdue,
        'due_now': due_now,
        'due_soon': due_soon,
        'today_total': today_total,
    })
