from django.utils import timezone
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Q, Count, Max
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from datetime import timedelta

from stock_meat.models import (
    Product_info,
    Product_list,
    FreezeRotation,
)


# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_THAW_HOURS = 24
DEFAULT_DISPLAY_DAYS = 3


# ============================================================
# FREEZE DASHBOARD
# ============================================================

@require_GET
def freeze_dashboard(request):
    """
    หน้า Dashboard สำหรับ Freeze Queue

    แสดง:
    - สินค้าที่กำลังละลาย (thawing)
    - สินค้าที่กำลังวางขาย (display)
    - สินค้าที่รอละลาย (frozen, มีในคิว)
    - แจ้งเตือนที่ต้องทำรายการ
    """

    # --------------------------------------------------------
    # กำลังละลาย
    # --------------------------------------------------------

    thawing_products = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
            'product__type_product',
            'product__import_from',
        )
        .filter(
            storage_status='thawing',
        )
        .order_by('thaw_queue_position')
    )

    # --------------------------------------------------------
    # กำลังวางขาย
    # --------------------------------------------------------

    display_products = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
            'product__type_product',
            'product__import_from',
        )
        .filter(
            storage_status='display',
        )
        .order_by('entered_display_at')
    )

    # --------------------------------------------------------
    # สินค้าที่หมดอายุวางขาย
    # (ต้องนำกลับแช่)
    # --------------------------------------------------------

    display_expired = []
    display_expiring_soon = []

    for product in display_products:
        remaining = product.display_days_remaining

        if remaining is not None:
            if remaining <= 0:
                display_expired.append(product)
            elif remaining <= 1:
                display_expiring_soon.append(product)

    # --------------------------------------------------------
    # รอละลาย (frozen ที่อยู่ในคิว)
    # --------------------------------------------------------

    frozen_in_queue = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
            'product__type_product',
            'product__import_from',
        )
        .filter(
            storage_status='frozen',
            thaw_queue_position__gt=0,
        )
        .order_by('thaw_queue_position')
    )

    # --------------------------------------------------------
    # แช่แข็ง ไม่ได้อยู่ในคิว
    # --------------------------------------------------------

    frozen_available = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
            'product__type_product',
            'product__import_from',
        )
        .filter(
            storage_status='frozen',
            thaw_queue_position=0,
        )
        .order_by(
            '-rotate_priority',
            'mfg',
        )
    )

    # --------------------------------------------------------
    # ละลายเสร็จแล้ว พร้อมวางขาย
    # --------------------------------------------------------

    thaw_ready = []
    for product in thawing_products:
        if product.is_thaw_complete:
            thaw_ready.append(product)

    # --------------------------------------------------------
    # สินค้าแช่แข็งที่มีกำหนดละลาย
    # --------------------------------------------------------

    thaw_upcoming = (
        Product_list.objects
        .select_related(
            'product', 'product__name',
        )
        .filter(
            storage_status='frozen',
            freeze_end_at__isnull=False,
        )
        .order_by('freeze_end_at')
    )

    # --------------------------------------------------------
    # แจ้งเตือนที่ต้องทำ
    # --------------------------------------------------------

    alerts = []

    if display_expired:
        alerts.append({
            'type': 'danger',
            'icon': '🔴',
            'title': 'สินค้าหมดอายุวางขาย!',
            'message': (
                f'มี {len(display_expired)} รายการ '
                'ที่ต้องนำกลับแช่ทันที'
            ),
            'count': len(display_expired),
            'action': 'freeze_return',
        })

    if display_expiring_soon:
        alerts.append({
            'type': 'warning',
            'icon': '🟡',
            'title': 'สินค้าใกล้หมดอายุวางขาย',
            'message': (
                f'มี {len(display_expiring_soon)} รายการ '
                'ที่เหลือเวลาวางขาย ≤ 1 วัน'
            ),
            'count': len(display_expiring_soon),
            'action': 'schedule_replacement',
        })

    if thaw_ready:
        alerts.append({
            'type': 'success',
            'icon': '🟢',
            'title': 'ละลายเสร็จแล้ว!',
            'message': (
                f'มี {len(thaw_ready)} รายการ '
                'พร้อมนำออกมาวางขาย'
            ),
            'count': len(thaw_ready),
            'action': 'start_display',
        })

    # --------------------------------------------------------
    # สถิติ
    # --------------------------------------------------------

    stats = {
        'total_frozen': (
            Product_list.objects
            .filter(storage_status='frozen')
            .count()
        ),
        'total_thawing': thawing_products.count(),
        'total_display': display_products.count(),
        'total_alerts': len(alerts),
        'queue_length': frozen_in_queue.count(),
    }

    return JsonResponse({
        'success': True,
        'data': {
            'thawing': [
                _serialize_product(p)
                for p in thawing_products
            ],
            'display': [
                _serialize_product(p, include_display_info=True)
                for p in display_products
            ],
            'display_expired': [
                _serialize_product(p, include_display_info=True)
                for p in display_expired
            ],
            'display_expiring_soon': [
                _serialize_product(p, include_display_info=True)
                for p in display_expiring_soon
            ],
            'frozen_in_queue': [
                _serialize_product(p)
                for p in frozen_in_queue
            ],
            'frozen_available': [
                _serialize_product(p)
                for p in frozen_available
            ],
            'thaw_ready': [
                _serialize_product(p)
                for p in thaw_ready
            ],
            'thaw_upcoming': [
                _serialize_product(p)
                for p in thaw_upcoming
            ],
            'alerts': alerts,
            'stats': stats,
        }
    })


# ============================================================
# START THAW
# ============================================================

