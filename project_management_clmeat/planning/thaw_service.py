"""
Thaw Calculation Service — configurable, product-aware thaw duration.

All thaw duration values are ESTIMATES based on configurable business rules.
They are NOT universal food-safety guarantees.

Thaw time depends on many factors:
- Starting temperature (frozen core temperature)
- Package geometry (thickness, surface area)
- Packaging type (vacuum, tray, loose)
- Airflow in thaw area
- Refrigerator/ambient temperature
- Product density and composition

The calculations here use weight-based interpolation with configurable
thresholds and safety buffers as a reasonable approximation.

Reference background:
- USDA FSIS: Refrigerated Thawing (Slow Thaw) recommends thawing in
  refrigerator at 40°F (4.4°C) or below.
- Weight-based approximation: ~24 hours per 5 lbs (2.3 kg) for
  refrigerator thawing is a common rule of thumb.
- The actual values used here are store-specific and configurable.
"""
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum, Q, Count


# ============================================================
# THAW DURATION CALCULATION
# ============================================================

def calculate_thaw_duration(package, thaw_profile):
    """
    Calculate thaw duration for a package using the thaw profile.
    
    Duration is determined by:
    1. Package weight vs weight_threshold_kg
    2. Weight scale factor for larger packages
    3. Safety buffer always added
    
    Args:
        package: Package instance (needs .weight)
        thaw_profile: ThawProfile instance
    
    Returns:
        timedelta: Calculated thaw duration (estimated)
    
    Note:
        This is a CONFIGURABLE ESTIMATE, not a safety guarantee.
        Adjust weight_threshold_kg, durations, and buffer to match
        your store's validated SOP.
    """
    weight_kg = float(package.weight)
    threshold = float(thaw_profile.weight_threshold_kg)
    scale = float(thaw_profile.weight_scale_factor)
    
    min_dur = thaw_profile.minimum_duration
    default_dur = thaw_profile.default_duration
    
    if weight_kg <= threshold:
        # Small packages: use minimum duration
        duration = min_dur
    elif weight_kg <= threshold * 2:
        # Medium packages: interpolate linearly between min and default
        # weight ranges from threshold to 2×threshold
        # duration ranges from min_dur to default_dur
        fraction = (weight_kg - threshold) / threshold
        min_secs = min_dur.total_seconds()
        default_secs = default_dur.total_seconds()
        interp_secs = min_secs + fraction * (default_secs - min_secs)
        duration = timedelta(seconds=int(interp_secs))
    else:
        # Large packages: use default * scale_factor
        secs = int(default_dur.total_seconds() * scale)
        duration = timedelta(seconds=secs)
    
    # Always add safety buffer
    duration += thaw_profile.buffer_duration
    
    return duration


def get_effective_thaw_duration(package, thaw_profile):
    """
    Get the effective thaw duration, considering any manual override.
    
    Args:
        package: Package instance
        thaw_profile: ThawProfile instance
    
    Returns:
        dict: {
            'duration': timedelta (the duration to use),
            'is_override': bool,
            'base_duration': timedelta (calculated before override),
            'source': str ('profile', 'override', 'estimated'),
        }
    """
    # Check for manual override on existing rotation plan
    from planning.models import RotationPlan
    plan = RotationPlan.objects.filter(package=package).first()
    
    base_duration = calculate_thaw_duration(package, thaw_profile)
    
    if plan and plan.thaw_override:
        return {
            'duration': plan.thaw_override,
            'is_override': True,
            'base_duration': base_duration,
            'source': 'override',
        }
    
    return {
        'duration': base_duration,
        'is_override': False,
        'base_duration': base_duration,
        'source': 'profile',
    }


# ============================================================
# TEMPERATURE VALIDATION
# ============================================================

