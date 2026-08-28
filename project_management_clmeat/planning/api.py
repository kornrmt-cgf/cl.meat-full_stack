"""
Planning API Views: JSON responses for frontend.
"""
import json
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import datetime
from .selectors import get_all_plans, get_queue, get_calendar_data
from .services import create_rotation_plan, add_to_thaw_queue, remove_from_thaw_queue
from common.time_service import format_display


@login_required
@require_http_methods(["GET"])
def eligible_packages_api(request):
    """Get eligible packages for plan creation, optionally filtered by product."""
    from inventory.models import Package, PackageState
    product_id = request.GET.get('product_id')

    packages = Package.objects.filter(
        current_state__in=[PackageState.PACKED, PackageState.FROZEN]
    ).exclude(
        rotation_plan__isnull=False
    ).select_related('product', 'batch')

    if product_id:
        packages = packages.filter(product_id=product_id)

    packages = packages.order_by('product__name', 'weight')

    state_labels = {
        'PACKED': {'label': 'ต้องแช่แข็งก่อน', 'icon': '🟡', 'class': 'state-packed'},
        'FROZEN': {'label': 'พร้อมละลาย', 'icon': '🧊', 'class': 'state-frozen'},
    }

    data = []
    for pkg in packages:
        w = _fmt_kg(pkg.weight)
        state_info = state_labels.get(pkg.current_state, {'label': pkg.get_current_state_display(), 'icon': '❓', 'class': ''})
        data.append({
            'id': pkg.pk,
            'product_name': pkg.product.name,
            'weight': w,
            'barcode': pkg.barcode or '',
            'batch_number': pkg.batch.batch_number if pkg.batch else '',
            'state': pkg.current_state,
            'state_label': state_info['label'],
            'state_icon': state_info['icon'],
            'state_class': state_info['class'],
            'display': f"{pkg.product.name} | {w} กก. | {pkg.barcode or '-'} | {state_info['icon']} {state_info['label']}",
        })

    return JsonResponse({'packages': data})


from decimal import Decimal, ROUND_HALF_UP