@csrf_exempt
@require_POST
def start_thaw(request):
    """
    เริ่มละลายสินค้า

    POST:
        product_id = Product_list ID
        thaw_duration_hours = ชั่วโมงที่ต้องการละลาย (default 24)
    """
    product_id = request.POST.get('product_id')
    duration_raw = request.POST.get(
        'thaw_duration_hours',
        str(DEFAULT_THAW_HOURS),
    )

    if not product_id:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุสินค้า',
        }, status=400)

    try:
        duration = int(duration_raw)
    except (TypeError, ValueError):
        duration = DEFAULT_THAW_HOURS

    if duration < 12 or duration > 48:
        return JsonResponse({
            'success': False,
            'message': 'เวลาละลายต้องอยู่ระหว่าง 12-48 ชั่วโมง',
        }, status=400)

    with transaction.atomic():
        product = (
            Product_list.objects
            .select_for_update()
            .select_related('product', 'product__name')
            .get(id=product_id)
        )

        if product.storage_status != 'frozen':
            return JsonResponse({
                'success': False,
                'message': (
                    f'สินค้านี้อยู่ในสถานะ '
                    f'"{product.get_storage_status_display()}" '
                    f'ไม่สามารถเริ่มละลายได้'
                ),
            }, status=400)

        # ------------------------------------------
        # หาลำดับถัดไปในคิว
        # ------------------------------------------

        max_queue = (
            Product_list.objects
            .filter(
                storage_status='thawing',
                thaw_queue_position__gt=0,
            )
            .aggregate(
                max_pos=Max('thaw_queue_position')
            )
            .get('max_pos') or 0
        )

        new_queue_position = max_queue + 1

        # ------------------------------------------
        # อัปเดตสถานะ
        # ------------------------------------------

        status_before = product.storage_status

        product.storage_status = 'thawing'
        product.thaw_started_at = timezone.now()
        product.thaw_duration_hours = duration
        product.thaw_queue_position = new_queue_position
        product.save(update_fields=[
            'storage_status',
            'thaw_started_at',
            'thaw_duration_hours',
            'thaw_queue_position',
        ])

        # ------------------------------------------
        # บันทึกประวัติ
        # ------------------------------------------

        FreezeRotation.objects.create(
            product_list=product,
            action='thaw_start',
            notes=(
                f'เริ่มละลาย ใช้เวลา {duration} ชั่วโมง '
                f'(คิวที่ {new_queue_position})'
            ),
            weight_at_action=float(product.weight),
            status_before=status_before,
            status_after='thawing',
        )

    return JsonResponse({
        'success': True,
        'message': (
            f'เริ่มละลาย "{product}" '
            f'ใช้เวลา {duration} ชั่วโมง '
            f'(คิวที่ {new_queue_position})'
        ),
        'product': _serialize_product(product),
        'thaw_ready_at': (
            product.thaw_ready_at.isoformat()
            if product.thaw_ready_at
            else None
        ),
    })


# ============================================================
# COMPLETE THAW -> START DISPLAY
# ============================================================

@csrf_exempt
@require_POST
def complete_thaw(request):
    """
    ละลายเสร็จแล้ว → นำออกมาวางขาย

    POST:
        product_id = Product_list ID
        display_days = จำนวนวันที่ต้องการวางขาย (default 3)
    """
    product_id = request.POST.get('product_id')
    display_days_raw = request.POST.get(
        'display_days',
        str(DEFAULT_DISPLAY_DAYS),
    )

    if not product_id:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุสินค้า',
        }, status=400)

    try:
        display_days = int(display_days_raw)
    except (TypeError, ValueError):
        display_days = DEFAULT_DISPLAY_DAYS

    if display_days < 1 or display_days > 7:
        return JsonResponse({
            'success': False,
            'message': 'จำนวนวันวางขายต้องอยู่ระหว่าง 1-7 วัน',
        }, status=400)

    with transaction.atomic():
        product = (
            Product_list.objects
            .select_for_update()
            .select_related('product', 'product__name')
            .get(id=product_id)
        )

        if product.storage_status != 'thawing':
            return JsonResponse({
                'success': False,
                'message': (
                    f'สินค้านี้อยู่ในสถานะ '
                    f'"{product.get_storage_status_display()}" '
                    f'ไม่สามารถนำออกวางขายได้'
                ),
            }, status=400)

        if not product.is_thaw_complete:
            remaining = product.thaw_hours_remaining
            return JsonResponse({
                'success': False,
                'message': (
                    f'ยังละลายไม่เสร็จ '
                    f'เหลืออีก {remaining} ชั่วโมง'
                ),
            }, status=400)

        # ------------------------------------------
        # อัปเดตสถานะ
        # ------------------------------------------

        status_before = product.storage_status
        now = timezone.now()

        product.storage_status = 'display'
        product.entered_display_at = now
        product.display_max_days = display_days
        product.thaw_started_at = None
        product.thaw_queue_position = 0
        product.save(update_fields=[
            'storage_status',
            'entered_display_at',
            'display_max_days',
            'thaw_started_at',
            'thaw_queue_position',
        ])

        # ------------------------------------------
        # บันทึกประวัติ
        # ------------------------------------------

        FreezeRotation.objects.create(
            product_list=product,
            action='display_start',
            notes=(
                f'นำออกมาวางขาย {display_days} วัน'
            ),
            weight_at_action=float(product.weight),
            status_before=status_before,
            status_after='display',
        )

    # --------------------------------------------------
    # จัดอันดับคิวละลายใหม่
    # --------------------------------------------------

    _reorder_thaw_queue()

    return JsonResponse({
        'success': True,
        'message': (
            f'นำ "{product}" ออกมาวางขาย '
            f'{display_days} วัน'
        ),
        'product': _serialize_product(
            product,
            include_display_info=True,
        ),
    })


# ============================================================
# PULL FROM DISPLAY -> FREEZE RETURN
# ============================================================

@csrf_exempt
@require_POST
def pull_from_display(request):
    """
    นำสินค้าออกจากชั้นวาง → กลับแช่แข็ง

    POST:
        product_id = Product_list ID
        reason = เหตุผล (optional)
    """
    product_id = request.POST.get('product_id')
    reason = request.POST.get('reason', '')

    if not product_id:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุสินค้า',
        }, status=400)

    with transaction.atomic():
        product = (
            Product_list.objects
            .select_for_update()
            .select_related('product', 'product__name')
            .get(id=product_id)
        )

        if product.storage_status != 'display':
            return JsonResponse({
                'success': False,
                'message': (
                    f'สินค้านี้อยู่ในสถานะ '
                    f'"{product.get_storage_status_display()}" '
                    f'ไม่สามารถนำกลับแช่ได้'
                ),
            }, status=400)

        # ------------------------------------------
        # อัปเดตสถานะ
        # ------------------------------------------

        status_before = product.storage_status

        product.storage_status = 'frozen'
        product.entered_display_at = None
        product.display_max_days = DEFAULT_DISPLAY_DAYS
        product.thaw_started_at = None
        product.thaw_queue_position = 0
        product.last_alert_at = None
        product.save(update_fields=[
            'storage_status',
            'entered_display_at',
            'display_max_days',
            'thaw_started_at',
            'thaw_queue_position',
            'last_alert_at',
        ])

        # ------------------------------------------
        # บันทึกประวัติ
        # ------------------------------------------

        FreezeRotation.objects.create(
            product_list=product,
            action='freeze_return',
            notes=reason or 'นำกลับแช่แข็ง',
            weight_at_action=float(product.weight),
            status_before=status_before,
            status_after='frozen',
        )

    return JsonResponse({
        'success': True,
        'message': (
            f'นำ "{product}" กลับแช่แข็งแล้ว'
        ),
        'product': _serialize_product(product),
    })


# ============================================================
# FREEZE AVAILABLE PRODUCTS (list all Product_list)
# ============================================================

