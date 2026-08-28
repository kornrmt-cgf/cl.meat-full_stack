from django.shortcuts import render
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from datetime import date, timedelta
import json

from stock_meat.models import (
    Transaction,
    ExpenseCategory,
)


# ============================================================
# FINANCE PAGE
# ============================================================

@require_GET
def finance_page(request):
    """หน้าจัดการรายรับ-รายจ่าย"""
    return render(request, 'finance.html')


# ============================================================
# GET CATEGORIES
# ============================================================

@require_GET
def get_categories(request):
    """ดึงหมวดหมู่ทั้งหมด"""
    categories = (
        ExpenseCategory.objects
        .filter(is_active=True)
        .order_by('category_type', 'name')
    )

    return JsonResponse({
        'success': True,
        'categories': [
            {
                'id': c.id,
                'name': c.name,
                'type': c.category_type,
                'icon': c.icon,
            }
            for c in categories
        ],
    })


# ============================================================
# ADD CATEGORY
# ============================================================

@csrf_exempt
@require_POST
def add_category(request):
    """เพิ่มหมวดหมู่ใหม่"""
    name = request.POST.get('name', '').strip()
    cat_type = request.POST.get('category_type', 'expense')
    icon = request.POST.get('icon', '📦')

    if not name:
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุชื่อหมวดหมู่',
        }, status=400)

    if cat_type not in ('income', 'expense'):
        return JsonResponse({
            'success': False,
            'message': 'ประเภทไม่ถูกต้อง',
        }, status=400)

    category = ExpenseCategory.objects.create(
        name=name,
        category_type=cat_type,
        icon=icon,
    )

    return JsonResponse({
        'success': True,
        'message': f'เพิ่มหมวดหมู่ "{name}" สำเร็จ',
        'category': {
            'id': category.id,
            'name': category.name,
            'type': category.category_type,
            'icon': category.icon,
        },
    })


# ============================================================
# ADD TRANSACTION
# ============================================================

@csrf_exempt
@require_POST
def add_transaction(request):
    """เพิ่มรายการรายรับ/รายจ่าย"""
    tx_type = request.POST.get('transaction_type', '')
    amount_raw = request.POST.get('amount', '0')
    category_id = request.POST.get('category_id', '')
    description = request.POST.get('description', '').strip()
    payment_method = request.POST.get('payment_method', 'cash')
    receipt_date = request.POST.get('receipt_date', '')
    receipt_number = request.POST.get('receipt_number', '').strip()
    notes = request.POST.get('notes', '').strip()

    # Validate
    if tx_type not in ('income', 'expense'):
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุประเภท (รายรับ/รายจ่าย)',
        }, status=400)

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return JsonResponse({
            'success': False,
            'message': 'จำนวนเงินไม่ถูกต้อง',
        }, status=400)

    if amount <= 0:
        return JsonResponse({
            'success': False,
            'message': 'จำนวนเงินต้องมากกว่า 0',
        }, status=400)

    if not receipt_date:
        receipt_date = date.today()
    else:
        try:
            receipt_date = date.fromisoformat(receipt_date)
        except ValueError:
            receipt_date = date.today()

    category = None
    if category_id:
        try:
            category = ExpenseCategory.objects.get(id=int(category_id))
        except (ExpenseCategory.DoesNotExist, ValueError):
            pass

    tx = Transaction.objects.create(
        transaction_type=tx_type,
        amount=amount,
        category=category,
        description=description,
        payment_method=payment_method,
        receipt_date=receipt_date,
        receipt_number=receipt_number,
        notes=notes,
    )

    return JsonResponse({
        'success': True,
        'message': (
            f'บันทึก{"รายรับ" if tx_type == "income" else "รายจ่าย"} '
            f'฿{amount:,.2f} สำเร็จ'
        ),
        'transaction': _serialize_transaction(tx),
    })


# ============================================================
# DELETE TRANSACTION
# ============================================================

@csrf_exempt
@require_POST
def delete_transaction(request):
    """ลบรายการ"""
    tx_id = request.POST.get('transaction_id', '')

    if not tx_id:
        return JsonResponse({
            'success': False,
            'message': 'ไม่พบรหัสรายการ',
        }, status=400)

    try:
        tx = Transaction.objects.get(id=int(tx_id))
    except (Transaction.DoesNotExist, ValueError):
        return JsonResponse({
            'success': False,
            'message': 'ไม่พบรหัสรายการนี้',
        }, status=404)

    tx.delete()

    return JsonResponse({
        'success': True,
        'message': 'ลบรายการสำเร็จ',
    })


# ============================================================
# LIST TRANSACTIONS
# ============================================================

