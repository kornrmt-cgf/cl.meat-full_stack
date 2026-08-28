"""
Meat Rotation Planner — Backend Calculation Functions

Weight-dependent schedule calculation:
- calculate_freeze_duration(product)
- calculate_thaw_duration(product)
- calculate_rotation_schedule(product, target_ready_at, ...)
- generate_worker_tasks(schedule)
"""

from datetime import timedelta
from django.utils import timezone


# ============================================================
# CONSTANTS
# ============================================================

# Freeze parameters (Newton's cooling law approximation)
# base × mass_kg^0.67
FREEZE_BASE_STANDARD = 5.0   # hours for -8°C
FREEZE_BASE_FAST = 2.5       # hours for -18°C

THAW_BASE_STANDARD = 12.0    # hours (from -8°C to ~0°C)
THAW_BASE_FAST = 6.0         # hours (from -18°C to ~0°C)

DEFAULT_BUFFER_MINUTES = 120  # 2 hours buffer before target ready


# ============================================================
# FREEZE DURATION
# ============================================================

def calculate_freeze_duration(weight_grams, freezer_temp=-8):
    """
    คำนวณระยะเวลาแช่แข็งโดยประมาณจากน้ำหนักและอุณหภูมิ

    Args:
        weight_grams: น้ำหนักสินค้า (กรัม)
        freezer_temp: อุณหภูมิตู้แช่ (-8 หรือ -18)

    Returns:
        dict: {
            'estimated_minutes': int,
            'estimated_hours': float,
            'formula': str,
        }
    """
    mass_kg = weight_grams / 1000.0

    if freezer_temp <= -15:
        base = FREEZE_BASE_FAST
    else:
        base = FREEZE_BASE_STANDARD

    # Newton's cooling: t = base × mass^0.67
    hours = base * (mass_kg ** 0.67)
    minutes = int(hours * 60)

    return {
        'estimated_minutes': minutes,
        'estimated_hours': round(hours, 1),
        'formula': f'{base}h × {mass_kg}kg^0.67 = {hours:.1f}h',
    }


# ============================================================
# THAW DURATION
# ============================================================

def calculate_thaw_duration(weight_grams, freezer_temp=-8):
    """
    คำนวณระยะเวลาละลายโดยประมาณจากน้ำหนัก

    Args:
        weight_grams: น้ำหนักสินค้า (กรัม)
        freezer_temp: อุณหภูมิตู้แช่เดิม (-8 หรือ -18)

    Returns:
        dict: {
            'estimated_minutes': int,
            'estimated_hours': float,
            'formula': str,
        }
    """
    mass_kg = weight_grams / 1000.0

    if freezer_temp <= -15:
        base = THAW_BASE_FAST
    else:
        base = THAW_BASE_STANDARD

    hours = base * (mass_kg ** 0.67)
    minutes = int(hours * 60)

    return {
        'estimated_minutes': minutes,
        'estimated_hours': round(hours, 1),
        'formula': f'{base}h × {mass_kg}kg^0.67 = {hours:.1f}h',
    }


# ============================================================
# ROTATION SCHEDULE
# ============================================================

