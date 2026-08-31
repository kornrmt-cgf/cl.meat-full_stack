"""
Inventory Services — Single Source of Truth for Stock Operations.

All stock mutations go through service functions here.
Views/API call services, never modify models directly.

══════════════════════════════════════════════════════════════
STOCK RULES (Phase 03 Authoritative)
══════════════════════════════════════════════════════════════

Available Stock (single source of truth):
    available_stock = SUM(package.weight)
        WHERE package.current_state IN (
            PACKED, FREEZING, FROZEN, READY_FOR_THAW,
            THAW_QUEUED, THAWING, READY_FOR_SALE, ON_DISPLAY
        )

    Dashboard, API, reports, and Loyverse integration all use
    get_available_stock(). No other calculation is authoritative.

    StockMovement is the audit trail, NOT the numeric source
    of truth. Stock totals are computed from Package.weight,
    not from summing StockMovement records.

══════════════════════════════════════════════════════════════
AUDIT SEMANTICS
══════════════════════════════════════════════════════════════

StockMovement: physical stock operations (location, weight, lifecycle)
    - RECEIVE: package created from batch
    - PACK:    package sealed for storage
    - MOVE:    package moved between locations
    - ADJUST:  weight correction after re-weighing
    - SOLD:    package sold to customer
    - DISCARDED: package discarded (damaged/expired)

PriceChangeHistory: price-only changes (no StockMovement created)
    - Manual price override
    - Auto price recalculation
    - Cost margin / discount adjustments

══════════════════════════════════════════════════════════════
STATE SEMANTICS (Phase 03)
══════════════════════════════════════════════════════════════

Lifecycle: PACKED → FREEZING → FROZEN → READY_FOR_THAW →
           THAW_QUEUED → THAWING → READY_FOR_SALE → ON_DISPLAY

Sale: ON_DISPLAY → PROCESSING → COMPLETED
    SALE/DISCARD semantics are represented by StockMovement
    records (SOLD/DISCARDED movement_type), not by a dedicated
    SOLD state. COMPLETED is the terminal state for both sale
    and discard.

Discard: ON_DISPLAY → DISCARDED → COMPLETED

══════════════════════════════════════════════════════════════
CONCURRENCY
══════════════════════════════════════════════════════════════

- Package mutations use select_for_update() for row-level locking
- BarcodeSequence uses select_for_update() for atomic increment
- Price changes use transaction.atomic + row lock
- All service functions use @transaction.atomic
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from inventory.models import (
    Category, Supplier, Product, Batch, Package,
    PackageState, StorageLocation, StockMovement,
    BarcodeSequence,
)
from common.state_machine import (
    transition_package, can_transition,
    InvalidTransitionError, TransitionValidationError,
)


# ============================================================
# VALIDATION EXCEPTIONS
# ============================================================

class InventoryError(Exception):
    """Base exception for inventory operations."""
    pass


class WeightError(InventoryError, ValueError):
    """Invalid weight operation."""
    pass


class StockError(InventoryError):
    """Invalid stock operation."""
    pass


class ConcurrencyError(InventoryError):
    """Concurrent modification detected."""
    pass


# ============================================================
# WEIGHT RULES
# ============================================================

_MIN_PACKAGE_WEIGHT = Decimal('0.001')
_MAX_PACKAGE_WEIGHT = Decimal('999.999')


def validate_weight(weight):
    """Validate weight is a valid Decimal within allowed range.

    Args:
        weight: value to validate

    Returns:
        Decimal: validated weight

    Raises:
        WeightError: if weight is invalid
    """
    try:
        w = Decimal(str(weight))
    except (InvalidOperation, TypeError, ValueError) as e:
        raise WeightError(f'Invalid weight: {weight!r} — {e}')

    if w < _MIN_PACKAGE_WEIGHT:
        raise WeightError(
            f'Weight {w} kg is below minimum {_MIN_PACKAGE_WEIGHT} kg')

    if w > _MAX_PACKAGE_WEIGHT:
        raise WeightError(
            f'Weight {w} kg exceeds maximum {_MAX_PACKAGE_WEIGHT} kg')

    return w


def validate_price(price):
    """Validate price is a valid non-negative Decimal.

    Args:
        price: value to validate

    Returns:
        Decimal: validated price

    Raises:
        StockError: if price is invalid
    """
    try:
        p = Decimal(str(price))
    except (InvalidOperation, TypeError, ValueError) as e:
        raise StockError(f'Invalid price: {price!r} — {e}')

    if p < 0:
        raise StockError(f'Price {p} cannot be negative')

    return p


# ============================================================
# PACKAGE QUERIES (Single Source of Truth)
# ============================================================

def get_available_stock(product=None, location=None):
    """Calculate available stock weight.

    Available = sum of weight for packages in active (non-terminal) states.

    This is THE authoritative stock calculation.
    Dashboard, API, reports all use this.

    Args:
        product: optional Product to filter by
        location: optional StorageLocation to filter by

    Returns:
        Decimal: total available weight in kg
    """
    from django.db.models import Sum, Q

    active_states = [
        PackageState.PACKED, PackageState.FREEZING,
        PackageState.FROZEN, PackageState.READY_FOR_THAW,
        PackageState.THAW_QUEUED, PackageState.THAWING,
        PackageState.READY_FOR_SALE, PackageState.ON_DISPLAY,
    ]

    qs = Package.objects.filter(current_state__in=active_states)

    if product:
        qs = qs.filter(product=product)
    if location:
        qs = qs.filter(storage_location=location)

    result = qs.aggregate(total=Sum('weight'))
    return result['total'] or Decimal('0')


def get_package_stock_summary(product=None):
    """Get stock breakdown by state for a product.

    Returns:
        dict: {state: weight_kg, ...} with 'total' key
    """
    from django.db.models import Sum

    qs = Package.objects.all()
    if product:
        qs = qs.filter(product=product)

    by_state = {}
    for state_choice in PackageState:
        total = qs.filter(
            current_state=state_choice
        ).aggregate(total=Sum('weight'))['total'] or Decimal('0')
        by_state[state_choice] = total

    by_state['total'] = sum(by_state.values(), Decimal('0'))
    return by_state


# ============================================================
# STOCK MOVEMENT AUDIT TRAIL
# ============================================================

def _record_movement(package, movement_type, weight_at_movement=None,
                     from_location=None, to_location=None,
                     actor='', reason='', metadata=None):
    """Record a stock movement audit entry.

    Every inventory mutation MUST call this.

    Args:
        package: Package instance
        movement_type: one of StockMovement.MOVEMENT_TYPE_CHOICES
        weight_at_movement: weight at time of movement (defaults to package.weight)
        from_location: source StorageLocation (or None)
        to_location: destination StorageLocation (or None)
        actor: who performed the action
        reason: why
        metadata: additional context dict

    Returns:
        StockMovement: created movement record
    """
    if weight_at_movement is None:
        weight_at_movement = package.weight

    return StockMovement.objects.create(
        package=package,
        movement_type=movement_type,
        from_location=from_location,
        to_location=to_location,
        weight_at_movement=weight_at_movement,
        actor=actor or 'system',
        reason=reason or '',
        metadata=metadata or {},
    )


# ============================================================
# CORE OPERATIONS
# ============================================================

@transaction.atomic
def create_package(product, batch, *args, **kwargs):
    """Create a new package from a batch.

    This is the ENTRY POINT for all package creation.

    Supports two calling conventions:

    New (explicit):
        create_package(product, batch, barcode='...', weight=1.0,
                       selling_price=150, ...)

    Legacy (positional, backward-compatible):
        create_package(product, batch, weight, storage_location=None)
        → auto-generates barcode and calculates price.

    Args:
        product: Product instance
        batch: Batch instance
        barcode: unique barcode string (optional, auto-generated if not given)
        weight: weight in kg (Decimal-safe)
        selling_price: selling price in THB (optional, auto-calculated if not given)
        packed_at: datetime when packed (default: now)
        storage_location: optional StorageLocation
        loyalty_sku: optional Loyverse SKU
        actor: who created the package
        reason: why

    Returns:
        Package: created package instance

    Raises:
        WeightError: if weight is invalid
        StockError: if barcode already exists or other validation fails
    """
    # --- detect legacy positional call: create_package(product, batch, weight) ---
    # Also supports: create_package(product, batch, weight, storage_location)
    if len(args) >= 1 and 'barcode' not in kwargs:
        # Legacy: third positional arg is weight
        weight_arg = args[0]
        # Check if 4th positional arg is a StorageLocation
        if len(args) >= 2 and hasattr(args[1], 'pk'):
            storage_location = args[1]
        else:
            storage_location = kwargs.pop('storage_location', None)
        barcode = generate_barcode(product, batch)
        weight = validate_weight(weight_arg)
        selling_price = calculate_package_price(product, weight)
    else:
        barcode = kwargs.pop('barcode', None)
        weight_arg = kwargs.pop('weight', None)
        if weight_arg is None and args:
            weight_arg = args[0]
        weight = validate_weight(weight_arg)
        selling_price_val = kwargs.pop('selling_price', None)
        if selling_price_val is not None:
            selling_price = validate_price(selling_price_val)
        else:
            selling_price = calculate_package_price(product, weight)
        storage_location = kwargs.pop('storage_location', None)
        if barcode is None:
            barcode = generate_barcode(product, batch)

    packed_at = kwargs.pop('packed_at', None)
    loyalty_sku = kwargs.pop('loyalty_sku', None)
    actor = kwargs.pop('actor', 'system')
    reason = kwargs.pop('reason', '')

    # Check barcode uniqueness
    if Package.objects.filter(barcode=barcode).exists():
        raise StockError(f'Barcode already exists: {barcode}')

    # Check loyalty SKU uniqueness
    if loyalty_sku and Package.objects.filter(loyverse_sku=loyalty_sku).exists():
        raise StockError(f'Loyalty SKU already exists: {loyalty_sku}')

    if packed_at is None:
        packed_at = timezone.now()

    package = Package.objects.create(
        product=product,
        batch=batch,
        barcode=barcode,
        weight=weight,
        selling_price=selling_price,
        packed_at=packed_at,
        current_state=PackageState.PACKED,
        storage_location=storage_location,
        loyverse_sku=loyalty_sku,
    )

    # Audit trail
    _record_movement(
        package, 'RECEIVED',
        weight_at_movement=weight,
        to_location=storage_location,
        actor=actor,
        reason=reason or f'Package created: {barcode}',
        metadata={
            'product_sku': product.sku,
            'batch_number': batch.batch_number,
            'weight_kg': str(weight),
            'selling_price': str(selling_price),
        },
    )

    return package


@transaction.atomic
def move_package(package, to_location, actor='system', reason=''):
    """Move a package to a different storage location.

    Creates a StockMovement audit record.

    Args:
        package: Package instance (with select_for_update for concurrency)
        to_location: destination StorageLocation
        actor: who performed the move
        reason: why

    Returns:
        Package: updated package

    Raises:
        StockError: if package is in terminal state
    """
    # Lock the package row for update (concurrency safety)
    package = Package.objects.select_for_update().get(pk=package.pk)

    if package.current_state in (PackageState.COMPLETED, PackageState.DISCARDED):
        raise StockError(
            f'Cannot move package in terminal state: {package.current_state}')

    from_location = package.storage_location
    package.storage_location = to_location
    package.save(update_fields=['storage_location', 'updated_at'])

    _record_movement(
        package, 'MOVED',
        from_location=from_location,
        to_location=to_location,
        actor=actor,
        reason=reason or f'Moved to {to_location}',
        metadata={
            'from_location': str(from_location) if from_location else None,
            'to_location': str(to_location),
        },
    )

    return package


@transaction.atomic
def adjust_weight(package, new_weight, actor='system', reason=''):
    """Adjust a package's weight (e.g., after re-weighing).

    Creates a StockMovement audit record with before/after.

    Args:
        package: Package instance
        new_weight: corrected weight in kg
        actor: who performed the adjustment
        reason: why the adjustment was needed

    Returns:
        Package: updated package

    Raises:
        WeightError: if new_weight is invalid
        StockError: if package is in terminal state
    """
    new_weight = validate_weight(new_weight)
    package = Package.objects.select_for_update().get(pk=package.pk)

    if package.current_state in (PackageState.COMPLETED, PackageState.DISCARDED):
        raise StockError(
            f'Cannot adjust weight of package in terminal state: '
            f'{package.current_state}')

    old_weight = package.weight
    if old_weight == new_weight:
        return package  # no change needed

    package.weight = new_weight
    package.save(update_fields=['weight', 'updated_at'])

    _record_movement(
        package, 'ADJUSTED',
        weight_at_movement=new_weight,
        actor=actor,
        reason=reason or f'Weight adjusted: {old_weight} → {new_weight}',
        metadata={
            'old_weight_kg': str(old_weight),
            'new_weight_kg': str(new_weight),
            'difference_kg': str(new_weight - old_weight),
        },
    )

    return package


@transaction.atomic
def sell_package(package, actor='system', reason=''):
    """Mark a package as sold.

    Transitions: READY_FOR_SALE → PROCESSING → COMPLETED
    or ON_DISPLAY → PROCESSING → COMPLETED

    Uses the centralized state machine for all transitions.

    Args:
        package: Package instance
        actor: who performed the sale
        reason: why

    Returns:
        Package: updated package

    Raises:
        StockError: if package cannot be sold from current state
    """
    package = Package.objects.select_for_update().get(pk=package.pk)

    # Package must be in a sellable state
    sellable_states = {
        PackageState.READY_FOR_SALE,
        PackageState.ON_DISPLAY,
    }
    if package.current_state not in sellable_states:
        raise StockError(
            f'Cannot sell package in state {package.current_state}. '
            f'Must be in {sellable_states}')

    # Transition through PROCESSING → COMPLETED via state machine
    transition_package(
        package, PackageState.PROCESSING,
        actor=actor,
        reason=reason or 'Sold — entering processing',
    )
    transition_package(
        package, PackageState.COMPLETED,
        actor=actor,
        reason=reason or 'Sold',
    )

    _record_movement(
        package, 'SOLD',
        weight_at_movement=package.weight,
        actor=actor,
        reason=reason or 'Package sold',
        metadata={
            'selling_price': str(package.selling_price),
            'weight_kg': str(package.weight),
        },
    )

    return package


@transaction.atomic
def discard_package(package, actor='system', reason=''):
    """Mark a package as discarded (damaged, expired, etc.).

    Uses the centralized state machine. The package must be in a state
    that can transition to DISCARDED (currently ON_DISPLAY).

    For packages in other states, the caller should first transition
    the package through the normal lifecycle to ON_DISPLAY.

    Args:
        package: Package instance
        actor: who performed the discard
        reason: why

    Returns:
        Package: updated package

    Raises:
        StockError: if package cannot be discarded from current state
    """
    package = Package.objects.select_for_update().get(pk=package.pk)

    # Terminal states cannot be discarded
    if package.current_state in (PackageState.COMPLETED, PackageState.DISCARDED):
        raise StockError(
            f'Package already in terminal state: {package.current_state}')

    # State machine allows discard only from ON_DISPLAY
    discardable_states = {PackageState.ON_DISPLAY}
    if package.current_state not in discardable_states:
        raise StockError(
            f'Cannot discard package in state {package.current_state}. '
            f'Must be in {discardable_states}. '
            f'Use transition_package() to move to ON_DISPLAY first.')

    # Transition through DISCARDED → COMPLETED via state machine
    transition_package(
        package, PackageState.DISCARDED,
        actor=actor,
        reason=reason or 'Discarded',
    )
    transition_package(
        package, PackageState.COMPLETED,
        actor=actor,
        reason=reason or 'Discarded — finalized',
    )

    _record_movement(
        package, 'DISCARDED',
        weight_at_movement=package.weight,
        actor=actor,
        reason=reason or 'Package discarded',
        metadata={
            'weight_kg': str(package.weight),
        },
    )

    return package


@transaction.atomic
def receive_stock(product, supplier, batch_number, packages_data,
                  received_at=None, actor='system', reason=''):
    """Receive a shipment and create batch + packages in one operation.

    This is the high-level entry point for receiving new stock.

    Args:
        product: Product instance
        supplier: Supplier instance
        batch_number: unique batch number string
        packages_data: list of dicts with keys:
            - barcode: str
            - weight: Decimal/str/int
            - selling_price: Decimal/str/int (optional, default 0)
            - storage_location: StorageLocation (optional)
            - loyalty_sku: str (optional)
        received_at: datetime (default: now)
        actor: who received the stock
        reason: why

    Returns:
        dict: {
            'batch': Batch,
            'packages': [Package, ...],
            'total_weight': Decimal,
        }

    Raises:
        StockError: if batch number already exists
        WeightError: if any package weight is invalid
    """
    # Check batch number uniqueness
    if Batch.objects.filter(batch_number=batch_number).exists():
        raise StockError(f'Batch number already exists: {batch_number}')

    if received_at is None:
        received_at = timezone.now()

    batch = Batch.objects.create(
        batch_number=batch_number,
        supplier=supplier,
        received_at=received_at,
        notes=reason or '',
    )

    created_packages = []
    total_weight = Decimal('0')

    for pkg_data in packages_data:
        pkg = create_package(
            product=product,
            batch=batch,
            barcode=pkg_data['barcode'],
            weight=pkg_data['weight'],
            selling_price=pkg_data.get('selling_price', 0),
            storage_location=pkg_data.get('storage_location'),
            loyalty_sku=pkg_data.get('loyalty_sku'),
            actor=actor,
            reason=f'Received in batch {batch_number}',
        )
        created_packages.append(pkg)
        total_weight += pkg.weight

    return {
        'batch': batch,
        'packages': created_packages,
        'total_weight': total_weight,
    }


# ============================================================
# CONCURRENCY-SAFE QUERIES
# ============================================================

def get_package_for_update(package_id):
    """Get a package with row-level lock for update.

    Use this when the package will be modified to prevent
    lost updates from concurrent operations.

    Args:
        package_id: Package PK

    Returns:
        Package: locked package instance

    Raises:
        Package.DoesNotExist: if package not found
    """
    return Package.objects.select_for_update().get(pk=package_id)


# ============================================================
# STOCK CONSISTENCY CHECK
# ============================================================

def verify_stock_consistency(product=None):
    """Verify that stock calculations are consistent.

    Checks:
    1. No negative weight packages exist
    2. All active packages have valid states
    3. StockMovement records exist for every active package

    Args:
        product: optional Product to check

    Returns:
        dict: {consistent: bool, issues: [...]}
    """
    issues = []
    qs = Package.objects.all()
    if product:
        qs = qs.filter(product=product)

    # Check 1: No negative weight
    negative = qs.filter(weight__lte=0)
    for pkg in negative:
        issues.append({
            'type': 'NEGATIVE_WEIGHT',
            'package_id': pkg.id,
            'barcode': pkg.barcode,
            'weight': str(pkg.weight),
        })

    # Check 2: All active packages have valid states
    valid_states = {choice[0] for choice in PackageState.choices}
    for pkg in qs:
        if pkg.current_state not in valid_states:
            issues.append({
                'type': 'INVALID_STATE',
                'package_id': pkg.id,
                'barcode': pkg.barcode,
                'state': pkg.current_state,
            })

    # Check 3: Every active package has at least one RECEIVED movement
    active_states = [
        PackageState.PACKED, PackageState.FREEZING,
        PackageState.FROZEN, PackageState.READY_FOR_THAW,
        PackageState.THAW_QUEUED, PackageState.THAWING,
        PackageState.READY_FOR_SALE, PackageState.ON_DISPLAY,
    ]
    active_packages = qs.filter(current_state__in=active_states)
    for pkg in active_packages:
        if not StockMovement.objects.filter(
                package=pkg, movement_type='RECEIVED').exists():
            issues.append({
                'type': 'MISSING_RECEIVED_MOVEMENT',
                'package_id': pkg.id,
                'barcode': pkg.barcode,
                'state': pkg.current_state,
            })

    return {
        'consistent': len(issues) == 0,
        'issues': issues,
        'checked_count': qs.count(),
    }


# ============================================================
# BACKWARD-COMPATIBLE QUERY HELPERS
# Used by existing test suites and views.
# ============================================================

def get_packages_by_state(state):
    """Get all packages in a given state.

    Args:
        state: PackageState value

    Returns:
        QuerySet of Package objects
    """
    return Package.objects.filter(current_state=state)


def get_available_for_planning():
    """Get packages available for rotation planning.

    These are packages in FROZEN state that have no active rotation plan.

    Returns:
        QuerySet of Package objects
    """
    from planning.models import RotationPlan
    frozen_pks = Package.objects.filter(
        current_state=PackageState.FROZEN
    ).exclude(
        rotation_plan__status__in=['PLANNED', 'IN_PROGRESS']
    ).values_list('pk', flat=True)
    return Package.objects.filter(pk__in=frozen_pks)


def get_package_by_barcode(barcode):
    """Get a package by its barcode.

    Args:
        barcode: barcode string

    Returns:
        Package instance or None
    """
    try:
        return Package.objects.get(barcode=barcode)
    except Package.DoesNotExist:
        return None


# ============================================================
# BACKWARD-COMPATIBLE CREATION HELPERS
# Used by existing test suites.
# ============================================================

@transaction.atomic
def generate_barcode(product, batch):
    """Generate a unique barcode for a package.

    Uses the product's barcode_prefix + batch number + sequence.

    CONCURRENCY SAFETY: The BarcodeSequence row is locked with
    select_for_update() inside an atomic transaction, so two
    concurrent calls cannot produce the same sequence number.

    Package.barcode UNIQUE constraint is the final safety net.

    Args:
        product: Product instance
        batch: Batch instance

    Returns:
        str: generated barcode

    Raises:
        ValueError: if product or batch is None
    """
    if product is None:
        raise ValueError('product is required for barcode generation')
    if batch is None:
        raise ValueError('batch is required for barcode generation')

    prefix = product.barcode_prefix or '0000'
    batch_num = batch.batch_number[-4:] if len(batch.batch_number) >= 4 else batch.batch_number

    # Atomic sequence: lock row → increment → save, all inside @transaction.atomic
    seq, _ = BarcodeSequence.objects.select_for_update().get_or_create(
        product=product,
        batch_number=batch.batch_number,
        supplier_id=batch.supplier_id,
        defaults={'last_sequence': 0},
    )
    seq.last_sequence += 1
    seq.save(update_fields=['last_sequence'])

    return f'{prefix}{batch_num}{seq.last_sequence:04d}'


def calculate_package_price(product, weight, mode='auto', value=None):
    """Calculate package price based on product pricing and weight.

    PRICING RULE: Always returns Decimal. Calculation uses Decimal
    throughout; ceiling is applied via (x + 0.999...) truncated to
    integer THB. Rounding rule: ceiling to nearest whole THB.

    Modes:
    - auto: selling_price_per_kg * weight (ceiling to whole THB)
    - price_per_kg: value * weight
    - cost_margin: cost_per_kg * (1 + value/100) * weight
    - discount: selling_price_per_kg * (1 - value/100) * weight

    Args:
        product: Product instance
        weight: weight in kg (Decimal-safe)
        mode: calculation mode
        value: mode-specific parameter

    Returns:
        Decimal: calculated price in THB (always Decimal, never int)

    Raises:
        ValueError: if mode is invalid
    """
    weight = Decimal(str(weight))
    if weight <= 0:
        return Decimal('0')

    raw = Decimal('0')
    if mode == 'auto':
        raw = product.selling_price_per_kg * weight
    elif mode == 'price_per_kg':
        raw = Decimal(str(value)) * weight
    elif mode == 'cost_margin':
        margin = Decimal(str(value)) / 100
        raw = product.cost_per_kg * (1 + margin) * weight
    elif mode == 'discount':
        discount = Decimal(str(value)) / 100
        raw = product.selling_price_per_kg * (1 - discount) * weight
    else:
        raise ValueError(f'Invalid price mode: {mode}')

    # Ceiling to nearest whole THB using pure Decimal arithmetic
    return raw.quantize(Decimal('1'), rounding=ROUND_CEILING)


@transaction.atomic
def adjust_package_price(package, new_price, mode='manual', actor='', value=None):
    """Adjust a package's selling price with atomic audit trail.

    ATOMICITY: Package row is locked with select_for_update(),
    then both Package.selling_price and PriceChangeHistory are
    updated inside one transaction. On any failure, both are
    rolled back.

    AUDIT: Price changes go to PriceChangeHistory, NOT
    StockMovement. StockMovement is for physical stock operations.

    Args:
        package: Package instance
        new_price: new price in THB
        mode: adjustment mode (manual, auto, price_per_kg, cost_margin, discount)
        actor: who performed the adjustment
        value: mode-specific value for calculation

    Returns:
        Package: updated package

    Raises:
        StockError: if price is invalid
    """
    from inventory.models import PriceChangeHistory
    new_price = validate_price(new_price)

    # Lock package row for atomic update
    package = Package.objects.select_for_update().get(pk=package.pk)
    old_price = package.selling_price

    package.selling_price = new_price
    package.save(update_fields=['selling_price', 'updated_at'])

    PriceChangeHistory.objects.create(
        package=package,
        old_price=old_price,
        new_price=new_price,
        mode=mode,
        value=Decimal(str(value)) if value else Decimal('0'),
        actor=actor,
    )

    return package


def create_product(sku, name, category, supplier=None, **kwargs):
    """Create a new product.

    Args:
        sku: unique SKU string
        name: product name
        category: Category instance
        supplier: optional Supplier instance
        **kwargs: additional Product fields

    Returns:
        Product: created product
    """
    return Product.objects.create(
        sku=sku, name=name, category=category,
        supplier=supplier, **kwargs,
    )


def create_batch(batch_number, supplier, received_at=None, **kwargs):
    """Create a new batch.

    Args:
        batch_number: unique batch number
        supplier: Supplier instance
        received_at: datetime (default: now)
        **kwargs: additional Batch fields

    Returns:
        Batch: created batch
    """
    if received_at is None:
        received_at = timezone.now()
    return Batch.objects.create(
        batch_number=batch_number, supplier=supplier,
        received_at=received_at, **kwargs,
    )


def create_storage_location(name, location_type='FREEZER', capacity=50, **kwargs):
    """Create a new storage location.

    Args:
        name: location name
        location_type: one of StorageLocation.LOCATION_TYPE_CHOICES
        capacity: maximum package count
        **kwargs: additional StorageLocation fields

    Returns:
        StorageLocation: created location
    """
    return StorageLocation.objects.create(
        name=name, location_type=location_type,
        capacity=capacity, **kwargs,
    )