@require_GET
def freeze_available_products(request):
    """
    รายชื่อสินค้าทั้งหมด (Product_list)
    ที่เพิ่มเข้า freeze queue ได้
    """
    products = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
            'product__type_product',
            'product__import_from',
        )
        .order_by(
            'storage_status',
            'mfg',
        )
    )

    status_labels = {
        'frozen': '❄️ แช่แข็ง',
        'thawing': '🔄 กำลังละลาย',
        'display': '🛒 วางขาย',
        'depleted': '📦 หมด',
    }

    return JsonResponse({
        'success': True,
        'products': [
            {
                'id': p.id,
                'name': (
                    p.product.name.name
                    if p.product and p.product.name
                    else ''
                ),
                'category': (
                    p.product.type_product.name_type
                    if p.product and p.product.type_product
                    else ''
                ),
                'import_from': (
                    p.product.import_from.name_place
                    if p.product and p.product.import_from
                    else ''
                ),
                'lot_number': (
                    p.product.lot_number
                    if p.product else ''
                ),
                'barcode': p.barcode,
                'weight': float(p.weight),
                'selling_price': float(p.selling_price),
                'mfg': p.mfg.strftime('%d/%m/%Y %H:%M'),
                'loyverse_sku': p.loyverse_sku or '',
                'loyverse_synced': p.loyverse_synced,
                'status': status_labels.get(
                    p.storage_status,
                    p.storage_status
                ),
                'storage_status': p.storage_status,
            }
            for p in products
        ],
    })


# ============================================================
# ADD TO QUEUE (set status for any Product_list)
# ============================================================

@csrf_exempt
@require_POST
def add_to_queue(request):
    """
    เพิ่มสินค้าเข้า freeze queue
    หรือเปลี่ยนสถานะสินค้า

    POST:
        product_id = Product_list ID
        status = frozen | thawing | display
        thaw_duration_hours = (ถ้า status=thawing)
        display_days = (ถ้า status=display)
    """
    product_id = request.POST.get('product_id')
    new_status = request.POST.get('status', 'frozen')

    if not product_id:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุสินค้า',
        }, status=400)

    if new_status not in ('frozen', 'thawing', 'display'):
        return JsonResponse({
            'success': False,
            'message': 'สถานะไม่ถูกต้อง',
        }, status=400)

    try:
        thaw_hours = int(
            request.POST.get(
                'thaw_duration_hours',
                str(DEFAULT_THAW_HOURS)
            )
        )
    except (TypeError, ValueError):
        thaw_hours = DEFAULT_THAW_HOURS

    try:
        disp_days = int(
            request.POST.get(
                'display_days',
                str(DEFAULT_DISPLAY_DAYS)
            )
        )
    except (TypeError, ValueError):
        disp_days = DEFAULT_DISPLAY_DAYS

    with transaction.atomic():
        product = (
            Product_list.objects
            .select_for_update()
            .select_related('product', 'product__name')
            .get(id=product_id)
        )

        status_before = product.storage_status
        now = timezone.now()

        # ------------------------------------------
        # frozen -> แช่แข็งปกติ
        # ------------------------------------------

        if new_status == 'frozen':
            product.storage_status = 'frozen'
            product.entered_display_at = None
            product.display_max_days = DEFAULT_DISPLAY_DAYS
            product.thaw_started_at = None
            product.thaw_queue_position = 0
            product.last_alert_at = None
            product.freeze_started_at = now

            # Freeze duration (minutes)
            freeze_dur_raw = request.POST.get('freeze_duration_minutes', '')
            if freeze_dur_raw:
                try:
                    product.freeze_duration_minutes = int(freeze_dur_raw)
                except (TypeError, ValueError):
                    product.freeze_duration_minutes = 0
            else:
                product.freeze_duration_minutes = 0

            # Freeze scheduling
            freeze_end_str = request.POST.get('freeze_end_at', '')
            if freeze_end_str:
                try:
                    from django.utils.dateparse import parse_datetime
                    product.freeze_end_at = parse_datetime(freeze_end_str)
                except Exception:
                    product.freeze_end_at = None
            else:
                product.freeze_end_at = None
            freeze_tgt = request.POST.get('freeze_target_temp', '')
            if freeze_tgt:
                try:
                    product.freeze_target_temp = int(freeze_tgt)
                except (TypeError, ValueError):
                    pass
            product.save(update_fields=[
                'storage_status', 'entered_display_at', 'display_max_days',
                'thaw_started_at', 'thaw_queue_position', 'last_alert_at',
                'freeze_started_at', 'freeze_duration_minutes',
                'freeze_end_at', 'freeze_target_temp',
            ])

            dur_note = ''
            if product.freeze_duration_minutes > 0:
                h = product.freeze_duration_minutes // 60
                m = product.freeze_duration_minutes % 60
                dur_note = f', แช่ {h} ชม. {m} น.' if m else f', แช่ {h} ชม.'

            FreezeRotation.objects.create(
                product_list=product,
                action='freeze_return',
                notes=f'เพิ่มเข้าระบบแช่แข็ง{dur_note}',
                weight_at_action=float(product.weight),
                status_before=status_before,
                status_after='frozen',
            )

        # ------------------------------------------
        # thawing -> เริ่มละลาย
        # ------------------------------------------
        elif new_status == 'thawing':
            max_queue = (
                Product_list.objects
                .filter(
                    storage_status='thawing',
                    thaw_queue_position__gt=0,
                )
                .aggregate(
                    max_pos=Max('thaw_queue_position')
                )
                .get('max_pos') or 0
            )

            new_queue = max_queue + 1

            product.storage_status = 'thawing'
            product.thaw_started_at = now
            product.thaw_duration_hours = thaw_hours
            product.thaw_queue_position = new_queue
            product.save(update_fields=[
                'storage_status',
                'thaw_started_at',
                'thaw_duration_hours',
                'thaw_queue_position',
            ])

            FreezeRotation.objects.create(
                product_list=product,
                action='thaw_start',
                notes=(
                    f'เริ่มละลาย '
                    f'(คิวที่ {new_queue}, '
                    f'{thaw_hours} ชม.)'
                ),
                weight_at_action=float(product.weight),
                status_before=status_before,
                status_after='thawing',
            )

        # ------------------------------------------
        # display -> วางขายทันที
        # ------------------------------------------
        elif new_status == 'display':
            product.storage_status = 'display'
            product.entered_display_at = now
            product.display_max_days = disp_days
            product.thaw_started_at = None
            product.thaw_queue_position = 0
            product.save(update_fields=[
                'storage_status',
                'entered_display_at',
                'display_max_days',
                'thaw_started_at',
                'thaw_queue_position',
            ])

            FreezeRotation.objects.create(
                product_list=product,
                action='display_start',
                notes=(
                    f'นำออกมาวางขาย '
                    f'{disp_days} วัน'
                ),
                weight_at_action=float(product.weight),
                status_before=status_before,
                status_after='display',
            )

    status_labels = {
        'frozen': '❄️ แช่แข็ง',
        'thawing': '🔄 กำลังละลาย',
        'display': '🛒 วางขาย',
    }

    return JsonResponse({
        'success': True,
        'message': (
            f'ย้าย "{product}" '
            f'ไปสถานะ {status_labels[new_status]}'
        ),
        'product': _serialize_product(
            product,
            include_display_info=True,
        ),
    })


