from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import Sum, Count, Q, F
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.views.decorators.http import require_GET
from datetime import date, timedelta

from stock_meat.models import (
    Product_list,
    Product_info,
    Transaction,
    ProductProcessing,
    FreezeRotation,
    ElectricityBill,
    meat_parts,
    Category,
    Supply_meat,
)


# ============================================================
# DASHBOARD PAGE
# ============================================================

@require_GET
def dashboard_page(request):
    """หน้า Dashboard สรุปยอด"""
    return render(request, 'dashboard.html')


# ============================================================
# GET DASHBOARD DATA
# ============================================================

@require_GET
def get_dashboard_data(request):
    """ดึงข้อมูลสรุปทั้งหมด"""
    today = date.today()
    now = timezone.now()
    current_month = today.month
    current_year = today.year
    
    # Months parameter for trend chart
    months_param = int(request.GET.get('months', 6))
    months_param = max(1, min(24, months_param))

    # --------------------------------------------------------
    # Stock Overview
    # --------------------------------------------------------

    total_product_info = Product_info.objects.filter(
        weight__gt=0,
    ).count()

    total_weight = (
        Product_info.objects
        .filter(weight__gt=0)
        .aggregate(total=Sum('weight'))
        .get('total') or 0
    )

    # --------------------------------------------------------
    # Storage Status Breakdown
    # --------------------------------------------------------

    storage_stats = (
        Product_list.objects
        .values('storage_status')
        .annotate(count=Count('id'))
        .order_by('storage_status')
    )

    storage_map = {}
    for s in storage_stats:
        storage_map[s['storage_status']] = s['count']

    total_packed = Product_list.objects.count()

    # --------------------------------------------------------
    # Financial Summary (current month)
    # --------------------------------------------------------

    month_income = (
        Transaction.objects
        .filter(
            transaction_type='income',
            receipt_date__year=current_year,
            receipt_date__month=current_month,
        )
        .aggregate(total=Sum('amount'))
        .get('total') or 0
    )

    month_expense = (
        Transaction.objects
        .filter(
            transaction_type='expense',
            receipt_date__year=current_year,
            receipt_date__month=current_month,
        )
        .aggregate(total=Sum('amount'))
        .get('total') or 0
    )

    # Meat purchase expenses this month
    meat_expense = (
        Transaction.objects
        .filter(
            transaction_type='expense',
            receipt_date__year=current_year,
            receipt_date__month=current_month,
            category__name__icontains='เนื้อ',
        )
        .aggregate(total=Sum('amount'))
        .get('total') or 0
    )

    # --------------------------------------------------------
    # Processing Stats
    # --------------------------------------------------------

    total_processed_weight = (
        ProductProcessing.objects
        .filter(action='process')
        .aggregate(total=Sum('input_weight'))
        .get('total') or 0
    )

    total_donated_weight = (
        ProductProcessing.objects
        .filter(action='donate')
        .aggregate(total=Sum('input_weight'))
        .get('total') or 0
    )

    total_discarded_weight = (
        ProductProcessing.objects
        .filter(action='discard')
        .aggregate(total=Sum('input_weight'))
        .get('total') or 0
    )

    processing_count = ProductProcessing.objects.count()

    # --------------------------------------------------------
    # Monthly trend (last 6 months)
    # --------------------------------------------------------

    monthly_trend = []
    for i in range(months_param - 1, -1, -1):
        m = current_month - i
        y = current_year
        if m <= 0:
            m += 12
            y -= 1

        income = (
            Transaction.objects
            .filter(
                transaction_type='income',
                receipt_date__year=y,
                receipt_date__month=m,
            )
            .aggregate(total=Sum('amount'))
            .get('total') or 0
        )

        expense = (
            Transaction.objects
            .filter(
                transaction_type='expense',
                receipt_date__year=y,
                receipt_date__month=m,
            )
            .aggregate(total=Sum('amount'))
            .get('total') or 0
        )

        packed = (
            Product_list.objects
            .filter(
                mfg__year=y,
                mfg__month=m,
            )
            .count()
        )

        monthly_trend.append({
            'month': f'{m:02d}/{y}',
            'income': float(income),
            'expense': float(expense),
            'profit': float(income) - float(expense),
            'packed': packed,
        })

    # --------------------------------------------------------
    # Recent Processing
    # --------------------------------------------------------

    recent_processing = (
        ProductProcessing.objects
        .select_related(
            'product_list',
            'product_list__product',
            'product_list__product__name',
            'process_type',
        )
        .all()[:5]
    )

    # --------------------------------------------------------
    # Low Stock Alerts (Product_info with low weight)
    # --------------------------------------------------------

    low_stock = (
        Product_info.objects
        .select_related('name', 'import_from')
        .filter(weight__gt=0, weight__lt=500)
        .order_by('weight')[:5]
    )

    # --------------------------------------------------------
    # Expiring Soon (display status, <=1 day)
    # --------------------------------------------------------

    expiring_soon = []
    display_products = (
        Product_list.objects
        .select_related('product', 'product__name')
        .filter(storage_status='display')
    )
    for p in display_products:
        if p.display_days_remaining is not None and p.display_days_remaining <= 1:
            expiring_soon.append(p)

    # --------------------------------------------------------
    # Electricity Bills (last 12 months)
    # --------------------------------------------------------
    
    elec_bills = list(
        ElectricityBill.objects
        .order_by('-year', '-month')[:12]
    )
    elec_data = []
    for b in reversed(elec_bills):
        month_names = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.']
        elec_data.append({
            'month_label': month_names[b.month - 1] + ' ' + str(b.year),
            'units_used': float(b.units_used),
            'total_amount': float(b.total_amount),
        })

    return JsonResponse({
        'success': True,
        'electricity': elec_data,
        'stock': {
            'total_product_info': total_product_info,
            'total_weight_grams': float(total_weight),
            'total_packed': total_packed,
        },
        'storage': {
            'frozen': storage_map.get('frozen', 0),
            'thawing': storage_map.get('thawing', 0),
            'display': storage_map.get('display', 0),
            'depleted': storage_map.get('depleted', 0),
        },
        'finance': {
            'month_income': float(month_income),
            'month_expense': float(month_expense),
            'month_profit': float(month_income) - float(month_expense),
            'meat_expense': float(meat_expense),
        },
        'processing': {
            'total_count': processing_count,
            'total_processed_grams': float(total_processed_weight),
            'total_donated_grams': float(total_donated_weight),
            'total_discarded_grams': float(total_discarded_weight),
        },
        'monthly_trend': monthly_trend,
        'recent_processing': [
            {
                'barcode': p.product_list.barcode if p.product_list else '',
                'name': (
                    p.product_list.product.name.name
                    if (p.product_list and p.product_list.product
                        and p.product_list.product.name)
                    else ''
                ),
                'action': p.action,
                'action_display': dict(
                    ProductProcessing.ACTION_CHOICES
                ).get(p.action, p.action),
                'weight': p.input_weight_float,
                'processed_at': p.processed_at.strftime('%d/%m %H:%M'),
            }
            for p in recent_processing
        ],
        'low_stock': [
            {
                'id': p.id,
                'name': p.name.name if p.name else '',
                'weight': float(p.weight),
                'import_from': p.import_from.name_place if p.import_from else '',
            }
            for p in low_stock
        ],
        'expiring_soon': [
            {
                'id': p.id,
                'barcode': p.barcode,
                'name': (
                    p.product.name.name
                    if p.product and p.product.name
                    else ''
                ),
                'days_remaining': p.display_days_remaining,
            }
            for p in expiring_soon
        ],
    })


