"""
Worker Action Service — determines what a worker must do next for each package.

Powers the barcode-first worker UI:
  scan barcode → see package state → get next action → execute → state transitions.

Each state maps to actions with:
  - button_label: what to show the worker
  - api_endpoint: where to POST
  - target_state: what state results
  - icon, color: UI hints
"""
from common.state_machine import TRANSITIONS


# ============================================================
# ACTION DEFINITIONS PER STATE
# ============================================================

ACTIONS = {
    'PACKED': {
        'button_label': '🧊 เริ่มแช่แข็ง',
        'api_endpoint': '/api/tasks/freeze/start/',
        'target_state': 'FREEZING',
        'icon': '🧊',
        'color': 'blue',
        'description': 'นำเข้าตู้แช่แข็ง',
        'worker_prompt': 'นำสินค้าไปแช่แข็ง',
    },
    'FREEZING': {
        'button_label': '✅ เสร็จแช่แข็ง',
        'api_endpoint': '/api/tasks/freeze/complete/',
        'target_state': 'FROZEN',
        'icon': '✅',
        'color': 'blue',
        'description': 'แช่แข็งเสร็จสิ้น — ย้ายไปที่เก็บ',
        'worker_prompt': 'ตรวจสอบอุณหภูมิแล้วยืนยัน',
        'task_type': 'FREEZE_CHECK',
    },
    'FROZEN': {
        'button_label': '📋 ใส่คิวละลาย',
        'api_endpoint': '/api/tasks/thaw/queue/',
        'target_state': 'THAW_QUEUED',
        'icon': '📋',
        'color': 'orange',
        'description': 'ใส่ในคิวละลายน้ำแข็ง',
        'worker_prompt': 'นำสินค้าไปใส่คิวละลายน้ำแข็ง',
    },
    'THAW_QUEUED': {
        'button_label': '🔄 เริ่มละลาย',
        'api_endpoint': '/api/tasks/thaw/start/',
        'target_state': 'THAWING',
        'icon': '🔄',
        'color': 'orange',
        'description': 'เริ่มละลายน้ำแข็ง',
        'worker_prompt': 'นำสินค้าไปละลายน้ำแข็ง',
    },
    'THAWING': {
        'button_label': '✅ เสร็จละลาย',
        'api_endpoint': '/api/tasks/thaw/complete/',
        'target_state': 'READY_FOR_SALE',
        'icon': '✅',
        'color': 'green',
        'description': 'ละลายน้ำแข็งเสร็จ — พร้อมขาย',
        'worker_prompt': 'ตรวจสอบอุณหภูมิแล้วยืนยัน',
        'task_type': 'THAW_CHECK',
    },
    'READY_FOR_SALE': {
        'button_label': '🛒 วางจำหน่าย',
        'api_endpoint': '/api/tasks/display/start/',
        'target_state': 'ON_DISPLAY',
        'icon': '🛒',
        'color': 'green',
        'description': 'ย้ายไปวางจำหน่าย',
        'worker_prompt': 'นำสินค้าไปวางบนชั้น',
    },
    'ON_DISPLAY': {
        'button_label': None,
        'actions': [
            {
                'button_label': '❄️ กลับแช่แข็ง',
                'api_endpoint': '/api/tasks/display/refreeze/',
                'target_state': 'REFREEZE_PENDING',
                'icon': '❄️',
                'color': 'blue',
                'description': 'นำกลับไปแช่แข็ง',
            },
            {
                'button_label': '🔪 แปรรูป',
                'api_endpoint': '/api/tasks/display/process/',
                'target_state': 'PROCESSING',
                'icon': '🔪',
                'color': 'purple',
                'description': 'นำไปแปรรูป',
            },
            {
                'button_label': '🗑️ ทิ้ง',
                'api_endpoint': '/api/tasks/display/discard/',
                'target_state': 'DISCARDED',
                'icon': '🗑️',
                'color': 'red',
                'description': 'ทิ้งสินค้า',
            },
        ],
        'worker_prompt': 'เลือกการดำเนินการ',
    },
    'REFREEZE_PENDING': {
        'button_label': '🧊 เริ่มแช่แข็ง',
        'api_endpoint': '/api/tasks/freeze/start/',
        'target_state': 'FREEZING',
        'icon': '🧊',
        'color': 'blue',
        'description': 'เริ่มแช่แข็งอีกครั้ง',
        'worker_prompt': 'นำสินค้าไปแช่แข็ง',
    },
    'PROCESSING': {
        'button_label': '✅ เสร็จแปรรูป',
        'api_endpoint': '/api/tasks/process/complete/',
        'target_state': 'COMPLETED',
        'icon': '✅',
        'color': 'purple',
        'description': 'แปรรูปเสร็จสิ้น',
        'worker_prompt': 'ยืนยันแปรรูปเสร็จ',
    },
    'DISCARDED': {
        'button_label': '✅ ยืนยันทิ้ง',
        'api_endpoint': '/api/tasks/discard/complete/',
        'target_state': 'COMPLETED',
        'icon': '✅',
        'color': 'red',
        'description': 'ยืนยันการทิ้ง',
        'worker_prompt': 'ยืนยันการทิ้งสินค้า',
    },
}


def get_next_action(package):
    """
    Get the next action for a package based on its current state.

    Args:
        package: Package instance

    Returns:
        dict: Action info with button_label, api_endpoint, target_state, etc.
        None: If package is in a terminal state (COMPLETED)
    """
    state = package.current_state
    return ACTIONS.get(state)


def get_state_urgency(package):
    """
    Determine urgency level for a package based on state and tasks.

    Returns:
        dict: {level, color, label, icon}
    """
    from operations.models import WorkerTask, TaskStatus
    from django.utils import timezone
    from datetime import timedelta

    now = timezone.now()
    task = WorkerTask.objects.filter(
        package=package,
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    ).order_by('scheduled_at').first()

    if not task:
        if package.current_state in ['PACKED', 'REFREEZE_PENDING']:
            return {'level': 'ACTION_REQUIRED', 'color': 'red', 'label': 'ต้องทำตอนนี้', 'icon': '🔴'}
        return {'level': 'OK', 'color': 'green', 'label': 'ปกติ', 'icon': '🟢'}

    if task.status == TaskStatus.OVERDUE:
        return {'level': 'OVERDUE', 'color': 'black', 'label': 'เกินกำหนด', 'icon': '⚫', 'task': task}

    if task.scheduled_at <= now:
        return {'level': 'ACTION_REQUIRED', 'color': 'red', 'label': 'ถึงเวลาทำแล้ว', 'icon': '🔴', 'task': task}

    time_until = task.scheduled_at - now
    if time_until <= timedelta(hours=1):
        return {'level': 'DUE_SOON', 'color': 'yellow', 'label': 'อีกไม่ถึง 1 ชม.', 'icon': '🟡', 'task': task}
    if time_until <= timedelta(hours=3):
        hours = int(time_until.total_seconds() // 3600)
        return {'level': 'UPCOMING', 'color': 'orange', 'label': f'อีก {hours} ชม.', 'icon': '🟠', 'task': task}

    return {'level': 'SCHEDULED', 'color': 'green', 'label': 'กำหนดทำทีหลัง', 'icon': '🟢', 'task': task}