# ============================================================
# AUTO ROTATION CHECK
# ============================================================

@require_GET
def auto_rotation_check(request):
    """
    ตรวจสอบสถานะอัตโนมัติ
    """

    alerts = []
    now = timezone.now()

    # --------------------------------------------------------
    # 0. แช่แข็งครบเวลา → แจ้งเตือน พร้อมเข้าคิวละลาย
    #    (ไม่เปลี่ยน status อัตโนมัติ — ให้ user กด "เข้าคิวละลาย" เอง)
    # --------------------------------------------------------
    freeze_expired = (
        Product_list.objects
        .select_related('product', 'product__name')
        .filter(
            storage_status='frozen',
            freeze_end_at__isnull=False,
            freeze_end_at__lte=now,
            thaw_queue_position=0,
        )
    )
    for product in freeze_expired:
        alerts.append({
            'type': 'freeze_complete',
            'icon': '🟢',
            'title': 'แช่แข็งครบแล้ว',
            'message': f'{product.product.name} ({product.barcode}) พร้อมนำเข้าคิวละลาย',
            'products': [_serialize_product(product)],
        })

    # --------------------------------------------------------
    # 0.5 คิวละลายที่มี target_ready_at ครบเวลา → เริ่มละลาย
    # --------------------------------------------------------
    thaw_scheduled = (
        Product_list.objects
        .select_related('product', 'product__name')
        .filter(
            storage_status='frozen',
            thaw_queue_position__gt=0,
            thaw_target_ready_at__isnull=False,
            thaw_started_at__isnull=True,
        )
    )
    for product in thaw_scheduled:
        if product.thaw_target_ready_at and product.thaw_target_ready_at <= now:
            # คำนวณ thaw_started_at = target_ready_at - thaw_duration_hours
            thaw_dur = product.thaw_duration_hours or DEFAULT_THAW_HOURS
            computed_start = product.thaw_target_ready_at - timedelta(hours=thaw_dur)
            # ถ้าเลยเวลาเริ่มละลายแล้ว ให้เริ่มเลย
            actual_start = max(computed_start, now)

            product.storage_status = 'thawing'
            product.thaw_started_at = actual_start
            product.save(update_fields=[
                'storage_status', 'thaw_started_at',
            ])
            FreezeRotation.objects.create(
                product_list=product,
                action='thaw_start',
                notes=f'เริ่มละลายตามกำหนด (เป้าหมายพร้อมขาย {product.thaw_target_ready_at.strftime("%d/%m/%Y %H:%M")})',
                weight_at_action=float(product.weight),
                status_before='frozen',
                status_after='thawing',
            )
            alerts.append({
                'type': 'thaw_start_auto',
                'icon': '🔄',
                'title': 'เริ่มละลายแล้ว',
                'message': f'{product.product.name} ({product.barcode}) เริ่มละลายตามกำหนด',
                'products': [_serialize_product(product)],
            })

    # --------------------------------------------------------
    # 1. ละลายเสร็จแล้ว พร้อมวางขาย
    # --------------------------------------------------------

    thaw_ready_products = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
        )
        .filter(
            storage_status='thawing',
        )
    )

    ready_list = []
    for product in thaw_ready_products:
        if product.is_thaw_complete:
            ready_list.append(product)

    if ready_list:
        alerts.append({
            'type': 'thaw_ready',
            'icon': '✅',
            'title': 'ละลายเสร็จแล้ว',
            'message': (
                f'{len(ready_list)} รายการ '
                'พร้อมนำออกวางขาย'
            ),
            'products': [
                _serialize_product(p)
                for p in ready_list
            ],
        })

    # --------------------------------------------------------
    # 2. สินค้าหมดอายุวางขาย
    # --------------------------------------------------------

    display_products = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
        )
        .filter(
            storage_status='display',
        )
    )

    expired_list = []
    expiring_soon_list = []

    for product in display_products:
        remaining = product.display_days_remaining
        if remaining is not None:
            if remaining <= 0:
                expired_list.append(product)
            elif remaining <= 1:
                expiring_soon_list.append(product)

    if expired_list:
        alerts.append({
            'type': 'display_expired',
            'icon': '🔴',
            'title': 'สินค้าหมดอายุวางขาย!',
            'message': (
                f'{len(expired_list)} รายการ '
                'ต้องนำกลับแช่ทันที'
            ),
            'products': [
                _serialize_product(p)
                for p in expired_list
            ],
        })

    if expiring_soon_list:
        alerts.append({
            'type': 'display_expiring',
            'icon': '🟡',
            'title': 'สินค้าใกล้หมดอายุวางขาย',
            'message': (
                f'{len(expiring_soon_list)} รายการ '
                'เหลือ ≤ 1 วัน ควรเริ่มละลายตัวถัดไป'
            ),
            'products': [
                _serialize_product(p)
                for p in expiring_soon_list
            ],
        })

    # --------------------------------------------------------
    # 3. แนะนำสินค้าถัดไปที่ควรละลาย
    # --------------------------------------------------------

    next_candidates = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
        )
        .filter(
            storage_status='frozen',
            thaw_queue_position=0,
        )
        .order_by(
            '-rotate_priority',
            'mfg',
        )[:3]
    )

    # --------------------------------------------------------
    # 4. บันทึกการแจ้งเตือน
    # --------------------------------------------------------

    if alerts:
        now = timezone.now()
        all_affected_ids = []

        for alert in alerts:
            for p in alert.get('products', []):
                all_affected_ids.append(p['id'])

        if all_affected_ids:
            (
                Product_list.objects
                .filter(id__in=all_affected_ids)
                .update(last_alert_at=now)
            )

    return JsonResponse({
        'success': True,
        'alerts': alerts,
        'next_candidates': [
            _serialize_product(p)
            for p in next_candidates
        ],
    })


# ============================================================
# REORDER THAW QUEUE
# ============================================================

@csrf_exempt
@require_POST
def update_thaw_queue(request):
    """
    เปลี่ยนลำดับคิวละลาย

    POST:
        ordered_ids[] = Product_list IDs เรียงตามลำดับใหม่
    """
    ordered_ids = request.POST.getlist('ordered_ids')

    if not ordered_ids:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุลำดับใหม่',
        }, status=400)

    try:
        ids = [int(x) for x in ordered_ids]
    except (TypeError, ValueError):
        return JsonResponse({
            'success': False,
            'message': 'ID ไม่ถูกต้อง',
        }, status=400)

    with transaction.atomic():
        products = list(
            Product_list.objects
            .select_for_update()
            .filter(
                id__in=ids,
                storage_status='thawing',
            )
        )

        for idx, product in enumerate(
            products, start=1
        ):
            product.thaw_queue_position = idx
            product.save(
                update_fields=[
                    'thaw_queue_position',
                ]
            )

    return JsonResponse({
        'success': True,
        'message': 'อัปเดตลำดับคิวละลายแล้ว',
        'queue': [
            {
                'id': p.id,
                'barcode': p.barcode,
                'position': p.thaw_queue_position,
            }
            for p in products
        ],
    })


