"""
SOLD ITEMS — Loyverse Receipt Sync & Dashboard
ดึงข้อมูลใบเสร็จจาก Loyverse API เทียบกับ Product_list
บันทึกสถานะว่าขายออกแล้ว พร้อมคำนวณ กำไร/ต้นทุน/เวลา/ค่าไฟ
"""

import requests
from datetime import datetime, timedelta
from decimal import Decimal
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from stock_meat.models import (
    Product_list,
    SoldItem,
    ElectricityBill,
    Transaction,
    ExpenseCategory,
)


import os

LOYVERSE_BASE_URL = "https://api.loyverse.com/v1.0"
LOYVERSE_ACCESS_TOKEN = os.environ.get("LOYVERSE_ACCESS_TOKEN", "")


def _get_headers():
    return {"Authorization": f"Bearer {LOYVERSE_ACCESS_TOKEN}"}


def _calculate_electricity_cost_per_day():
    """
    คำนวณค่าไฟต่อวันจากบิลค่าไฟล่าสุด
    หารด้วย 30 วัน (ประมาณจำนวนวันในเดือน)
    """
    latest = ElectricityBill.objects.order_by('-year', '-month').first()
    if not latest or not latest.total_amount_float:
        return 0
    return latest.total_amount_float / 30.0


def _estimate_frozen_items_count():
    """
    ประมาณจำนวนสินค้าที่แช่อยู่ในตู้ (frozen + thawing)
    สำหรับหารค่าไฟ
    """
    return Product_list.objects.filter(
        storage_status__in=['frozen', 'thawing']
    ).count() or 1


