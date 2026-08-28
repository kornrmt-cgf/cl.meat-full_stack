from django.shortcuts import render
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Sum, Count
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from stock_meat.models import (
    Product_list,
    ProductProcessing,
    ProcessType,
)


# ============================================================
# PROCESSING PAGE
# ============================================================

@require_GET
def processing_page(request):
    """หน้าจัดการแปรรูปสินค้า"""
    return render(request, 'processing.html')


# ============================================================
# GET PROCESS TYPES
# ============================================================

@require_GET
def get_process_types(request):
    """ดึงประเภทการแปรรูปทั้งหมด"""
    types = (
        ProcessType.objects
        .filter(is_active=True)
        .order_by('name')
    )

    return JsonResponse({
        'success': True,
        'process_types': [
            {
                'id': pt.id,
                'name': pt.name,
                'description': pt.description,
                'output_price_per_kg': float(pt.output_price_per_kg),
            }
            for pt in types
        ],
    })


# ============================================================
# ADD PROCESS TYPE
# ============================================================

@csrf_exempt
@require_POST
def add_process_type(request):
    """เพิ่มประเภทการแปรรูป"""
    name = request.POST.get('name', '').strip()
    description = request.POST.get('description', '').strip()
    price_raw = request.POST.get('output_price_per_kg', '0')

    if not name:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุชื่อประเภทแปรรูป',
        }, status=400)

    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        price = 0

    pt = ProcessType.objects.create(
        name=name,
        description=description,
        output_price_per_kg=price,
    )

    return JsonResponse({
        'success': True,
        'message': f'เพิ่ม "{name}" สำเร็จ',
        'process_type': {
            'id': pt.id,
            'name': pt.name,
            'description': pt.description,
            'output_price_per_kg': float(pt.output_price_per_kg),
        },
    })


# ============================================================
# SUBMIT PROCESSING
# ============================================================

@csrf_exempt
@require_POST
def submit_processing(request):
    """บันทึกการแปรรูป/บริจาค/ทิ้ง"""
    product_id = request.POST.get('product_id', '')
    action = request.POST.get('action', 'process')
    process_type_id = request.POST.get('process_type_id', '')
    input_weight_raw = request.POST.get('input_weight', '0')
    output_weight_raw = request.POST.get('output_weight', '')
    notes = request.POST.get('notes', '').strip()

    if not product_id:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุสินค้า',
        }, status=400)

    if action not in ('process', 'donate', 'discard'):
        return JsonResponse({
            'success': False,
            'message': 'ประเภทการดำเนินการไม่ถูกต้อง',
        }, status=400)

    try:
        product = Product_list.objects.select_related(
            'product',
            'product__name',
        ).get(id=int(product_id))
    except (Product_list.DoesNotExist, ValueError):
        return JsonResponse({
            'success': False,
            'message': 'ไม่พบสินค้า',
        }, status=404)

    try:
        input_weight = float(input_weight_raw)
    except (TypeError, ValueError):
        return JsonResponse({
            'success': False,
            'message': 'น้ำหนักไม่ถูกต้อง',
        }, status=400)

    if input_weight <= 0:
        return JsonResponse({
            'success': False,
            'message': 'น้ำหนักต้องมากกว่า 0',
        }, status=400)

    output_weight = None
    if output_weight_raw:
        try:
            output_weight = float(output_weight_raw)
        except (TypeError, ValueError):
            pass

    process_type = None
    if process_type_id:
        try:
            process_type = ProcessType.objects.get(
                id=int(process_type_id)
            )
        except (ProcessType.DoesNotExist, ValueError):
            pass

    with transaction.atomic():
        proc = ProductProcessing.objects.create(
            product_list=product,
            process_type=process_type,
            action=action,
            input_weight=input_weight,
            output_weight=output_weight,
            notes=notes,
            processed_at=timezone.now(),
        )

        # Mark product as depleted
        product.storage_status = 'depleted'
        product.save(update_fields=['storage_status'])

    action_labels = {
        'process': 'แปรรูป',
        'donate': 'บริจาค',
        'discard': 'ทิ้ง',
    }

    product_name = (
        product.product.name.name
        if product.product and product.product.name
        else product.barcode
    )

    return JsonResponse({
        'success': True,
        'message': (
            f'บันทึกการ{action_labels[action]} '
            f'"{product_name}" ({input_weight}g) สำเร็จ'
        ),
        'processing': {
            'id': proc.id,
            'product_barcode': product.barcode,
            'product_name': product_name,
            'action': action,
            'input_weight': input_weight,
            'output_weight': output_weight,
            'process_type': process_type.name if process_type else '',
            'notes': notes,
            'processed_at': proc.processed_at.isoformat(),
        },
    })


# ============================================================
# LIST PROCESSING HISTORY
# ============================================================

@require_GET
def list_processing(request):
    """ดูประวัติการแปรรูป"""
    limit_raw = request.GET.get('limit', '100')

    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 100

    history = (
        ProductProcessing.objects
        .select_related(
            'product_list',
            'product_list__product',
            'product_list__product__name',
            'process_type',
        )
        .all()[:limit]
    )

    # Stats
    total_processed = (
        ProductProcessing.objects
        .filter(action='process')
        .aggregate(total_input=Sum('input_weight'))
        .get('total_input') or 0
    )

    total_donated = (
        ProductProcessing.objects
        .filter(action='donate')
        .aggregate(total_input=Sum('input_weight'))
        .get('total_input') or 0
    )

    total_discarded = (
        ProductProcessing.objects
        .filter(action='discard')
        .aggregate(total_input=Sum('input_weight'))
        .get('total_input') or 0
    )

    return JsonResponse({
        'success': True,
        'history': [
            _serialize_processing(p)
            for p in history
        ],
        'stats': {
            'total_processed': float(total_processed),
            'total_donated': float(total_donated),
            'total_discarded': float(total_discarded),
            'total_records': ProductProcessing.objects.count(),
        },
    })


# ============================================================
# GET PRODUCTS AVAILABLE FOR PROCESSING
# ============================================================

@require_GET
def get_processable_products(request):
    """สินค้าที่พร้อมแปรรูป (display expired, expiring soon, หรือ manual)"""
    from stock_meat.models import Product_list

    # Products in display or thawing status that could be processed
    products = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
            'product__type_product',
        )
        .filter(
            storage_status__in=['display', 'thawing', 'frozen'],
        )
        .order_by('storage_status', '-mfg')
    )

    return JsonResponse({
        'success': True,
        'products': [
            {
                'id': p.id,
                'barcode': p.barcode,
                'name': (
                    p.product.name.name
                    if p.product and p.product.name
                    else ''
                ),
                'weight': float(p.weight),
                'selling_price': float(p.selling_price),
                'storage_status': p.storage_status,
                'storage_status_display': p.get_storage_status_display(),
            }
            for p in products
        ],
    })


# ============================================================
# HELPER
# ============================================================

def _serialize_processing(proc):
    product_list = proc.product_list

    product_name = ''
    if product_list and product_list.product and product_list.product.name:
        product_name = product_list.product.name.name

    return {
        'id': proc.id,
        'barcode': product_list.barcode if product_list else '',
        'product_name': product_name,
        'action': proc.action,
        'action_display': dict(ProductProcessing.ACTION_CHOICES).get(
            proc.action, proc.action
        ),
        'input_weight': proc.input_weight_float,
        'output_weight': proc.output_weight_float,
        'yield_percent': proc.yield_percent,
        'process_type': proc.process_type.name if proc.process_type else '',
        'notes': proc.notes,
        'processed_at': proc.processed_at.isoformat(),
    }