@require_GET
def list_transactions(request):
    """ดึงรายการทั้งหมด พร้อม filter"""
    tx_type = request.GET.get('type', '')  # income / expense / all
    month = request.GET.get('month', '')  # YYYY-MM
    limit_raw = request.GET.get('limit', '200')

    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 200

    qs = Transaction.objects.select_related('category').all()

    if tx_type in ('income', 'expense'):
        qs = qs.filter(transaction_type=tx_type)

    if month:
        try:
            year, mon = month.split('-')
            qs = qs.filter(
                receipt_date__year=int(year),
                receipt_date__month=int(mon),
            )
        except (ValueError, AttributeError):
            pass

    transactions = qs[:limit]

    return JsonResponse({
        'success': True,
        'transactions': [
            _serialize_transaction(tx)
            for tx in transactions
        ],
    })


# ============================================================
# GET SUMMARY
# ============================================================

@require_GET
def get_summary(request):
    """สรุปรายรับ-รายจ่าย ตามเดือน"""
    today = date.today()
    current_month = today.month
    current_year = today.year

    # Monthly summary (last 12 months)
    months_data = []
    for i in range(12):
        if current_month - i <= 0:
            m = current_month - i + 12
            y = current_year - 1
        else:
            m = current_month - i
            y = current_year

        month_income = (
            Transaction.objects
            .filter(
                transaction_type='income',
                receipt_date__year=y,
                receipt_date__month=m,
            )
            .aggregate(total=Sum('amount'))
            .get('total') or 0
        )

        month_expense = (
            Transaction.objects
            .filter(
                transaction_type='expense',
                receipt_date__year=y,
                receipt_date__month=m,
            )
            .aggregate(total=Sum('amount'))
            .get('total') or 0
        )

        months_data.append({
            'month': f'{y}-{m:02d}',
            'month_label': f'{m:02d}/{y}',
            'income': float(month_income),
            'expense': float(month_expense),
            'profit': float(month_income) - float(month_expense),
        })

    months_data.reverse()

    # Current month detail
    current_income = (
        Transaction.objects
        .filter(
            transaction_type='income',
            receipt_date__year=current_year,
            receipt_date__month=current_month,
        )
        .aggregate(total=Sum('amount'))
        .get('total') or 0
    )

    current_expense = (
        Transaction.objects
        .filter(
            transaction_type='expense',
            receipt_date__year=current_year,
            receipt_date__month=current_month,
        )
        .aggregate(total=Sum('amount'))
        .get('total') or 0
    )

    # Expense breakdown by category this month
    expense_by_category = (
        Transaction.objects
        .filter(
            transaction_type='expense',
            receipt_date__year=current_year,
            receipt_date__month=current_month,
        )
        .values('category__name', 'category__icon')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )

    # Income breakdown by category this month
    income_by_category = (
        Transaction.objects
        .filter(
            transaction_type='income',
            receipt_date__year=current_year,
            receipt_date__month=current_month,
        )
        .values('category__name', 'category__icon')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )

    # Total all time
    total_income = (
        Transaction.objects
        .filter(transaction_type='income')
        .aggregate(total=Sum('amount'))
        .get('total') or 0
    )

    total_expense = (
        Transaction.objects
        .filter(transaction_type='expense')
        .aggregate(total=Sum('amount'))
        .get('total') or 0
    )

    return JsonResponse({
        'success': True,
        'months': months_data,
        'current': {
            'income': float(current_income),
            'expense': float(current_expense),
            'profit': float(current_income) - float(current_expense),
            'month_label': f'{current_month:02d}/{current_year}',
        },
        'expense_by_category': [
            {
                'name': e['category__name'] or 'อื่นๆ',
                'icon': e['category__icon'] or '📦',
                'total': float(e['total']),
            }
            for e in expense_by_category
        ],
        'income_by_category': [
            {
                'name': e['category__name'] or 'อื่นๆ',
                'icon': e['category__icon'] or '💰',
                'total': float(e['total']),
            }
            for e in income_by_category
        ],
        'totals': {
            'income': float(total_income),
            'expense': float(total_expense),
            'profit': float(total_income) - float(total_expense),
        },
    })


# ============================================================
# HELPER
# ============================================================

def _serialize_transaction(tx):
    return {
        'id': tx.id,
        'type': tx.transaction_type,
        'amount': float(tx.amount),
        'category_name': tx.category.name if tx.category else '',
        'category_icon': tx.category.icon if tx.category else '📦',
        'category_id': tx.category_id,
        'description': tx.description,
        'payment_method': tx.payment_method,
        'payment_method_display': tx.get_payment_method_display(),
        'receipt_date': tx.receipt_date.isoformat(),
        'receipt_number': tx.receipt_number,
        'notes': tx.notes,
        'created_at': tx.created_at.isoformat(),
    }