@csrf_exempt
@require_POST
def sync_loyverse_receipts(request):
    """
    ดึงใบเสร็จจาก Loyverse API แล้วบันทึกเป็น SoldItem
    - ใช้ cursor-based pagination
    - ป้องกันซ้ำด้วย loyverse_receipt_id + loyverse_variant_id
    - คำนวณ profit, days_to_sell, electricity_cost
    """
    limit_raw = request.POST.get('limit', '100')
    try:
        limit = min(int(limit_raw), 200)
    except (TypeError, ValueError):
        limit = 100

    url = f"{LOYVERSE_BASE_URL}/receipts?limit={limit}"
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        return JsonResponse({
            'success': False,
            'message': f'ไม่สามารถเชื่อมต่อ Loyverse API: {str(e)}',
        }, status=502)

    data = resp.json()
    receipts = data.get('receipts', [])

    # Electricity cost
    elec_per_day = _calculate_electricity_cost_per_day()
    frozen_count = _estimate_frozen_items_count()
    elec_per_item_per_day = elec_per_day / frozen_count if frozen_count else 0

    synced = 0
    skipped = 0
    errors = 0
    new_items = []

    for rec in receipts:
        receipt_number = rec.get('receipt_number', '')
        receipt_date_str = rec.get('receipt_date', '')
        store_id = rec.get('store_id', '')

        # Parse receipt date
        try:
            receipt_date = datetime.fromisoformat(
                receipt_date_str.replace('Z', '+00:00')
            )
        except (ValueError, AttributeError):
            continue

        for item in rec.get('line_items', []):
            sku = item.get('sku', '')
            if not sku:
                continue

            # Only sync items with SKU >= 30000 (our items)
            try:
                sku_int = int(sku)
            except (TypeError, ValueError):
                continue

            if sku_int < 30000:
                continue

            variant_id = item.get('variant_id', '')
            item_id = item.get('item_id', '')

            # Dedup: receipt_id + variant_id
            receipt_id = f"{receipt_number}_{variant_id}"
            if SoldItem.objects.filter(
                loyverse_receipt_id=receipt_id
            ).exists():
                skipped += 1
                continue

            # Find matching Product_list
            product_list = None
            try:
                product_list = Product_list.objects.get(
                    loyverse_sku=sku
                )
            except Product_list.DoesNotExist:
                pass

            # Calculate profit
            selling_price = Decimal(str(item.get('price', 0)))
            cost_price = Decimal(str(item.get('cost', 0)))
            qty = int(item.get('quantity', 1))
            total = Decimal(str(item.get('total_money', 0))) or (selling_price * qty)
            profit = (selling_price - cost_price) * qty
            profit_pct = 0
            if cost_price > 0:
                profit_pct = float(((selling_price - cost_price) / cost_price) * 100)

            # Days to sell
            days_to_sell = 0
            if product_list and product_list.mfg:
                mfg = product_list.mfg
                if hasattr(mfg, 'astimezone'):
                    sold_date = receipt_date.astimezone(timezone.get_current_timezone())
                else:
                    sold_date = receipt_date
                delta = sold_date - mfg
                days_to_sell = max(0, delta.days)

            # Electricity cost for this item
            elec_cost = Decimal('0')
            if days_to_sell > 0:
                elec_cost = Decimal(str(round(
                    elec_per_item_per_day * days_to_sell, 2
                )))

            item_name = item.get('item_name') or item.get('variant_name', '')
            if not item_name and product_list and product_list.product:
                name_obj = product_list.product.name
                if name_obj:
                    item_name = name_obj.name

            try:
                sold = SoldItem.objects.create(
                    product_list=product_list,
                    receipt_number=receipt_number,
                    receipt_date=receipt_date,
                    store_id=store_id,
                    loyverse_sku=sku,
                    item_name=item_name,
                    quantity=qty,
                    selling_price=selling_price,
                    cost_price=cost_price,
                    total_amount=total,
                    profit=profit,
                    profit_percent=profit_pct,
                    days_to_sell=days_to_sell,
                    electricity_cost=elec_cost,
                    loyverse_item_id=item_id,
                    loyverse_variant_id=variant_id,
                    loyverse_receipt_id=receipt_id,
                )
                synced += 1
                new_items.append({
                    'sku': sku,
                    'name': item_name,
                    'price': float(selling_price),
                    'profit': float(profit),
                    'days': days_to_sell,
                    'receipt': receipt_number,
                })
            except Exception:
                errors += 1

    # --- สร้าง Transaction อัตโนมัติ 1 รายการต่อบิล ---
    receipt_groups = {}
    for item_data in new_items:
        rkey = item_data['receipt']
        if rkey not in receipt_groups:
            receipt_groups[rkey] = {'total': 0, 'date': None, 'items': []}
        receipt_groups[rkey]['total'] += item_data['price']
        receipt_groups[rkey]['items'].append(item_data['name'])
    # ดึงวันที่จาก SoldItem ล่าสุดของแต่ละบิล
    from django.db.models import Max
    for rkey, grp in receipt_groups.items():
        latest = (
            SoldItem.objects
            .filter(receipt_number=rkey)
            .aggregate(latest_date=Max('receipt_date'))
        )
        grp['date'] = latest.get('latest_date')

    # หมวดหมู่รายรับ
    income_cat = ExpenseCategory.objects.filter(
        name__icontains='ขายหน้าร้าน',
        category_type='income'
    ).first()
    if not income_cat:
        income_cat = ExpenseCategory.objects.filter(
            category_type='income'
        ).first()

    txn_created = 0
    for rkey, grp in receipt_groups.items():
        # Dedup: ตรวจว่ามี Transaction ที่ receipt_number ตรงกันแล้วหรือยัง
        exists = Transaction.objects.filter(
            receipt_number=rkey,
            transaction_type='income',
        ).exists()
        if exists:
            continue
        if grp['date']:
            txn_date = grp['date'].date() if hasattr(grp['date'], 'date') else grp['date']
        else:
            txn_date = timezone.now().date()
        item_names = ', '.join(grp['items'][:3])
        if len(grp['items']) > 3:
            item_names += f' +{len(grp["items"]) - 3} รายการ'
        desc = f'ขายหน้าร้าน บิล {rkey} ({item_names})'
        Transaction.objects.create(
            transaction_type='income',
            category=income_cat,
            amount=round(grp['total'], 2),
            description=desc,
            receipt_date=txn_date,
            receipt_number=rkey,
        )
        txn_created += 1

    msg = f'ซิงค์สำเร็จ: {synced} รายการใหม่'
    if txn_created:
        msg += f', +{txn_created} รายการรายรับ'
    if skipped:
        msg += f', {skipped} ซ้ำ'
    if errors:
        msg += f', {errors} ข้อผิดพลาด'

    return JsonResponse({
        'success': True,
        'message': msg,
        'synced': synced,
        'skipped': skipped,
        'errors': errors,
        'txn_created': txn_created,
        'new_items': new_items[:20],
        'elec_per_day': round(elec_per_day, 2),
        'elec_per_item_per_day': round(elec_per_item_per_day, 4),
    })


@require_GET
def sold_items_list(request):
    """
    แสดงรายการสินค้าที่ขายแล้ว
    Optional: ?sku=XXX&days=30
    """
    sku = request.GET.get('sku', '').strip()
    days_raw = request.GET.get('days', '')

    qs = SoldItem.objects.select_related('product_list', 'product_list__product')

    if sku:
        qs = qs.filter(loyverse_sku__icontains=sku)

    if days_raw:
        try:
            days = int(days_raw)
            since = timezone.now() - timedelta(days=days)
            qs = qs.filter(receipt_date__gte=since)
        except (TypeError, ValueError):
            pass

    items = []
    for s in qs[:200]:
        item = {
            'id': s.id,
            'sku': s.loyverse_sku,
            'name': s.item_name,
            'receipt_number': s.receipt_number,
            'receipt_date': timezone.localtime(s.receipt_date).strftime('%d/%m/%Y %H:%M'),
            'quantity': s.quantity,
            'selling_price': float(s.selling_price),
            'cost_price': float(s.cost_price),
            'total_amount': float(s.total_amount),
            'profit': float(s.profit),
            'profit_percent': round(s.profit_percent, 1),
            'days_to_sell': s.days_to_sell,
            'electricity_cost': float(s.electricity_cost),
            'barcode': s.product_list.barcode if s.product_list else '',
            'storage_status': s.product_list.storage_status if s.product_list else 'sold',
        }
        items.append(item)

    return JsonResponse({
        'success': True,
        'count': len(items),
        'items': items,
    })