# ============================================================
# MEAT PARTS PRICE DASHBOARD
# ============================================================

@require_GET
def meat_parts_prices(request):
    """เปรียบเทียบราคา meat_parts แต่ละรอบนำเข้า"""

    # Get all Product_info grouped by meat_parts
    parts = meat_parts.objects.select_related('category').order_by('category', 'name')

    parts_data = []
    for part in parts:
        lots = (
            Product_info.objects
            .filter(name=part)
            .select_related('import_from')
            .order_by('-lot_number')
        )
        if lots.exists():
            lot_history = []
            for lot in lots:
                lot_history.append({
                    'id': lot.id,
                    'lot_number': lot.lot_number,
                    'import_from': lot.import_from.name_place if lot.import_from else '',
                    'weight': float(lot.weight),
                    'cost': float(lot.cost or 0),
                    'selling_price_per_kg': float(lot.selling_price_per_kg or 0),
                    'profit_per_kg': float(lot.profit_per_kg),
                    'profit_percent': float(lot.profit_percent),
                    'created_at': lot.created_at.strftime('%d/%m/%Y'),
                })

            latest = lots.first()
            parts_data.append({
                'id': part.id,
                'name': part.name,
                'category': part.category.name_type if part.category else '',
                'lot_count': lots.count(),
                'latest_cost': float(latest.cost or 0),
                'latest_price': float(latest.selling_price_per_kg or 0),
                'latest_profit': float(latest.profit_per_kg),
                'lot_history': lot_history,
            })

    return JsonResponse({
        'success': True,
        'parts': parts_data,
    })