def validate_temperature(actual_temperature, thaw_profile=None, location=None):
    """
    Validate a temperature reading against allowed range.
    
    The range comes from thaw_profile (preferred) or location (fallback).
    
    Args:
        actual_temperature: Decimal or float, temperature in Celsius
        thaw_profile: ThawProfile instance (optional)
        location: StorageLocation instance (optional)
    
    Returns:
        dict: {
            'valid': bool,
            'status': str ('OK', 'WARNING', 'CRITICAL'),
            'min_allowed': Decimal or None,
            'max_allowed': Decimal or None,
            'target': Decimal or None,
            'message': str,
        }
    """
    actual = Decimal(str(actual_temperature))
    
    # Determine range from profile or location
    min_temp = None
    max_temp = None
    target_temp = None
    
    if thaw_profile:
        min_temp = thaw_profile.min_temperature
        max_temp = thaw_profile.max_temperature
        target_temp = thaw_profile.target_temperature
    elif location:
        min_temp = location.min_temperature
        max_temp = location.max_temperature
    
    # If no range configured, can't validate
    if min_temp is None and max_temp is None:
        return {
            'valid': True,
            'status': 'OK',
            'min_allowed': None,
            'max_allowed': None,
            'target': target_temp,
            'message': 'ไม่มีการกำหนดช่วงอุณหภูมิ — ตรวจสอบด้วยตนเอง',
        }
    
    # Check against range
    status = 'OK'
    valid = True
    message = 'อุณหภูมิอยู่ในช่วงที่กำหนด'
    
    if min_temp is not None and actual < min_temp:
        status = 'CRITICAL'
        valid = False
        message = f'อุณหภูมิต่ำเกินไป: {actual}°C (ต่ำสุดที่กำหนด: {min_temp}°C)'
    elif max_temp is not None and actual > max_temp:
        status = 'CRITICAL'
        valid = False
        message = f'อุณหภูมิสูงเกินไป: {actual}°C (สูงสุดที่กำหนด: {max_temp}°C)'
    else:
        # Warning zone: strictly between boundary and 1°C inside
        # At exactly min or max = OK
        # Between min and min+1 = WARNING low
        # Between max-1 and max = WARNING high
        warning_buffer = Decimal('1.0')
        if min_temp is not None and actual > min_temp and actual < min_temp + warning_buffer:
            status = 'WARNING'
            message = f'อุณหภูมิใกล้ขีดจำกัดล่าง: {actual}°C (ต่ำสุด: {min_temp}°C)'
        elif max_temp is not None and actual < max_temp and actual > max_temp - warning_buffer:
            status = 'WARNING'
            message = f'อุณหภูมิใกล้ขีดจำกัดบน: {actual}°C (สูงสุด: {max_temp}°C)'
    
    return {
        'valid': valid,
        'status': status,
        'min_allowed': min_temp,
        'max_allowed': max_temp,
        'target': target_temp,
        'message': message,
    }


def record_temperature_reading(location, actual_temperature, thaw_profile=None,
                                source='MANUAL', recorded_by='', notes=''):
    """
    Record a temperature reading and validate it.
    
    Creates a TemperatureLog entry with automatic status determination.
    
    Args:
        location: StorageLocation instance
        actual_temperature: Decimal or float
        thaw_profile: ThawProfile (optional, for range reference)
        source: str ('MANUAL', 'SENSOR', 'BLUETOOTH')
        recorded_by: str
        notes: str
    
    Returns:
        dict: {
            'log': TemperatureLog instance,
            'validation': validation result dict,
        }
    """
    from inventory.models import TemperatureLog
    
    # Validate
    validation = validate_temperature(actual_temperature, thaw_profile, location)
    
    # Determine status for the log
    status_map = {'OK': 'OK', 'WARNING': 'WARNING', 'CRITICAL': 'CRITICAL'}
    log_status = status_map.get(validation['status'], 'OK')
    
    # Create log entry
    log = TemperatureLog.objects.create(
        location=location,
        actual_temperature=Decimal(str(actual_temperature)),
        target_temperature=validation['target'],
        min_allowed=validation['min_allowed'],
        max_allowed=validation['max_allowed'],
        status=log_status,
        source=source,
        recorded_by=recorded_by,
        notes=notes,
    )
    
    return {
        'log': log,
        'validation': validation,
    }


# ============================================================
# CAPACITY CHECK
# ============================================================