# ============================================================
# BULK THAW
# ============================================================

@csrf_exempt
@require_POST
def bulk_start_thaw(request):
    """
    เริ่มละลายหลายรายการพร้อมกัน

    POST:
        product_ids[] = Product_list IDs
        thaw_duration_hours = ชั่วโมงละลาย (default 24)
    """
    selected_ids = request.POST.getlist('product_ids')
    duration_raw = request.POST.get(
        'thaw_duration_hours',
        str(DEFAULT_THAW_HOURS),
    )

    if not selected_ids:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาเลือกรายการสินค้า',
        }, status=400)

    try:
        duration = int(duration_raw)
    except (TypeError, ValueError):
        duration = DEFAULT_THAW_HOURS

    if duration < 12 or duration > 48:
        return JsonResponse({
            'success': False,
            'message': 'เวลาละลายต้องอยู่ระหว่าง 12-48 ชั่วโมง',
        }, status=400)

    try:
        ids = [int(x) for x in selected_ids]
    except (TypeError, ValueError):
        return JsonResponse({
            'success': False,
            'message': 'ID ไม่ถูกต้อง',
        }, status=400)

    started = []
    skipped = []

    with transaction.atomic():
        products = list(
            Product_list.objects
            .select_for_update()
            .select_related('product', 'product__name')
            .filter(id__in=ids)
        )

        max_queue = (
            Product_list.objects
            .filter(
                storage_status='thawing',
                thaw_queue_position__gt=0,
            )
            .aggregate(
                max_pos=Max('thaw_queue_position')
            )
            .get('max_pos') or 0
        )

        current_queue = max_queue

        for product in products:
            if product.storage_status != 'frozen':
                skipped.append({
                    'id': product.id,
                    'barcode': product.barcode,
                    'reason': (
                        f'สถานะ "{product.get_storage_status_display()}"'
                    ),
                })
                continue

            current_queue += 1

            status_before = product.storage_status

            product.storage_status = 'thawing'
            product.thaw_started_at = timezone.now()
            product.thaw_duration_hours = duration
            product.thaw_queue_position = current_queue
            product.save(update_fields=[
                'storage_status',
                'thaw_started_at',
                'thaw_duration_hours',
                'thaw_queue_position',
            ])

            FreezeRotation.objects.create(
                product_list=product,
                action='thaw_start',
                notes=(
                    f'เริ่มละลาย (bulk) '
                    f'ใช้เวลา {duration} ชั่วโมง '
                    f'(คิวที่ {current_queue})'
                ),
                weight_at_action=float(product.weight),
                status_before=status_before,
                status_after='thawing',
            )

            started.append({
                'id': product.id,
                'barcode': product.barcode,
                'queue_position': current_queue,
            })

    return JsonResponse({
        'success': True,
        'message': (
            f'เริ่มละลาย {len(started)} รายการ, '
            f'ข้าม {len(skipped)} รายการ'
        ),
        'started': started,
        'skipped': skipped,
    })


# ============================================================
# ROTATION HISTORY
# ============================================================

@require_GET
def rotation_history(request):
    """
    ประวัติการหมุนเวียน
    """
    product_id = request.GET.get('product_id')
    limit_raw = request.GET.get('limit', '50')

    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 50

    queryset = (
        FreezeRotation.objects
        .select_related(
            'product_list',
            'product_list__product',
            'product_list__product__name',
        )
    )

    if product_id:
        try:
            queryset = queryset.filter(
                product_list_id=int(product_id)
            )
        except (TypeError, ValueError):
            pass

    history = queryset[:limit]

    return JsonResponse({
        'success': True,
        'history': [
            {
                'id': h.id,
                'product_list_id': h.product_list_id,
                'barcode': (
                    h.product_list.barcode
                    if h.product_list
                    else ''
                ),
                'product_name': (
                    h.product_list.product.name.name
                    if (h.product_list
                        and h.product_list.product
                        and h.product_list.product.name)
                    else ''
                ),
                'action': h.action,
                'action_display': (
                    h.get_action_display()
                ),
                'performed_at': (
                    h.performed_at.isoformat()
                ),
                'notes': h.notes,
                'weight_at_action': h.weight_at_action,
                'status_before': h.status_before,
                'status_after': h.status_after,
            }
            for h in history
        ],
    })


# ============================================================
# ADD TO THAW QUEUE
# ============================================================

@csrf_exempt
@require_POST
def add_to_thaw_queue(request):
    """
    เพิ่มสินค้าเข้าคิวละลาย (ไม่เริ่มละลายทันที)

    POST:
        product_id = Product_list ID

    เปลี่ยน:
        frozen + thaw_queue_position=0
        → frozen + thaw_queue_position>0
    """
    product_id = request.POST.get('product_id')

    if not product_id:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุสินค้า',
        }, status=400)

    with transaction.atomic():
        now = timezone.now()

        product = (
            Product_list.objects
            .select_for_update()
            .select_related('product', 'product__name')
            .get(id=product_id)
        )

        if product.storage_status != 'frozen':
            return JsonResponse({
                'success': False,
                'message': (
                    f'สินค้านี้อยู่ในสถานะ '
                    f'"{product.get_storage_status_display()}" '
                    f'ไม่สามารถเข้าคิวละลายได้'
                ),
            }, status=400)

        if product.thaw_queue_position > 0:
            return JsonResponse({
                'success': False,
                'message': 'สินค้านี้อยู่ในคิวละลายแล้ว',
            }, status=400)

        # หาลำดับคิวถัดไป
        max_queue = (
            Product_list.objects
            .filter(thaw_queue_position__gt=0)
            .aggregate(max_pos=Max('thaw_queue_position'))
            .get('max_pos') or 0
        )
        new_queue = max_queue + 1

        product.thaw_queue_position = new_queue
        product.thaw_scheduled_at = now
        product.save(update_fields=[
            'thaw_queue_position', 'thaw_scheduled_at',
        ])

        FreezeRotation.objects.create(
            product_list=product,
            action='thaw_start',
            notes=f'เข้าคิวละลาย (คิวที่ {new_queue})',
            weight_at_action=float(product.weight),
            status_before='frozen',
            status_after='frozen',
        )

    return JsonResponse({
        'success': True,
        'message': (
            f'"{product}" เข้าคิวละลายแล้ว '
            f'(คิวที่ {new_queue})'
        ),
        'product': _serialize_product(product),
        'queue_position': new_queue,
    })


