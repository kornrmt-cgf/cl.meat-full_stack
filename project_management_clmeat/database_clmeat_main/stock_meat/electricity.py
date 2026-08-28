from django.shortcuts import render
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Sum, Avg
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from datetime import date

from stock_meat.models import ElectricityBill


RATE_PER_UNIT = 4.58


@require_GET
def electricity_page(request):
    return render(request, 'electricity.html')


@csrf_exempt
@require_POST
def add_electricity_bill(request):
    month = request.POST.get('month', '')
    year = request.POST.get('year', '')
    units_raw = request.POST.get('units_used', '')
    meter_raw = request.POST.get('meter_reading', '')
    prev_raw = request.POST.get('previous_reading', '')
    notes = request.POST.get('notes', '').strip()

    try:
        month = int(month)
        year = int(year)
        units = float(units_raw)
    except (TypeError, ValueError):
        return JsonResponse({
            'success': False,
            'message': 'กรุณาระบุข้อมูลให้ถูกต้อง',
        }, status=400)

    if month < 1 or month > 12:
        return JsonResponse({
            'success': False,
            'message': 'เดือนไม่ถูกต้อง',
        }, status=400)

    if units < 0:
        return JsonResponse({
            'success': False,
            'message': 'หน่วยต้องมากกว่าหรือเท่ากับ 0',
        }, status=400)

    meter = None
    prev = None
    try:
        if meter_raw:
            meter = float(meter_raw)
    except (TypeError, ValueError):
        pass
    try:
        if prev_raw:
            prev = float(prev_raw)
    except (TypeError, ValueError):
        pass

    # Auto-calculate units from meter readings
    if meter is not None and prev is not None and units == 0:
        units = max(0, meter - prev)

    bill, created = ElectricityBill.objects.update_or_create(
        month=month,
        year=year,
        defaults={
            'units_used': units,
            'meter_reading': meter,
            'previous_reading': prev,
            'notes': notes,
        },
    )
    # Trigger save to calculate total_amount
    bill.save()

    # Auto-create expense transaction
    elec_cat = ExpenseCategory.objects.filter(name__icontains='ค่าไฟ').first()
    if not elec_cat:
        elec_cat = ExpenseCategory.objects.filter(category_type='expense').first()
    if elec_cat and bill.total_amount_float > 0:
        desc = f'ค่าไฟฟ้า {month:02d}/{year} ({bill.units_used_float} หน่วย)'
        existing = Transaction.objects.filter(
            description__icontains=f'ค่าไฟฟ้า {month:02d}/{year}'
        ).first()
        if not existing:
            Transaction.objects.create(
                transaction_type='expense',
                category=elec_cat,
                amount=bill.total_amount_float,
                description=desc,
                receipt_date=date(year, month, 1),
            )
        elif existing.amount != bill.total_amount_float:
            existing.amount = bill.total_amount_float
            existing.save(update_fields=['amount'])

    action = 'เพิ่ม' if created else 'อัปเดต'
    return JsonResponse({
        'success': True,
        'message': f'{action}ค่าไฟ {month:02d}/{year} สำเร็จ',
        'bill': {
            'id': bill.id,
            'month': bill.month,
            'year': bill.year,
            'units_used': bill.units_used_float,
            'total_amount': bill.total_amount_float,
            'meter_reading': bill.meter_reading,
            'previous_reading': bill.previous_reading,
            'notes': bill.notes,
        },
    })


@csrf_exempt
@require_POST
def delete_electricity_bill(request):
    bill_id = request.POST.get('bill_id', '')
    if not bill_id:
        return JsonResponse({
            'success': False,
            'message': 'ไม่พบรหัส',
        }, status=400)
    try:
        ElectricityBill.objects.get(id=int(bill_id)).delete()
    except (ElectricityBill.DoesNotExist, ValueError):
        return JsonResponse({
            'success': False,
            'message': 'ไม่พบรายการ',
        }, status=404)
    return JsonResponse({'success': True, 'message': 'ลบสำเร็จ'})


