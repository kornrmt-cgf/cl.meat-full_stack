"""
Inventory Services — CRUD, stock movement, barcode generation, and lifecycle helpers.

All inventory operations go through these services for consistency and traceability.
"""
from decimal import Decimal
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
import math

from inventory.models import (
    Product, Batch, Package, StorageLocation, StockMovement,
    BarcodeSequence, PackageState
)


# ============================================================
# PRODUCT / BATCH / LOCATION CRUD
# ============================================================

def create_product(sku, name, category, supplier=None, unit='KG', **kwargs):
    if Product.objects.filter(sku=sku).exists():
        raise ValueError(f"Product with SKU '{sku}' already exists")
    return Product.objects.create(sku=sku, name=name, category=category,
                                  supplier=supplier, unit=unit, **kwargs)


def create_batch(batch_number, supplier, received_at=None, notes=''):
    if Batch.objects.filter(batch_number=batch_number).exists():
        raise ValueError(f"Batch '{batch_number}' already exists")
    if received_at is None:
        received_at = timezone.now()
    return Batch.objects.create(batch_number=batch_number, supplier=supplier,
                                received_at=received_at, notes=notes)


def create_storage_location(name, location_type, capacity=50, thaw_capacity=20):
    return StorageLocation.objects.create(
        name=name, location_type=location_type,
        capacity=capacity, thaw_capacity=thaw_capacity
    )


# ============================================================
# BARCODE GENERATION
# ============================================================

def generate_barcode(product, batch):
    """
    Generate the next barcode for a product in a given batch.

    Format: {supplier_id}{batch_number}{product_barcode_prefix}{sequence:02d}
    Uses atomic transaction + select_for_update to prevent race conditions.
    """
    if not product:
        raise ValueError("Product is required")
    if not batch:
        raise ValueError("Batch is required")

    supplier_id = _get_supplier_id(batch)
    batch_number = str(batch.batch_number) if batch.batch_number else '0'
    prefix = str(product.barcode_prefix) if product.barcode_prefix else '0000'

    with transaction.atomic():
        seq_obj, _ = BarcodeSequence.objects.select_for_update().get_or_create(
            product=product, batch_number=batch_number, supplier_id=supplier_id,
            defaults={'last_sequence': 0}
        )
        seq_obj.last_sequence += 1
        seq_obj.save(update_fields=['last_sequence', 'updated_at'])

        barcode = f"{supplier_id}{batch_number}{prefix}{seq_obj.last_sequence:02d}"

        # Belt and suspenders: ensure uniqueness
        while Package.objects.filter(barcode=barcode).exists():
            seq_obj.last_sequence += 1
            seq_obj.save(update_fields=['last_sequence', 'updated_at'])
            barcode = f"{supplier_id}{batch_number}{prefix}{seq_obj.last_sequence:02d}"

    return barcode


def _get_supplier_id(batch):
    supplier = str(batch.supplier) if batch.supplier else '0'
    try:
        return int(supplier)
    except (ValueError, TypeError):
        return abs(hash(supplier)) % 100


# ============================================================
# PACKAGE CREATION
# ============================================================

@transaction.atomic
def create_package(product, batch, weight_kg, packed_at=None,
                   storage_location=None, selling_price=None, barcode=None):
    """
    Create a new package with auto-generated barcode and price calculation.

    Args:
        product: Product instance
        batch: Batch instance
        weight_kg: Weight in kilograms
        packed_at: When packed (default=now)
        storage_location: Optional location
        selling_price: Optional override (auto-calculated if None)
        barcode: Optional override (auto-generated if None)

    Returns:
        Package instance
    """
    if weight_kg <= 0:
        raise ValueError("Weight must be positive")

    if packed_at is None:
        packed_at = timezone.now()

    # Auto-generate barcode
    if not barcode:
        barcode = generate_barcode(product, batch)

    # Auto-calculate selling price
    if selling_price is None:
        selling_price = calculate_package_price(product, weight_kg, mode='auto')

    package = Package.objects.create(
        product=product, batch=batch, barcode=barcode,
        weight=Decimal(str(weight_kg)),
        selling_price=Decimal(str(selling_price)),
        packed_at=packed_at, current_state=PackageState.PACKED,
        storage_location=storage_location,
    )

    # Record stock movement
    StockMovement.objects.create(
        package=package, movement_type='RECEIVED',
        to_location=storage_location,
        weight_at_movement=package.weight,
        actor='system', reason='Package created',
    )

    return package


# ============================================================
# PACKAGE PRICE CALCULATION
# ============================================================

def calculate_package_price(product, weight_kg, mode='auto', value=None):
    """
    Calculate package selling price.

    Modes:
        auto/price_per_kg: selling_price_per_kg × weight_kg
        cost_margin: cost_per_kg × weight_kg × (1 + margin%/100)
        discount: current_price × (1 - discount%/100)

    Returns: int (rounded up with math.ceil, matching legacy behavior)
    """
    if weight_kg <= 0:
        return 0

    if mode in ('auto', 'price_per_kg'):
        price_per_kg = float(value) if value is not None else float(product.selling_price_per_kg)
        return max(0, math.ceil(price_per_kg * weight_kg))
    elif mode == 'cost_margin':
        cost = float(product.cost_per_kg)
        margin = float(value or 0)
        return max(0, math.ceil(cost * weight_kg * (1 + margin / 100)))
    elif mode == 'discount':
        base_price = float(product.selling_price_per_kg) * weight_kg
        discount = float(value or 0)
        return max(0, math.ceil(base_price * (1 - discount / 100)))
    else:
        raise ValueError(f"Unknown pricing mode: {mode}")


# ============================================================
# STOCK MOVEMENT
# ============================================================

@transaction.atomic
def move_package(package, to_location, actor='', reason=''):
    """
    Move a package to a different storage location.
    Records a StockMovement audit trail.
    """
    from_location = package.storage_location
    package.storage_location = to_location
    package.save(update_fields=['storage_location', 'updated_at'])

    StockMovement.objects.create(
        package=package, movement_type='MOVED',
        from_location=from_location, to_location=to_location,
        weight_at_movement=package.weight,
        actor=actor, reason=reason,
    )
    return package


@transaction.atomic
def adjust_package_price(package, new_price, mode='manual', value=Decimal('0'), actor=''):
    """Adjust package price with audit trail."""
    from inventory.models import PriceChangeHistory
    old_price = package.selling_price
    package.selling_price = Decimal(str(new_price))
    package.save(update_fields=['selling_price', 'updated_at'])

    PriceChangeHistory.objects.create(
        package=package, old_price=old_price, new_price=Decimal(str(new_price)),
        mode=mode, value=Decimal(str(value)), actor=actor,
    )
    return package


# ============================================================
# QUERIES
# ============================================================

def get_packages_by_state(state, product=None):
    qs = Package.objects.filter(current_state=state).select_related('product', 'batch', 'storage_location')
    if product:
        qs = qs.filter(product=product)
    return qs


def get_available_for_planning(product=None):
    """Get FROZEN packages available for rotation planning."""
    return get_packages_by_state(PackageState.FROZEN, product)


def get_package_by_barcode(barcode):
    return Package.objects.select_related('product', 'batch', 'storage_location').filter(barcode=barcode).first()