def _fmt_kg(value):
    """Format kg value to 1 decimal place."""
    if value is None:
        return '0.0'
    d = Decimal(str(value))
    return str(d.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def _fmt_days(value):
    """Format days to 1 decimal place."""
    if value is None:
        return '0.0'
    d = Decimal(str(value))
    return str(d.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


@login_required
@require_http_methods(["GET"])
def stock_analysis_api(request):
    """Get stock analysis for a product."""
    product_id = request.GET.get('product_id')
    if not product_id:
        return JsonResponse({'error': 'product_id required'}, status=400)

    from inventory.models import Product
    try:
        product = Product.objects.get(pk=product_id)
    except Product.DoesNotExist:
        return JsonResponse({'error': 'Product not found'}, status=404)

    from .stock_service import get_product_stock_summary, calculate_required_quantity
    summary = get_product_stock_summary(product)
    quantity = calculate_required_quantity(product)

    # Convert to JSON-serializable
    packages = []
    for pkg in summary['eligible_packages']:
        pw = _fmt_kg(pkg.weight)
        packages.append({
            'id': pkg.pk,
            'weight': pw,
            'barcode': pkg.barcode or '',
            'batch': pkg.batch.batch_number if pkg.batch else '',
            'packed_at': pkg.packed_at.strftime('%d/%m/%Y'),
            'display': f"{product.name} | {pw} กก. | {pkg.barcode or '-'} | {pkg.get_current_state_display()}",
        })

    data = {
        'product': {'id': product.pk, 'name': product.name, 'category': product.category},
        'total_stock_kg': _fmt_kg(summary['total_stock_kg']),
        'frozen_kg': _fmt_kg(summary['frozen_kg']),
        'usable_kg': _fmt_kg(summary['usable_kg']),
        'package_count': summary['package_count'],
        'avg_daily_usage_kg': _fmt_kg(summary['avg_daily_usage_kg']),
        'safety_stock_days': _fmt_days(summary['safety_stock_days']),
        'target_coverage_days': _fmt_days(summary['target_coverage_days']),
        'coverage_days': _fmt_days(summary['coverage_days']),
        'projected_stockout_date': summary['projected_stockout_date'].isoformat() if summary['projected_stockout_date'] else None,
        'recommended_ready_date': summary['recommended_ready_date'].isoformat() if summary['recommended_ready_date'] else None,
        'eligible_packages': packages,
        'eligible_weight_kg': _fmt_kg(summary['eligible_weight_kg']),
        'eligible_count': summary['eligible_count'],
        'required_kg': _fmt_kg(quantity['required_kg']),
        'existing_planned_kg': _fmt_kg(quantity['existing_planned_kg']),
        'net_required_kg': _fmt_kg(quantity['net_required_kg']),
    }
    return JsonResponse(data)


@login_required
@require_http_methods(["GET"])
def barcode_check_api(request):
    """Check barcode eligibility for planning."""
    barcode = request.GET.get('barcode', '').strip()
    product_id = request.GET.get('product_id')

    if not barcode:
        return JsonResponse({'error': 'barcode required'}, status=400)

    product = None
    if product_id:
        from inventory.models import Product
        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist:
            return JsonResponse({'error': 'Product not found'}, status=404)

    from .stock_service import get_barcode_package_eligibility
    result = get_barcode_package_eligibility(barcode, product)

    data = {
        'found': result['found'],
        'eligible': result['eligible'],
        'reason': result['reason'],
    }

    if result['package']:
        pkg = result['package']
        data['package'] = {
            'id': pkg.pk,
            'product_name': pkg.product.name,
            'weight': _fmt_kg(pkg.weight),
            'barcode': pkg.barcode,
            'batch': pkg.batch.batch_number if pkg.batch else '',
            'state': pkg.current_state,
            'state_display': pkg.get_current_state_display(),
            'packed_at': pkg.packed_at.strftime('%d/%m/%Y'),
        }

    return JsonResponse(data)


def _require_permission(request, perm):
    """Check permission, return error response if not authorized."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'กรุณาเข้าสู่ระบบ'}, status=401)
    if not request.user.has_perm(perm):
        return JsonResponse({'error': 'ไม่มีสิทธิ์ดำเนินการนี้'}, status=403)
    return None


@login_required
@require_http_methods(["GET"])
def plan_list_api(request):
    """List rotation plans."""
    status = request.GET.get('status')
    plans = get_all_plans(status=status)
    
    data = []
    for plan in plans:
        data.append({
            'id': plan.pk,
            'package_name': plan.package.display_name,
            'package_id': plan.package.pk,
            'target_ready_at': format_display(plan.target_ready_at),
            'planned_thaw_start_at': format_display(plan.planned_thaw_start_at),
            'planned_freeze_start_at': format_display(plan.planned_freeze_start_at),
            'freeze_profile': plan.freeze_profile.name,
            'thaw_profile': plan.thaw_profile.name,
            'status': plan.status,
        })
    
    return JsonResponse({'plans': data})


@login_required
@require_http_methods(["POST"])
def plan_create_api(request):
    """Create a rotation plan via API."""
    perm_err = _require_permission(request, 'planning.add_rotationplan')
    if perm_err:
        return perm_err
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    try:
        from inventory.models import Package
        from .models import FreezeProfile, ThawProfile
        
        package = Package.objects.get(pk=data['package_id'])
        freeze_profile = FreezeProfile.objects.get(pk=data['freeze_profile_id'])
        thaw_profile = ThawProfile.objects.get(pk=data['thaw_profile_id'])
        
        target_ready_at = datetime.fromisoformat(data['target_ready_at'])
        if timezone.is_naive(target_ready_at):
            target_ready_at = timezone.make_aware(target_ready_at)
        
        plan = create_rotation_plan(
            package=package,
            target_ready_at=target_ready_at,
            freeze_profile=freeze_profile,
            thaw_profile=thaw_profile,
            actor=data.get('actor', 'api')
        )
        
        return JsonResponse({
            'id': plan.pk,
            'message': 'Rotation plan created successfully'
        }, status=201)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_http_methods(["GET"])
def plan_calendar_api(request):
    """Get calendar data for monthly planner."""
    year = int(request.GET.get('year', timezone.now().year))
    month = int(request.GET.get('month', timezone.now().month))
    
    calendar_data = get_calendar_data(year, month)
    
    data = []
    for day_data in calendar_data:
        data.append({
            'date': day_data['date'].isoformat(),
            'required': day_data['required'],
            'planned': day_data['planned'],
            'status': day_data['status'],
        })
    
    return JsonResponse({'calendar': data})


@require_http_methods(["GET"])
def plan_detail_api(request, pk):
    """Get plan detail."""
    from .models import RotationPlan
    
    try:
        plan = RotationPlan.objects.select_related(
            'package', 'package__product', 'freeze_profile', 'thaw_profile'
        ).get(pk=pk)
    except RotationPlan.DoesNotExist:
        return JsonResponse({'error': 'Plan not found'}, status=404)
    
    data = {
        'id': plan.pk,
        'package_name': plan.package.display_name,
        'package_id': plan.package.pk,
        'target_ready_at': format_display(plan.target_ready_at),
        'planned_thaw_start_at': format_display(plan.planned_thaw_start_at),
        'planned_thaw_queue_at': format_display(plan.planned_thaw_queue_at),
        'planned_freeze_start_at': format_display(plan.planned_freeze_start_at),
        'planned_freeze_end_at': format_display(plan.planned_freeze_end_at),
        'freeze_profile': plan.freeze_profile.name,
        'thaw_profile': plan.thaw_profile.name,
        'freeze_duration': str(plan.freeze_duration),
        'thaw_duration': str(plan.thaw_duration),
        'status': plan.status,
    }
    
    return JsonResponse(data)


@login_required
@require_http_methods(["POST"])
def plan_recalculate_api(request, pk):
    """Recalculate a rotation plan."""
    from .models import RotationPlan
    
    try:
        plan = RotationPlan.objects.get(pk=pk)
    except RotationPlan.DoesNotExist:
        return JsonResponse({'error': 'Plan not found'}, status=404)
    
    try:
        from .services import calculate_rotation_plan
        
        plan_data = calculate_rotation_plan(
            plan.package,
            plan.target_ready_at,
            plan.freeze_profile,
            plan.thaw_profile
        )
        
        plan.planned_thaw_start_at = plan_data['planned_thaw_start_at']
        plan.planned_thaw_queue_at = plan_data['planned_thaw_queue_at']
        plan.planned_freeze_start_at = plan_data['planned_freeze_start_at']
        plan.planned_freeze_end_at = plan_data['planned_freeze_end_at']
        plan.freeze_duration = plan_data['freeze_duration']
        plan.thaw_duration = plan_data['thaw_duration']
        plan.save()
        
        return JsonResponse({'message': 'Plan recalculated successfully'})
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_http_methods(["GET"])
def queue_list_api(request):
    """List thaw queue entries."""
    queue_entries = get_queue()
    
    data = []
    for entry in queue_entries:
        data.append({
            'id': entry.pk,
            'queue_position': entry.queue_position,
            'package_name': entry.package.display_name,
            'package_id': entry.package.pk,
            'planned_start_at': format_display(entry.planned_start_at),
            'target_ready_at': format_display(entry.target_ready_at),
            'status': entry.status,
        })
    
    return JsonResponse({'queue': data})


@login_required
@require_http_methods(["POST"])
def queue_add_api(request):
    """Add package to thaw queue."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    try:
        from inventory.models import Package
        
        package = Package.objects.get(pk=data['package_id'])
        rotation_plan = package.rotation_plan
        
        entry = add_to_thaw_queue(
            package=package,
            rotation_plan=rotation_plan,
            actor=data.get('actor', 'api')
        )
        
        return JsonResponse({
            'id': entry.pk,
            'queue_position': entry.queue_position,
            'message': 'Added to thaw queue'
        }, status=201)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def queue_remove_api(request, pk):
    """Remove from thaw queue."""
    from .models import ThawQueueEntry
    
    try:
        entry = ThawQueueEntry.objects.get(pk=pk)
    except ThawQueueEntry.DoesNotExist:
        return JsonResponse({'error': 'Queue entry not found'}, status=404)
    
    try:
        remove_from_thaw_queue(entry)
        return JsonResponse({'message': 'Removed from queue'})
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_http_methods(["GET"])
def conflicts_api(request):
    """Check for scheduling conflicts."""
    from .services import check_conflicts
    
    # Check for today's conflicts
    today = timezone.localtime(timezone.now()).date()
    conflicts = check_conflicts(
        datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
    )
    
    return JsonResponse({'conflicts': conflicts})


@login_required
@require_http_methods(["GET"])
def planning_dashboard_api(request):
    """
    Get the planning dashboard — product cards with planning status.
    
    Only returns products relevant for inventory planning.
    """
    from .stock_service import get_planning_dashboard
    cards = get_planning_dashboard()
    return JsonResponse({'cards': cards})