@require_GET
def list_electricity_bills(request):
    bills = ElectricityBill.objects.all()[:24]

    total_units = 0
    total_amount = 0
    avg_units = 0

    stats = ElectricityBill.objects.aggregate(
        total_u=Sum('units_used'),
        total_a=Sum('total_amount'),
        avg_u=Avg('units_used'),
    )
    if stats['total_u']:
        total_units = float(stats['total_u'])
    if stats['total_a']:
        total_amount = float(stats['total_a'])
    if stats['avg_u']:
        avg_units = float(stats['avg_u'])

    return JsonResponse({
        'success': True,
        'rate_per_unit': RATE_PER_UNIT,
        'bills': [
            {
                'id': b.id,
                'month': b.month,
                'year': b.year,
                'month_label': f'{b.month:02d}/{b.year}',
                'units_used': b.units_used_float,
                'total_amount': b.total_amount_float,
                'meter_reading': float(b.meter_reading) if b.meter_reading else None,
                'previous_reading': float(b.previous_reading) if b.previous_reading else None,
                'notes': b.notes,
            }
            for b in bills
        ],
        'stats': {
            'total_units': total_units,
            'total_amount': total_amount,
            'avg_units': round(avg_units, 1),
            'avg_amount': round(avg_units * RATE_PER_UNIT, 2),
            'record_count': ElectricityBill.objects.count(),
        },
    })


@require_GET
def electricity_calculator(request):
    units_raw = request.GET.get('units', '0')
    try:
        units = float(units_raw)
    except (TypeError, ValueError):
        units = 0
    return JsonResponse({
        'success': True,
        'units': units,
        'rate': RATE_PER_UNIT,
        'total': round(units * RATE_PER_UNIT, 2),
    })


# ============================================================
# LATEST METER READING
# ============================================================

@require_GET
def latest_meter(request):
    """
    ดึงเลขมิเตอร์ล่าสุดสำหรับ auto-fill
    """
    from stock_meat.models import ElectricityBill
    
    latest = ElectricityBill.objects.order_by('-year', '-month').first()
    if latest and latest.meter_reading:
        return JsonResponse({
            'success': True,
            'previous_meter': latest.meter_reading,
            'month': latest.month,
            'year': latest.year,
        })
    return JsonResponse({
        'success': True,
        'previous_meter': 0,
    })


# ============================================================
# DAILY ELECTRICITY DATA
# ============================================================

@require_GET
def daily_electricity_list(request):
    """ดึงข้อมูลค่าไฟรายวัน"""
    from stock_meat.models import DailyElectricity
    from datetime import date, timedelta
    
    days_raw = request.GET.get('days', '60')
    try:
        days = int(days_raw)
    except (TypeError, ValueError):
        days = 60
    
    since = date.today() - timedelta(days=days)
    records = DailyElectricity.objects.filter(date__gte=since).order_by('-date')
    
    data = []
    for r in records:
        data.append({
            'date': r.date.isoformat(),
            'date_label': r.date.strftime('%d/%m/%Y'),
            'meter_reading': float(r.meter_reading) if r.meter_reading else None,
            'units': float(r.units_used),
            'amount': float(r.amount),
        })
    
    return JsonResponse({
        'success': True,
        'count': len(data),
        'data': data,
    })


@csrf_exempt
def daily_electricity_add(request):
    """เพิ่มค่าไฟรายวัน"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)
    
    from stock_meat.models import DailyElectricity
    from datetime import date
    from decimal import Decimal
    
    try:
        date_str = request.POST.get('date', '')
        meter_reading = request.POST.get('meter_reading', '0')
        units_used = request.POST.get('units_used', '0')
        previous_reading = request.POST.get('previous_reading', '')
        notes = request.POST.get('notes', '')
        
        if not date_str:
            return JsonResponse({'success': False, 'message': 'กรุณาระบุวันที่'})
        
        d = date.fromisoformat(date_str)
        
        # Get previous day's meter reading
        prev_meter = None
        if previous_reading:
            prev_meter = Decimal(previous_reading)
        
        units = Decimal(units_used or '0')
        meter = Decimal(meter_reading or '0')
        
        if units == 0 and prev_meter is not None and meter > prev_meter:
            units = meter - prev_meter
        
        amount = units * Decimal('4.58')
        
        # Upsert: update if same date exists
        obj, created = DailyElectricity.objects.update_or_create(
            date=d,
            defaults={
                'meter_reading': meter,
                'units_used': units,
                'amount': amount,
                'notes': notes,
            }
        )
        
        action = 'เพิ่ม' if created else 'อัปเดต'
        return JsonResponse({
            'success': True,
            'message': f'{action}ค่าไฟวันที่ {d.strftime("%d/%m/%Y")} สำเร็จ',
            'amount': float(amount),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})


@csrf_exempt
def daily_electricity_delete(request):
    """ลบค่าไฟรายวัน"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)
    
    from stock_meat.models import DailyElectricity
    from datetime import date
    
    try:
        date_str = request.POST.get('date', '')
        d = date.fromisoformat(date_str)
        DailyElectricity.objects.filter(date=d).delete()
        return JsonResponse({
            'success': True,
            'message': f'ลบค่าไฟวันที่ {d.strftime("%d/%m/%Y")} สำเร็จ',
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})