# ============================================================
# SCHEDULE THAW — กำหนดเวลาพร้อมจำหน่าย
# ============================================================

@csrf_exempt
@require_POST
def schedule_thaw(request):
    """
    กำหนดเวลาที่ต้องการให้สินค้าพร้อมจำหน่าย

    POST:
        product_id = Product_list ID
        target_ready_at = ISO datetime ที่ต้องการพร้อมจำหน่าย

    Backend คำนวณ:
        thaw_duration_hours = target_ready_at - now
        thaw_started_at = now (เมื่อถึงเวลาเริ่มละลาย)
    """
    product_id = request.POST.get('product_id')
    target_raw = request.POST.get('target_ready_at', '')

    if not product_id:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุสินค้า',
        }, status=400)

    if not target_raw:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุเวลาที่ต้องการพร้อมจำหน่าย',
        }, status=400)

    from django.utils.dateparse import parse_datetime
    target_ready_at = parse_datetime(target_raw)
    if not target_ready_at:
        return JsonResponse({
            'success': False,
            'message': 'รูปแบบวันที่ไม่ถูกต้อง',
        }, status=400)

    if timezone.is_naive(target_ready_at):
        target_ready_at = timezone.make_aware(target_ready_at)

    with transaction.atomic():
        product = (
            Product_list.objects
            .select_for_update()
            .select_related('product', 'product__name')
            .get(id=product_id)
        )

        if product.storage_status != 'frozen' or product.thaw_queue_position <= 0:
            return JsonResponse({
                'success': False,
                'message': 'สินค้าต้องอยู่ในคิวละลายก่อนจึงจะกำหนดเวลาได้',
            }, status=400)

        now = timezone.now()
        if target_ready_at <= now:
            return JsonResponse({
                'success': False,
                'message': 'เวลาพร้อมจำหน่ายต้องอยู่ในอนาคต',
            }, status=400)

        # คำนวณ thaw_duration จาก target_ready_at
        # ใช้ DEFAULT_THAW_HOURS เป็นระยะเวลาละลายมาตรฐาน
        thaw_hrs = product.thaw_duration_hours or DEFAULT_THAW_HOURS

        product.thaw_target_ready_at = target_ready_at
        product.thaw_duration_hours = thaw_hrs
        product.save(update_fields=[
            'thaw_target_ready_at', 'thaw_duration_hours',
        ])

        # คำนวณเวลาที่ต้องเริ่มละลาย
        thaw_start_at = target_ready_at - timedelta(hours=thaw_hrs)

        FreezeRotation.objects.create(
            product_list=product,
            action='thaw_start',
            notes=(
                f'กำหนดเวลาพร้อมจำหน่าย: '
                f'{target_ready_at.strftime("%d/%m/%Y %H:%M")} '
                f'(ละลาย {thaw_hrs} ชม.)'
            ),
            weight_at_action=float(product.weight),
            status_before='frozen',
            status_after='frozen',
        )

    return JsonResponse({
        'success': True,
        'message': (
            f'กำหนดเวลาพร้อมจำหน่าย: '
            f'{target_ready_at.strftime("%d/%m/%Y %H:%M")}'
        ),
        'product': _serialize_product(product),
        'thaw_start_at': (
            thaw_start_at.isoformat()
        ),
        'target_ready_at': (
            target_ready_at.isoformat()
        ),
    })


# ============================================================
# CREATE ROTATION PLAN
# ============================================================

@csrf_exempt
@require_POST
def create_rotation_plan(request):
    """
    สร้างแผนการหมุนเวียนสินค้า

    POST:
        product_id = Product_list ID
        target_ready_at = ISO datetime
        freeze_duration_minutes = (optional override)
        thaw_duration_minutes = (optional override)
        buffer_minutes = (optional, default 120)
    """
    from stock_meat.models import RotationSchedule
    from stock_meat.schedule import (
        calculate_rotation_schedule,
        generate_worker_tasks,
    )

    product_id = request.POST.get('product_id')
    target_raw = request.POST.get('target_ready_at', '')

    if not product_id:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุสินค้า',
        }, status=400)

    if not target_raw:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุเวลาที่ต้องการพร้อมจำหน่าย',
        }, status=400)

    from django.utils.dateparse import parse_datetime
    target_ready_at = parse_datetime(target_raw)
    if not target_ready_at:
        return JsonResponse({
            'success': False,
            'message': 'รูปแบบวันที่ไม่ถูกต้อง',
        }, status=400)

    if timezone.is_naive(target_ready_at):
        target_ready_at = timezone.make_aware(target_ready_at)

    product = (
        Product_list.objects
        .select_related('product', 'product__name')
        .get(id=product_id)
    )

    # Parse optional overrides
    freeze_override = None
    thaw_override = None
    buffer_mins = 120

    fd_raw = request.POST.get('freeze_duration_minutes', '')
    if fd_raw:
        try:
            freeze_override = int(fd_raw)
        except (TypeError, ValueError):
            pass

    td_raw = request.POST.get('thaw_duration_minutes', '')
    if td_raw:
        try:
            thaw_override = int(td_raw)
        except (TypeError, ValueError):
            pass

    buf_raw = request.POST.get('buffer_minutes', '')
    if buf_raw:
        try:
            buffer_mins = int(buf_raw)
        except (TypeError, ValueError):
            pass

    # Calculate schedule
    schedule_data = calculate_rotation_schedule(
        product_list=product,
        target_ready_at=target_ready_at,
        freeze_duration_minutes=freeze_override,
        thaw_duration_minutes=thaw_override,
        buffer_minutes=buffer_mins,
    )

    # Create RotationSchedule
    schedule = RotationSchedule.objects.create(
        product_list=product,
        status='planned',
        target_ready_at=schedule_data['target_ready_at'],
        thaw_start_at=schedule_data['thaw_start_at'],
        freeze_end_at=schedule_data['freeze_end_at'],
        freeze_start_at=schedule_data['freeze_start_at'],
        freeze_duration_minutes=schedule_data['freeze_duration_minutes'],
        thaw_duration_minutes=schedule_data['thaw_duration_minutes'],
        buffer_minutes=schedule_data['buffer_minutes'],
        freeze_duration_estimated=schedule_data['freeze_estimated'],
        thaw_duration_estimated=schedule_data['thaw_estimated'],
        is_override=schedule_data['is_override'],
    )

    # Generate worker tasks
    tasks = generate_worker_tasks(schedule)

    # Update Product_list with schedule info
    product.freeze_started_at = schedule_data['freeze_start_at']
    product.freeze_end_at = schedule_data['freeze_end_at']
    product.freeze_duration_minutes = schedule_data['freeze_duration_minutes']
    product.thaw_target_ready_at = schedule_data['target_ready_at']
    product.thaw_duration_hours = int(
        schedule_data['thaw_duration_minutes'] / 60
    )  # model uses hours
    product.save(update_fields=[
        'freeze_started_at', 'freeze_end_at',
        'freeze_duration_minutes', 'thaw_target_ready_at',
        'thaw_duration_hours',
    ])

    return JsonResponse({
        'success': True,
        'message': (
            f'สร้างแผนสำเร็จ: {product.product.name} '
            f'พร้อมจำหน่าย {target_ready_at.strftime("%d/%m/%Y %H:%M")}'
        ),
        'schedule': {
            'id': schedule.id,
            'freeze_start_at': schedule.freeze_start_at.isoformat(),
            'freeze_end_at': schedule.freeze_end_at.isoformat(),
            'thaw_start_at': schedule.thaw_start_at.isoformat(),
            'target_ready_at': schedule.target_ready_at.isoformat(),
            'freeze_duration_minutes': schedule.freeze_duration_minutes,
            'thaw_duration_minutes': schedule.thaw_duration_minutes,
            'buffer_minutes': schedule.buffer_minutes,
            'is_override': schedule.is_override,
        },
        'tasks': [
            {
                'id': t.id,
                'task_type': t.task_type,
                'scheduled_at': t.scheduled_at.isoformat(),
            }
            for t in tasks
        ],
    })


