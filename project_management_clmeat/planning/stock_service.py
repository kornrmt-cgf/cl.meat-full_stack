"""
Stock Coverage and Demand Planning Service.

Calculates:
- Current stock for a product
- Average daily usage
- Coverage days
- Projected stock-out date
- Required preparation date
- Eligible packages for a product
"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum, Q


def get_product_stock_summary(product):
    """
    Calculate stock summary for a product.
    
    Returns dict with:
        - total_stock_kg: current usable stock
        - frozen_kg: stock in frozen/queue states
        - available_kg: stock available for new plans
        - package_count: number of usable packages
        - avg_daily_usage_kg: from planning profile
        - safety_stock_days: from planning profile
        - target_coverage_days: from planning profile
        - coverage_days: calculated coverage
        - projected_stockout_date: when stock runs out
        - recommended_ready_date: when product should be ready
    """
    from inventory.models import Package, PackageState

    # Get all active packages for this product
    active_packages = Package.objects.filter(
        product=product,
        current_state__in=[
            PackageState.FROZEN,
            PackageState.PACKED,
            PackageState.READY_FOR_THAW,
            PackageState.THAW_QUEUED,
            PackageState.THAWING,
            PackageState.READY_FOR_SALE,
        ]
    )

    # Calculate stock by state
    frozen_kg = active_packages.filter(
        current_state__in=[PackageState.FROZEN, PackageState.READY_FOR_THAW, PackageState.THAW_QUEUED]
    ).aggregate(total=Sum('weight'))['total'] or Decimal('0')

    usable_kg = active_packages.filter(
        current_state__in=[PackageState.READY_FOR_SALE, PackageState.ON_DISPLAY]
    ).aggregate(total=Sum('weight'))['total'] or Decimal('0')

    total_stock_kg = frozen_kg + usable_kg

    # Get planning profile
    profile = getattr(product, 'planning_profile', None)
    avg_daily_usage = Decimal('0')
    safety_days = Decimal('1')
    target_coverage = Decimal('7')

    if profile:
        avg_daily_usage = profile.avg_daily_usage_kg
        safety_days = profile.safety_stock_days
        target_coverage = profile.target_coverage_days

    # Calculate coverage
    coverage_days = Decimal('0')
    projected_stockout = None
    if avg_daily_usage > 0 and total_stock_kg > 0:
        coverage_days = total_stock_kg / avg_daily_usage
        days_until_stockout = float(coverage_days - safety_days)
        projected_stockout = timezone.localtime(timezone.now()).date() + timedelta(days=max(0, days_until_stockout))

    # Calculate recommended ready date
    recommended_ready_date = None
    if projected_stockout:
        # Product should be ready 1 day before projected stock-out
        recommended_ready_date = projected_stockout - timedelta(days=1)
    elif total_stock_kg == Decimal('0'):
        # No stock at all — ready ASAP
        recommended_ready_date = timezone.localtime(timezone.now()).date() + timedelta(days=1)

    # Get packages without plans for this product (PACKED or FROZEN)
    eligible_packages = Package.objects.filter(
        product=product,
        current_state__in=[PackageState.PACKED, PackageState.FROZEN]
    ).exclude(
        rotation_plan__isnull=False
    ).order_by('packed_at')  # FEFO: oldest first

    # Calculate total eligible weight
    eligible_weight = eligible_packages.aggregate(
        total=Sum('weight')
    )['total'] or Decimal('0')

    return {
        'product': product,
        'total_stock_kg': total_stock_kg,
        'frozen_kg': frozen_kg,
        'usable_kg': usable_kg,
        'package_count': active_packages.count(),
        'avg_daily_usage_kg': avg_daily_usage,
        'safety_stock_days': safety_days,
        'target_coverage_days': target_coverage,
        'coverage_days': coverage_days,
        'projected_stockout_date': projected_stockout,
        'recommended_ready_date': recommended_ready_date,
        'eligible_packages': eligible_packages,
        'eligible_weight_kg': eligible_weight,
        'eligible_count': eligible_packages.count(),
    }


def calculate_required_quantity(product, target_ready_date=None):
    """
    Calculate how much product is needed for a target date.
    
    Args:
        product: Product instance
        target_ready_date: Date when product should be ready
    
    Returns:
        dict with required_kg and existing_planned_kg
    """
    profile = getattr(product, 'planning_profile', None)
    avg_daily_usage = Decimal('0')
    target_coverage = Decimal('7')

    if profile:
        avg_daily_usage = profile.avg_daily_usage_kg
        target_coverage = profile.target_coverage_days

    # How much do we need for the target coverage period?
    required_kg = avg_daily_usage * target_coverage

    # What's already planned (existing RotationPlans that aren't completed/cancelled)
    from planning.models import RotationPlan, PlanStatus
    existing_planned = RotationPlan.objects.filter(
        package__product=product,
        status__in=[PlanStatus.PLANNED, PlanStatus.IN_PROGRESS, PlanStatus.READY]
    ).aggregate(
        total=Sum('package__weight')
    )['total'] or Decimal('0')

    # Get current stock
    stock_summary = get_product_stock_summary(product)

    net_required = required_kg - stock_summary['total_stock_kg'] - existing_planned
    net_required = max(Decimal('0'), net_required)

    return {
        'required_kg': required_kg,
        'existing_planned_kg': existing_planned,
        'current_stock_kg': stock_summary['total_stock_kg'],
        'net_required_kg': net_required,
    }


def calculate_preparation_schedule(target_ready_at, freeze_profile, thaw_profile, package_weight_kg=None):
    """
    Calculate the full preparation schedule backwards from target ready time.
    
    Returns dict with all calculated timestamps and durations.
    """
    from planning.services import calculate_rotation_plan
    from inventory.models import Package, PackageState

    # Create a dummy package for duration calculation if no real package
    if package_weight_kg is None:
        # Use average weight for estimation
        dummy_weight = Decimal('0.600')
    else:
        dummy_weight = Decimal(str(package_weight_kg))

    # Use a temporary unsaved package for duration calculation
    dummy_package = Package(
        product_id=1,
        batch_id=1,
        weight=dummy_weight,
        packed_at=timezone.now(),
        current_state=PackageState.FROZEN
    )

    # Calculate durations
    from planning.services import calculate_freeze_duration, calculate_thaw_duration
    freeze_duration = calculate_freeze_duration(dummy_package, freeze_profile)
    thaw_duration = calculate_thaw_duration(dummy_package, thaw_profile)

    # Backward calculation
    thaw_start_at = target_ready_at - thaw_duration
    thaw_queue_at = thaw_start_at - timedelta(minutes=30)
    freeze_end_at = thaw_start_at - timedelta(minutes=15)
    freeze_start_at = freeze_end_at - freeze_duration

    total_preparation_time = target_ready_at - freeze_start_at

    return {
        'target_ready_at': target_ready_at,
        'planned_thaw_start_at': thaw_start_at,
        'planned_thaw_queue_at': thaw_queue_at,
        'planned_freeze_start_at': freeze_start_at,
        'planned_freeze_end_at': freeze_end_at,
        'freeze_duration': freeze_duration,
        'thaw_duration': thaw_duration,
        'total_preparation_time': total_preparation_time,
    }


def check_plan_conflicts(product, target_ready_at, exclude_plan=None):
    """
    Check for conflicts when creating a plan.
    
    Returns list of warning/error messages.
    """
    from planning.models import RotationPlan, PlanStatus
    warnings = []

    # Check for existing plans on the same date
    existing = RotationPlan.objects.filter(
        package__product=product,
        target_ready_at__date=target_ready_at.date(),
        status__in=[PlanStatus.PLANNED, PlanStatus.IN_PROGRESS]
    )
    if exclude_plan:
        existing = existing.exclude(pk=exclude_plan.pk)

    if existing.exists():
        warnings.append(
            f'⚠️ สินค้า {product.name} มีแผนงานแล้ว {existing.count()} แผน '
            f'สำหรับวันที่ {target_ready_at.strftime("%d/%m/%Y")}'
        )

    # Check if eligible packages are available
    from inventory.models import Package, PackageState
    eligible = Package.objects.filter(
        product=product,
        current_state=PackageState.FROZEN
    ).exclude(rotation_plan__isnull=False)

    if not eligible.exists():
        warnings.append(
            f'🔴 ไม่มีแพ็กเกจที่พร้อมสร้างแผนงานสำหรับ {product.name}'
        )

    return warnings


def get_barcode_package_eligibility(barcode, product=None):
    """
    Check if a barcode corresponds to an eligible package.
    
    Returns dict with eligibility info.
    """
    from inventory.models import Package, PackageState

    result = {
        'found': False,
        'eligible': False,
        'package': None,
        'reason': None,
    }

    try:
        package = Package.objects.select_related('product', 'batch').get(barcode=barcode)
    except Package.DoesNotExist:
        result['reason'] = 'ไม่พบบาร์โค้ดในระบบ'
        return result

    result['found'] = True
    result['package'] = package

    # Check product match
    if product and package.product != product:
        result['reason'] = f'บาร์โค้ดนี้เป็นสินค้า {package.product.name} ไม่ใช่ {product.name}'
        return result

    # Check state
    if package.current_state != PackageState.FROZEN:
        result['reason'] = f'สถานะปัจจุบันคือ {package.get_current_state_display()} (ต้องเป็น FROZEN)'
        return result

    # Check existing plan
    from planning.models import RotationPlan
    if RotationPlan.objects.filter(package=package).exists():
        result['reason'] = 'แพ็กเกจนี้มีแผนงานอยู่แล้ว'
        return result

    result['eligible'] = True
    return result


# ============================================================
# PLANNING STATUS CONSTANTS
# ============================================================

class PlanningStatus:
    SUFFICIENT = 'SUFFICIENT'
    LOW_STOCK = 'LOW_STOCK'
    OUT_OF_STOCK = 'OUT_OF_STOCK'
    INCOMING = 'INCOMING'
    PLANNING_REQUIRED = 'PLANNING_REQUIRED'
    STOCK_GAP = 'STOCK_GAP'
    OVERSTOCKED = 'OVERSTOCKED'

    CHOICES = [
        (SUFFICIENT, 'สต็อคเพียงพอ'),
        (LOW_STOCK, 'สต็อคใกล้หมด'),
        (OUT_OF_STOCK, 'ไม่มีสต็อค'),
        (INCOMING, 'มีสินค้าเข้ามา'),
        (PLANNING_REQUIRED, 'ต้องวางแผน'),
        (STOCK_GAP, 'สต็อคไม่พอ'),
        (OVERSTOCKED, 'สต็อคมากเกินไป'),
    ]

    LABELS = {
        SUFFICIENT: 'สต็อคเพียงพอ',
        LOW_STOCK: 'สต็อคใกล้หมด',
        OUT_OF_STOCK: 'ไม่มีสต็อค',
        INCOMING: 'มีสินค้าเข้ามา',
        PLANNING_REQUIRED: 'ต้องวางแผน',
        STOCK_GAP: 'สต็อคไม่พอ',
        OVERSTOCKED: 'สต็อคมากเกินไป',
    }


# ============================================================
# PLANNING DASHBOARD
# ============================================================

def get_planning_dashboard():
    """
    Build the complete planning dashboard with product cards.
    
    Only returns products that are relevant for planning:
    - Has current stock
    - Has active ProductPlanningProfile
    - Has eligible packages
    - Has existing plans
    - Has a stock gap
    
    Returns list of product cards sorted by urgency.
    """
    from inventory.models import Product, Package, PackageState, ProductPlanningProfile
    from planning.models import RotationPlan, PlanStatus
    from django.db.models import Sum, Count, Q
    
    now = timezone.localtime(timezone.now())
    today = now.date()
    
    # Get all products that have either stock, plans, or a planning profile
    products = Product.objects.filter(
        active=True
    ).filter(
        Q(packages__current_state__in=[
            PackageState.FROZEN, PackageState.PACKED,
            PackageState.READY_FOR_THAW, PackageState.THAW_QUEUED,
            PackageState.THAWING, PackageState.READY_FOR_SALE,
            PackageState.ON_DISPLAY,
        ]) |
        Q(planning_profile__isnull=False) |
        Q(packages__rotation_plan__isnull=False)
    ).distinct().order_by('name')
    
    cards = []
    for product in products:
        card = _build_product_card(product, today)
        if card is not None:
            cards.append(card)
    
    # Sort by urgency: OUT_OF_STOCK > STOCK_GAP > LOW_STOCK > PLANNING_REQUIRED > others
    urgency_order = {
        PlanningStatus.OUT_OF_STOCK: 0,
        PlanningStatus.STOCK_GAP: 1,
        PlanningStatus.LOW_STOCK: 2,
        PlanningStatus.PLANNING_REQUIRED: 3,
        PlanningStatus.INCOMING: 4,
        PlanningStatus.SUFFICIENT: 5,
        PlanningStatus.OVERSTOCKED: 6,
    }
    cards.sort(key=lambda c: urgency_order.get(c['status'], 99))
    
    return cards


def _build_product_card(product, today):
    """
    Build a single product planning card.
    
    Returns None if product has no relevant planning data.
    """
    from inventory.models import Package, PackageState, ProductPlanningProfile
    from planning.models import RotationPlan, PlanStatus
    from django.db.models import Sum
    
    profile = getattr(product, 'planning_profile', None)
    
    # --- Current stock ---
    active_packages = Package.objects.filter(
        product=product,
        current_state__in=[
            PackageState.FROZEN, PackageState.PACKED,
            PackageState.READY_FOR_THAW, PackageState.THAW_QUEUED,
            PackageState.THAWING, PackageState.READY_FOR_SALE,
            PackageState.ON_DISPLAY,
        ]
    )
    
    total_stock_kg = active_packages.aggregate(
        total=Sum('weight')
    )['total'] or Decimal('0')
    
    frozen_kg = active_packages.filter(
        current_state__in=[PackageState.FROZEN, PackageState.READY_FOR_THAW, PackageState.THAW_QUEUED]
    ).aggregate(total=Sum('weight'))['total'] or Decimal('0')
    
    display_kg = active_packages.filter(
        current_state=PackageState.ON_DISPLAY
    ).aggregate(total=Sum('weight'))['total'] or Decimal('0')
    
    # --- Planning profile ---
    avg_daily_usage = Decimal('0')
    safety_days = Decimal('1')
    target_coverage = Decimal('7')
    if profile:
        avg_daily_usage = profile.avg_daily_usage_kg
        safety_days = profile.safety_stock_days
        target_coverage = profile.target_coverage_days
    
    # --- Coverage ---
    coverage_days = Decimal('0')
    projected_stockout = None
    if avg_daily_usage > 0 and total_stock_kg > 0:
        coverage_days = total_stock_kg / avg_daily_usage
        days_until_stockout = float(coverage_days - safety_days)
        projected_stockout = today + timedelta(days=max(0, days_until_stockout))
    elif total_stock_kg == 0:
        projected_stockout = today  # Already out
    
    # --- Existing plans (active) ---
    active_plans = RotationPlan.objects.filter(
        package__product=product,
        status__in=[PlanStatus.PLANNED, PlanStatus.IN_PROGRESS, PlanStatus.READY]
    )
    planned_kg = active_plans.aggregate(
        total=Sum('package__weight')
    )['total'] or Decimal('0')
    planned_count = active_plans.count()
    
    # --- Eligible packages (PACKED or FROZEN, no plan) ---
    eligible_packages = Package.objects.filter(
        product=product,
        current_state__in=[PackageState.PACKED, PackageState.FROZEN]
    ).exclude(
        rotation_plan__isnull=False
    )
    eligible_kg = eligible_packages.aggregate(
        total=Sum('weight')
    )['total'] or Decimal('0')
    eligible_count = eligible_packages.count()
    
    # --- Incoming stock (PACKED = received but not yet frozen) ---
    incoming_kg = Package.objects.filter(
        product=product,
        current_state=PackageState.PACKED
    ).aggregate(total=Sum('weight'))['total'] or Decimal('0')
    
    # --- Calculate net required ---
    required_kg = avg_daily_usage * target_coverage if avg_daily_usage > 0 else Decimal('0')
    net_required = required_kg - total_stock_kg - planned_kg
    net_required = max(Decimal('0'), net_required)
    
    # --- Determine planning status ---
    status = _determine_planning_status(
        total_stock_kg=total_stock_kg,
        coverage_days=coverage_days,
        safety_days=safety_days,
        net_required=net_required,
        incoming_kg=incoming_kg,
        eligible_kg=eligible_kg,
        has_profile=profile is not None,
        projected_stockout=projected_stockout,
        today=today,
    )
    
    # --- Recommended ready date ---
    recommended_ready_date = None
    if projected_stockout:
        recommended_ready_date = projected_stockout - timedelta(days=1)
    elif total_stock_kg == 0 and incoming_kg == 0:
        recommended_ready_date = today + timedelta(days=1)
    
    return {
        'product_id': product.pk,
        'product_name': product.name,
        'product_sku': product.sku,
        'category': product.get_category_display(),
        'category_code': product.category,
        'status': status,
        'status_label': PlanningStatus.LABELS.get(status, status),
        'current_stock_kg': _fmt_kg(total_stock_kg),
        'frozen_kg': _fmt_kg(frozen_kg),
        'display_kg': _fmt_kg(display_kg),
        'avg_daily_usage_kg': _fmt_kg(avg_daily_usage),
        'safety_stock_days': _fmt_kg(safety_days),
        'target_coverage_days': _fmt_kg(target_coverage),
        'coverage_days': _fmt_kg(coverage_days),
        'projected_stockout_date': projected_stockout.isoformat() if projected_stockout else None,
        'incoming_kg': _fmt_kg(incoming_kg),
        'planned_kg': _fmt_kg(planned_kg),
        'planned_count': planned_count,
        'eligible_kg': _fmt_kg(eligible_kg),
        'eligible_count': eligible_count,
        'net_required_kg': _fmt_kg(net_required),
        'recommended_ready_date': recommended_ready_date.isoformat() if recommended_ready_date else None,
        'has_profile': profile is not None,
    }


def _fmt_kg(value):
    """Format kg value to 1 decimal place."""
    if value is None:
        return '0.0'
    d = Decimal(str(value))
    return str(d.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def _determine_planning_status(
    total_stock_kg, coverage_days, safety_days, net_required,
    incoming_kg, eligible_kg, has_profile, projected_stockout, today
):
    """
    Determine the planning status for a product.
    
    Priority order:
    1. OUT_OF_STOCK — no usable stock at all
    2. STOCK_GAP — stock is insufficient even with planned supply
    3. LOW_STOCK — coverage is approaching safety threshold
    4. PLANNING_REQUIRED — needs preparation but stock exists
    5. INCOMING — has incoming stock to replenish
    6. SUFFICIENT — stock is adequate
    7. OVERSTOCKED — stock exceeds target coverage
    """
    if not has_profile:
        # No planning profile — show as SUFFICIENT if product has stock
        # Return None only if no stock at all (nothing to plan)
        if total_stock_kg > 0:
            return PlanningStatus.SUFFICIENT
        return None  # Don't show in dashboard — nothing to plan
    
    # OUT_OF_STOCK
    if total_stock_kg == 0 and incoming_kg == 0:
        return PlanningStatus.OUT_OF_STOCK
    
    # STOCK_GAP — projected to run out before any planned supply arrives
    if net_required > 0 and incoming_kg == 0 and eligible_kg == 0:
        return PlanningStatus.STOCK_GAP
    
    # LOW_STOCK — coverage below safety threshold
    if coverage_days > 0 and coverage_days <= safety_days:
        return PlanningStatus.LOW_STOCK
    
    # PLANNING_REQUIRED — net required > 0 but we have eligible packages
    if net_required > 0 and eligible_kg > 0:
        return PlanningStatus.PLANNING_REQUIRED
    
    # INCOMING — has packed (incoming) stock
    if incoming_kg > 0:
        return PlanningStatus.INCOMING
    
    # OVERSTOCKED — coverage exceeds 2x target
    if has_profile and coverage_days > 0:
        target = safety_days + Decimal('7')  # target coverage
        if coverage_days > target * 2:
            return PlanningStatus.OVERSTOCKED
    
    # SUFFICIENT
    return PlanningStatus.SUFFICIENT