def calculate_rotation_schedule(
    product_list,
    target_ready_at,
    freeze_duration_minutes=None,
    thaw_duration_minutes=None,
    buffer_minutes=DEFAULT_BUFFER_MINUTES,
    freezer_temp=None,
):
    """
    คำนวณแผนการหมุนเวียนทั้งหมดย้อนกลับจาก target_ready_at

    Args:
        product_list: Product_list instance
        target_ready_at: datetime ที่ต้องการให้พร้อมจำหน่าย
        freeze_duration_minutes: override (ถ้า None = คำนวณจากน้ำหนัก)
        thaw_duration_minutes: override (ถ้า None = คำนวณจากน้ำหนัก)
        buffer_minutes: เวลา buffer ก่อน target (default 120 = 2 ชม.)
        freezer_temp: อุณหภูมิตู้ (ถ้า None = ใช้ freeze_target_temp)

    Returns:
        dict: {
            'freeze_start_at': datetime,
            'freeze_end_at': datetime,
            'thaw_start_at': datetime,
            'target_ready_at': datetime,
            'freeze_duration_minutes': int,
            'thaw_duration_minutes': int,
            'buffer_minutes': int,
            'is_override': bool,
            'freeze_estimated': int,
            'thaw_estimated': int,
        }
    """
    if freezer_temp is None:
        freezer_temp = product_list.freeze_target_temp or -8

    # Calculate estimated durations
    freeze_est = calculate_freeze_duration(product_list.weight, freezer_temp)
    thaw_est = calculate_thaw_duration(product_list.weight, freezer_temp)

    freeze_mins = freeze_duration_minutes or freeze_est['estimated_minutes']
    thaw_mins = thaw_duration_minutes or thaw_est['estimated_minutes']

    is_override = (
        freeze_duration_minutes is not None
        or thaw_duration_minutes is not None
    )

    # Work backwards from target_ready_at
    # target_ready_at = time when product must be ready to sell
    thaw_start_at = target_ready_at - timedelta(minutes=thaw_mins + buffer_minutes)
    freeze_end_at = thaw_start_at  # freeze must end when thaw starts
    freeze_start_at = freeze_end_at - timedelta(minutes=freeze_mins)

    return {
        'freeze_start_at': freeze_start_at,
        'freeze_end_at': freeze_end_at,
        'thaw_start_at': thaw_start_at,
        'target_ready_at': target_ready_at,
        'freeze_duration_minutes': freeze_mins,
        'thaw_duration_minutes': thaw_mins,
        'buffer_minutes': buffer_minutes,
        'is_override': is_override,
        'freeze_estimated': freeze_est['estimated_minutes'],
        'thaw_estimated': thaw_est['estimated_minutes'],
    }


# ============================================================
# GENERATE WORKER TASKS
# ============================================================

def generate_worker_tasks(schedule):
    """
    สร้าง WorkerTask จาก RotationSchedule

    Args:
        schedule: RotationSchedule instance

    Returns:
        list[WorkerTask]: tasks ที่สร้างแล้ว
    """
    from stock_meat.models import WorkerTask

    # Delete existing tasks for this schedule
    schedule.tasks.all().delete()

    tasks = []

    # 1. Freeze start
    if schedule.freeze_start_at:
        tasks.append(WorkerTask(
            rotation_schedule=schedule,
            task_type='freeze_start',
            scheduled_at=schedule.freeze_start_at,
        ))

    # 2. Thaw queue (before thaw start)
    if schedule.thaw_start_at:
        queue_time = schedule.thaw_start_at - timedelta(minutes=30)
        tasks.append(WorkerTask(
            rotation_schedule=schedule,
            task_type='thaw_queue',
            scheduled_at=queue_time,
        ))

    # 3. Thaw start
    if schedule.thaw_start_at:
        tasks.append(WorkerTask(
            rotation_schedule=schedule,
            task_type='thaw_start',
            scheduled_at=schedule.thaw_start_at,
        ))

    # 4. Display start (at target ready)
    tasks.append(WorkerTask(
        rotation_schedule=schedule,
        task_type='display_start',
        scheduled_at=schedule.target_ready_at,
    ))

    created = WorkerTask.objects.bulk_create(tasks)
    return created


# ============================================================
# GET TODAY'S TASKS
# ============================================================

def get_tasks_for_date(target_date=None):
    """
    ดึงงานทั้งหมดสำหรับวันที่กำหนด

    Args:
        target_date: date object (ถ้า None = วันนี้)

    Returns:
        QuerySet of WorkerTask
    """
    from stock_meat.models import WorkerTask

    if target_date is None:
        target_date = timezone.now().date()

    start = timezone.make_aware(
        timezone.datetime.combine(target_date, timezone.datetime.min.time())
    )
    end = start + timedelta(days=1)

    return WorkerTask.objects.filter(
        scheduled_at__gte=start,
        scheduled_at__lt=end,
    ).select_related(
        'rotation_schedule',
        'rotation_schedule__product_list',
        'rotation_schedule__product_list__product',
        'rotation_schedule__product_list__product__name',
    ).order_by('scheduled_at')