# ============================================================
# WORKER TASKS
# ============================================================

@require_GET
def worker_tasks(request):
    """
    ดึงงานที่คนงานต้องทำ

    GET:
        date = YYYY-MM-DD (optional, default today)
    """
    from stock_meat.schedule import get_tasks_for_date

    date_str = request.GET.get('date', '')
    target_date = None

    if date_str:
        try:
            from datetime import date as _date
            parts = date_str.split('-')
            target_date = _date(
                int(parts[0]), int(parts[1]), int(parts[2])
            )
        except (ValueError, IndexError):
            target_date = None

    tasks = get_tasks_for_date(target_date)

    task_list = []
    for t in tasks:
        schedule = t.rotation_schedule
        product = schedule.product_list
        product_name = ''
        if product.product and product.product.name:
            product_name = product.product.name.name

        task_list.append({
            'id': t.id,
            'task_type': t.task_type,
            'task_type_display': t.get_task_type_display(),
            'scheduled_at': t.scheduled_at.isoformat(),
            'status': t.status,
            'is_overdue': t.is_overdue,
            'product_name': product_name,
            'barcode': product.barcode,
            'weight': float(product.weight),
            'target_ready_at': (
                schedule.target_ready_at.isoformat()
            ),
        })

    return JsonResponse({
        'success': True,
        'date': target_date.isoformat() if target_date else timezone.now().date().isoformat(),
        'tasks': task_list,
        'total': len(task_list),
    })


# ============================================================
# COMPLETE TASK
# ============================================================

@csrf_exempt
@require_POST
def complete_task(request):
    """
    ทำเครื่องหมายงานเสร็จ

    POST:
        task_id = WorkerTask ID
        completed_by = ชื่อผู้ทำ (optional)
    """
    from stock_meat.models import WorkerTask

    task_id = request.POST.get('task_id')
    completed_by = request.POST.get('completed_by', '')

    if not task_id:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุงาน',
        }, status=400)

    task = WorkerTask.objects.get(id=task_id)
    task.status = 'completed'
    task.completed_at = timezone.now()
    task.completed_by = completed_by
    task.save(update_fields=[
        'status', 'completed_at', 'completed_by',
    ])

    return JsonResponse({
        'success': True,
        'message': 'ทำเครื่องหมายเสร็จแล้ว',
    })


# ============================================================
# ROTATION PLANS LIST
# ============================================================

@require_GET
def rotation_plans(request):
    """
    ดูแผนการหมุนเวียนทั้งหมด
    """
    from stock_meat.models import RotationSchedule

    status = request.GET.get('status', '')

    qs = RotationSchedule.objects.select_related(
        'product_list',
        'product_list__product',
        'product_list__product__name',
    )

    if status:
        qs = qs.filter(status=status)

    plans = []
    for s in qs[:50]:
        product = s.product_list
        product_name = ''
        if product.product and product.product.name:
            product_name = product.product.name.name

        plans.append({
            'id': s.id,
            'product_name': product_name,
            'barcode': product.barcode,
            'weight': float(product.weight),
            'status': s.status,
            'target_ready_at': s.target_ready_at.isoformat(),
            'thaw_start_at': (
                s.thaw_start_at.isoformat() if s.thaw_start_at else None
            ),
            'freeze_end_at': (
                s.freeze_end_at.isoformat() if s.freeze_end_at else None
            ),
            'freeze_start_at': (
                s.freeze_start_at.isoformat() if s.freeze_start_at else None
            ),
            'freeze_duration_minutes': s.freeze_duration_minutes,
            'thaw_duration_minutes': s.thaw_duration_minutes,
            'is_override': s.is_override,
            'tasks_count': s.tasks.count(),
            'tasks_completed': s.tasks.filter(status='completed').count(),
        })

    return JsonResponse({
        'success': True,
        'plans': plans,
        'total': len(plans),
    })


# ============================================================
# HELPER: SERIALIZE PRODUCT
# ============================================================