def check_thaw_capacity(thaw_profile=None, location=None, target_time=None):
    """
    Check if thaw capacity is available.
    
    Checks both:
    1. Profile-level capacity (thaw_capacity on ThawProfile)
    2. Location-level capacity (thaw_capacity on StorageLocation)
    
    Args:
        thaw_profile: ThawProfile (optional)
        location: StorageLocation (optional)
        target_time: datetime to check capacity at (optional, defaults to now)
    
    Returns:
        dict: {
            'available': bool,
            'current_count': int,
            'max_capacity': int,
            'source': str,
            'message': str,
        }
    """
    from planning.models import ThawQueueEntry, QueueStatus
    
    if target_time is None:
        target_time = timezone.now()
    
    # Count active thaw operations at the target time
    # An entry is "active" at target_time if:
    #   1. Its planned_start_at <= target_time (it should have started)
    #   2. Its target_ready_at >= target_time (it's not done yet)
    #   3. Status is QUEUED, READY_TO_START, or STARTED
    active = ThawQueueEntry.objects.filter(
        planned_start_at__lte=target_time,
        target_ready_at__gte=target_time,
        status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START, QueueStatus.STARTED],
    )
    
    current_count = active.count()
    
    # Determine max capacity
    max_capacity = 20  # absolute default
    source = 'default'
    
    if location and location.thaw_capacity:
        max_capacity = location.thaw_capacity
        source = f'location:{location.name}'
    
    if thaw_profile and thaw_profile.thaw_capacity:
        # Use the smaller of location and profile capacity
        profile_cap = thaw_profile.thaw_capacity
        if profile_cap < max_capacity:
            max_capacity = profile_cap
            source = f'profile:{thaw_profile.name}'
    
    available = current_count < max_capacity
    
    if available:
        message = f'พร้อมรับ: {current_count}/{max_capacity} ช่อง'
    else:
        message = f'ไม่พร้อม: ใช้ครบ {current_count}/{max_capacity} ช่อง'
    
    return {
        'available': available,
        'current_count': current_count,
        'max_capacity': max_capacity,
        'source': source,
        'message': message,
    }


# ============================================================
# BACKWARD SCHEDULING
# ============================================================

def calculate_thaw_schedule(target_ready_at, thaw_profile, package=None,
                             queue_buffer_minutes=30, transition_buffer_minutes=15):
    """
    Calculate thaw schedule backwards from target ready time.
    
    Timeline:
        target_ready_at
            ↑ (thaw_duration before)
        thaw_start_at
            ↑ (queue_buffer_minutes before)
        thaw_queue_at
    
    Args:
        target_ready_at: datetime — when product must be ready
        thaw_profile: ThawProfile — configuration to use
        package: Package (optional, for weight-based duration)
        queue_buffer_minutes: int — minutes before thaw_start to queue
        transition_buffer_minutes: int — minutes between freeze_end and thaw_start
    
    Returns:
        dict: {
            'target_ready_at': datetime,
            'thaw_start_at': datetime,
            'thaw_queue_at': datetime,
            'thaw_duration': timedelta,
            'total_preparation_time': timedelta,
            'is_override': bool,
            'duration_source': str,
        }
    """
    # Calculate duration
    if package:
        eff = get_effective_thaw_duration(package, thaw_profile)
        thaw_duration = eff['duration']
        is_override = eff['is_override']
        duration_source = eff['source']
    else:
        # Use profile default when no package specified
        thaw_duration = thaw_profile.default_duration + thaw_profile.buffer_duration
        is_override = False
        duration_source = 'profile'
    
    # Backward calculation
    thaw_start_at = target_ready_at - thaw_duration
    thaw_queue_at = thaw_start_at - timedelta(minutes=queue_buffer_minutes)
    
    total_preparation = target_ready_at - thaw_queue_at
    
    return {
        'target_ready_at': target_ready_at,
        'thaw_start_at': thaw_start_at,
        'thaw_queue_at': thaw_queue_at,
        'thaw_duration': thaw_duration,
        'total_preparation_time': total_preparation,
        'is_override': is_override,
        'duration_source': duration_source,
    }


# ============================================================
# PROFILE MATCHING
# ============================================================

def get_best_thaw_profile(product=None, package=None):
    """
    Find the best thaw profile for a product/package.
    
    Priority:
    1. Category-specific profile (matches product.category)
    2. 'All Categories' profile (category='')
    3. Any active profile (fallback)
    
    Args:
        product: Product instance (optional)
        package: Package instance (optional, uses package.product)
    
    Returns:
        ThawProfile instance or None
    """
    from planning.models import ThawProfile
    
    if package and not product:
        product = package.product
    
    if product:
        # Try category-specific first
        category_profile = ThawProfile.objects.filter(
            category=product.category,
            active=True,
        ).first()
        
        if category_profile:
            return category_profile
    
    # Try 'all categories' profile
    all_profile = ThawProfile.objects.filter(
        category='',
        active=True,
    ).first()
    
    if all_profile:
        return all_profile
    
    # Fallback to any active profile
    return ThawProfile.objects.filter(active=True).first()


def get_available_profiles(product=None):
    """
    Get thaw profiles applicable to a product.
    
    Returns profiles that match the product's category OR have no category (all).
    
    Args:
        product: Product instance (optional)
    
    Returns:
        QuerySet of ThawProfile
    """
    from planning.models import ThawProfile
    
    if product:
        return ThawProfile.objects.filter(
            Q(category=product.category) | Q(category=''),
            active=True,
        ).order_by('name')
    
    return ThawProfile.objects.filter(active=True).order_by('name')