@require_GET
def sold_items_summary(request):
    """
    สรุปยอดขายสำหรับ dashboard
    - ยอดขายรวม
    - กำไรรวม
    - ค่าไฟรวม
    - กำไรสุทธิ
    - จำนวนสินค้า
    - เวลาเฉลี่ยในการขาย
    """
    days_raw = request.GET.get('days', '30')
    try:
        days = int(days_raw)
    except (TypeError, ValueError):
        days = 30

    since = timezone.now() - timedelta(days=days)
    items = SoldItem.objects.filter(receipt_date__gte=since)

    from django.db.models import Sum, Avg, Count

    stats = items.aggregate(
        total_sales=Sum('total_amount'),
        total_profit=Sum('profit'),
        total_electricity=Sum('electricity_cost'),
        total_items=Count('id'),
        avg_days=Avg('days_to_sell'),
        avg_profit_pct=Avg('profit_percent'),
    )

    total_sales = float(stats.get('total_sales') or 0)
    total_profit = float(stats.get('total_profit') or 0)
    total_elec = float(stats.get('total_electricity') or 0)
    net_profit = total_profit - total_elec

    # Daily breakdown
    from django.db.models.functions import TruncDate
    daily = (
        items
        .annotate(date=TruncDate('receipt_date'))
        .values('date')
        .annotate(
            sales=Sum('total_amount'),
            profit=Sum('profit'),
            elec=Sum('electricity_cost'),
            count=Count('id'),
        )
        .order_by('date')
    )

    daily_data = []
    for d in daily:
        daily_data.append({
            'date': d['date'].strftime('%d/%m') if d['date'] else '',
            'sales': float(d.get('sales') or 0),
            'profit': float(d.get('profit') or 0),
            'electricity': float(d.get('elec') or 0),
            'count': d.get('count', 0),
        })

    # Top sold products
    # Group by product name (via Product_info.name)
    top_products = (
        items
        .filter(product_list__isnull=False)
        .values(
            'product_list__product__name__name',
            'product_list__product__type_product__name_type',
        )
        .annotate(
            total_qty=Sum('quantity'),
            total_sales=Sum('total_amount'),
            total_profit=Sum('profit'),
            total_elec=Sum('electricity_cost'),
            avg_days=Avg('days_to_sell'),
        )
        .order_by('-total_qty')[:30]
    )

    # Merge duplicates (same name, different product_info IDs)
    merged = {}
    for tp in top_products:
        name = (tp.get('product_list__product__name__name') or 'ไม่ระบุ').strip()
        category = (tp.get('product_list__product__type_product__name_type') or '').strip()
        key = name  # merge by name
        if key in merged:
            merged[key]['quantity'] += tp.get('total_qty', 0)
            merged[key]['sales'] += float(tp.get('total_sales') or 0)
            merged[key]['profit'] += float(tp.get('total_profit') or 0)
            merged[key]['electricity'] += float(tp.get('total_elec') or 0)
            old_qty = merged[key]['_raw_qty']
            new_qty = tp.get('total_qty', 0)
            old_days = merged[key]['avg_days']
            new_days = float(tp.get('avg_days') or 0)
            if old_qty + new_qty > 0:
                merged[key]['avg_days'] = round(
                    (old_days * old_qty + new_days * new_qty) / (old_qty + new_qty), 1
                )
            merged[key]['_raw_qty'] += new_qty
        else:
            merged[key] = {
                'name': name,
                'category': category,
                'quantity': tp.get('total_qty', 0),
                'sales': float(tp.get('total_sales') or 0),
                'profit': float(tp.get('total_profit') or 0),
                'electricity': float(tp.get('total_elec') or 0),
                'avg_days': round(float(tp.get('avg_days') or 0), 1),
                '_raw_qty': tp.get('total_qty', 0),
            }

    top = sorted(merged.values(), key=lambda x: -x['quantity'])[:15]
    for t in top:
        t.pop('_raw_qty', None)

    return JsonResponse({
        'success': True,
        'days': days,
        'summary': {
            'total_sales': round(total_sales, 2),
            'total_profit': round(total_profit, 2),
            'total_electricity': round(total_elec, 2),
            'net_profit': round(net_profit, 2),
            'total_items': stats.get('total_items', 0),
            'avg_days_to_sell': round(float(stats.get('avg_days') or 0), 1),
            'avg_profit_percent': round(float(stats.get('avg_profit_pct') or 0), 1),
        },
        'daily': daily_data,
        'top_products': top,
    })


def sold_items_page(request):
    """หน้า dashboard แสดง sold items"""
    from django.shortcuts import render
    return render(request, 'sold_items.html')