def _serialize_product(
    product,
    include_display_info=False,
):
    """
    Serialize Product_list for JSON response
    """
    product_info = product.product

    data = {
        'id': product.id,
        'barcode': product.barcode,
        'weight': float(product.weight),
        'selling_price': float(product.selling_price),
        'mfg': product.mfg.strftime('%d/%m/%Y %H:%M'),
        'loyverse_sku': product.loyverse_sku or '',
        'loyverse_synced': product.loyverse_synced,

        # Product_info fields
        'name': (
            product_info.name.name
            if product_info and product_info.name
            else ''
        ),
        'category': (
            product_info.type_product.name_type
            if product_info and product_info.type_product
            else ''
        ),
        'import_from': (
            product_info.import_from.name_place
            if product_info and product_info.import_from
            else ''
        ),
        'lot_number': (
            product_info.lot_number
            if product_info else ''
        ),
        'cost': float(
            product_info.cost or 0
        ) if product_info else 0,
        'selling_price_per_kg': float(
            product_info.selling_price_per_kg or 0
        ) if product_info else 0,

        # Freeze status
        'storage_status': product.storage_status,
        'storage_status_display': (
            product.get_storage_status_display()
        ),
        'thaw_queue_position': (
            product.thaw_queue_position
        ),
        'thaw_duration_hours': (
            product.thaw_duration_hours
        ),
        'thaw_scheduled_at': (
            product.thaw_scheduled_at.isoformat()
            if product.thaw_scheduled_at else None
        ),
        'thaw_target_ready_at': (
            product.thaw_target_ready_at.isoformat()
            if product.thaw_target_ready_at else None
        ),
        'rotate_priority': product.rotate_priority,
        'freeze_started_at': (
            product.freeze_started_at.isoformat()
            if product.freeze_started_at else None
        ),
        'freeze_duration_minutes': product.freeze_duration_minutes,
        'freeze_end_at': (
            product.freeze_end_at.isoformat()
            if product.freeze_end_at else None
        ),
        'freeze_target_temp': product.freeze_target_temp,
    }

    # Thaw info
    if product.storage_status == 'thawing':
        data['thaw_started_at'] = (
            product.thaw_started_at.isoformat()
            if product.thaw_started_at
            else None
        )
        data['thaw_ready_at'] = (
            product.thaw_ready_at.isoformat()
            if product.thaw_ready_at
            else None
        )
        data['thaw_hours_remaining'] = (
            product.thaw_hours_remaining
        )
        data['is_thaw_complete'] = (
            product.is_thaw_complete
        )

    # Display info
    if include_display_info:
        data['entered_display_at'] = (
            product.entered_display_at.isoformat()
            if product.entered_display_at
            else None
        )
        data['display_max_days'] = (
            product.display_max_days
        )
        data['display_days_remaining'] = (
            product.display_days_remaining
        )
        data['is_display_expired'] = (
            product.is_display_expired
        )
        data['display_end_at'] = (
            product.display_end_at.isoformat()
            if product.display_end_at else None
        )

    # --------------------------------------------------------
    # Time in current status
    # --------------------------------------------------------

    data['time_in_status'] = _get_time_in_status(product)

    return data


# ============================================================
# HELPER: TIME IN STATUS
# ============================================================

def _get_time_in_status(product):
    """
    คำนวณเวลาที่สินค้าอยู่ในสถานะปัจจุบัน
    """
    now = timezone.now()

    if (
        product.storage_status == 'thawing'
        and product.thaw_started_at
    ):
        elapsed = (
            now - product.thaw_started_at
        )
        hours = elapsed.total_seconds() / 3600
        return {
            'label': 'เริ่มละลายเมื่อ',
            'started_at': (
                product.thaw_started_at.isoformat()
            ),
            'elapsed_hours': round(hours, 1),
            'elapsed_display': (
                f'{int(hours)} ชม. '
                f'{int((hours % 1) * 60)} น.'
            ),
        }

    elif (
        product.storage_status == 'display'
        and product.entered_display_at
    ):
        elapsed = (
            now - product.entered_display_at
        )
        days = elapsed.days
        hours = (
            elapsed.total_seconds() / 3600
        ) % 24
        return {
            'label': 'วางขายมา',
            'started_at': (
                product.entered_display_at.isoformat()
            ),
            'elapsed_hours': round(
                elapsed.total_seconds() / 3600, 1
            ),
            'elapsed_display': (
                f'{days} วัน '
                f'{int(hours)} ชม.'
            ),
        }

    elif product.storage_status == 'frozen':
        last_rotation = (
            FreezeRotation.objects
            .filter(
                product_list=product,
                action='freeze_return',
            )
            .order_by('-performed_at')
            .first()
        )

        if last_rotation:
            elapsed = (
                now - last_rotation.performed_at
            )
        else:
            elapsed = (
                now - product.mfg
            )

        days = elapsed.days
        hours = (
            elapsed.total_seconds() / 3600
        ) % 24
        return {
            'label': 'แช่มา',
            'started_at': (
                (
                    last_rotation.performed_at
                    if last_rotation
                    else product.mfg
                ).isoformat()
            ),
            'elapsed_hours': round(
                elapsed.total_seconds() / 3600, 1
            ),
            'elapsed_display': (
                f'{days} วัน '
                f'{int(hours)} ชม.'
            ),
        }

    return None


# ============================================================
# HELPER: REORDER THAW QUEUE
# ============================================================

def _reorder_thaw_queue():
    """
    จัดอันดับคิวละลายใหม่
    """
    thawing_products = list(
        Product_list.objects
        .filter(
            storage_status='thawing',
            thaw_queue_position__gt=0,
        )
        .order_by('thaw_queue_position')
    )

    for idx, product in enumerate(
        thawing_products, start=1
    ):
        if (
            product.thaw_queue_position
            != idx
        ):
            product.thaw_queue_position = idx
            product.save(
                update_fields=[
                    'thaw_queue_position',
                ]
            )


# ============================================================
# PENDING PRODUCTS (newly packed, awaiting decision)
# ============================================================

@require_GET
def pending_products(request):
    """
    ดึง product_list ที่ status = 'pending'
    สำหรับให้ผู้ดูแลเลือกว่าจะส่งแช่แข็งหรือวางขาย
    """
    products = (
        Product_list.objects
        .filter(storage_status='pending')
        .select_related(
            'product',
            'product__name',
        )
        .order_by('-mfg')
    )

    data = []
    for p in products:
        data.append({
            'id': p.id,
            'barcode': p.barcode,
            'name': p.product.name.name if p.product and p.product.name else '-',
            'weight': float(p.weight),
            'selling_price': float(p.selling_price),
            'mfg': p.mfg.strftime('%d/%m/%Y %H:%M') if p.mfg else '-',
            'storage_status': p.storage_status,
        })

    return JsonResponse({
        'success': True,
        'products': data,
    })


# ============================================================
# SET PRODUCT STATUS (freeze or display)
# ============================================================

@csrf_exempt
@require_POST
def set_product_status(request):
    """
    เปลี่ยนสถานะ product_list
    - frozen: ส่งเข้าแช่แข็ง
    - display: ส่งขึ้นวางขาย
    """
    product_id = request.POST.get('product_id')
    new_status = request.POST.get('status')

    if not product_id or not new_status:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาเลือกสินค้าและสถานะ',
        })

    if new_status not in ('frozen', 'display'):
        return JsonResponse({
            'success': False,
            'message': 'สถานะไม่ถูกต้อง',
        })

    try:
        product = Product_list.objects.get(id=product_id)
    except Product_list.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'ไม่พบสินค้า',
        })

    product.storage_status = new_status

    if new_status == 'display':
        product.entered_display_at = timezone.now()
        display_days = request.POST.get('display_days', DEFAULT_DISPLAY_DAYS)
        product.display_max_days = int(display_days)

    product.save(update_fields=[
        'storage_status',
        'entered_display_at',
        'display_max_days',
    ])

    # Record rotation
    FreezeRotation.objects.create(
        product_list=product,
        action='set_' + new_status,
        notes='เปลี่ยนสถานะเป็น ' + new_status,
    )

    status_label = '❄️ แช่แข็ง' if new_status == 'frozen' else '🛒 วางขาย'
    return JsonResponse({
        'success': True,
        'message': f'เปลี่ยนสถานะเป็น {status_label} สำเร็จ',
    })
