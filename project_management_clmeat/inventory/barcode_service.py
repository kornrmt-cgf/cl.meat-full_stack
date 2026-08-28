"""
Barcode Service — the single authoritative source for barcode generation.

Barcode Format (CL.MEAT legacy):
    {supplier_id}{batch_number}{product_barcode_prefix}{sequence:02d}

Example:
    supplier_id = 2
    batch_number = 18
    prefix = 0051
    sequence = 03
    
    barcode = "218005103"

The sequence is per-product per-batch per-supplier, using MAX(sequence) + 1
to prevent duplicates even after deletions.
"""
from django.db import transaction
from django.db.models import Max
import math

from inventory.models import (
    Product, Batch, Package, BarcodeSequence
)


# ============================================================
# PUBLIC API
# ============================================================

def generate_barcode(product, batch):
    """
    Generate the next barcode for a product in a given batch.
    
    Uses atomic transaction + select_for_update to prevent race conditions.
    Returns the generated barcode string.
    
    Args:
        product: Product instance (must have barcode_prefix)
        batch: Batch instance (must have batch_number, supplier info)
    
    Returns:
        str: The generated barcode (e.g., "218005103")
    
    Raises:
        ValueError: If product or batch is missing required data
    """
    if not product:
        raise ValueError("Product is required")
    if not batch:
        raise ValueError("Batch is required")
    
    supplier_id = _get_supplier_id(batch)
    batch_number = _get_batch_number(batch)
    prefix = _get_product_prefix(product)
    
    with transaction.atomic():
        # Get or create sequence tracker, locked for update
        seq_obj, created = BarcodeSequence.objects.select_for_update().get_or_create(
            product=product,
            batch_number=batch_number,
            supplier_id=supplier_id,
            defaults={'last_sequence': 0}
        )
        
        # Increment sequence
        seq_obj.last_sequence += 1
        seq_obj.save(update_fields=['last_sequence', 'updated_at'])
        
        # Build barcode
        barcode = _build_barcode(supplier_id, batch_number, prefix, seq_obj.last_sequence)
        
        # Verify uniqueness (belt and suspenders)
        while Package.objects.filter(barcode=barcode).exists():
            seq_obj.last_sequence += 1
            seq_obj.save(update_fields=['last_sequence', 'updated_at'])
            barcode = _build_barcode(supplier_id, batch_number, prefix, seq_obj.last_sequence)
    
    return barcode


def generate_preview_barcode(product, batch):
    """
    Preview what the next barcode would be WITHOUT creating it.
    Used for UI preview before actual package creation.
    
    Returns:
        str: Preview barcode string
    """
    supplier_id = _get_supplier_id(batch)
    batch_number = _get_batch_number(batch)
    prefix = _get_product_prefix(product)
    
    # Find max sequence without locking
    last_seq = BarcodeSequence.objects.filter(
        product=product,
        batch_number=batch_number,
        supplier_id=supplier_id,
    ).aggregate(max_seq=Max('last_sequence')).get('max_seq') or 0
    
    return _build_barcode(supplier_id, batch_number, prefix, last_seq + 1)


def validate_barcode(barcode):
    """
    Validate that a barcode is unique across all packages.
    
    Returns:
        dict: {valid: bool, package_id: int or None, error: str or None}
    """
    if not barcode or not barcode.strip():
        return {'valid': False, 'package_id': None, 'error': 'Barcode is empty'}
    
    barcode = barcode.strip()
    
    existing = Package.objects.filter(barcode=barcode).first()
    if existing:
        return {
            'valid': False,
            'package_id': existing.id,
            'error': f'Barcode already exists for package #{existing.id}'
        }
    
    return {'valid': True, 'package_id': None, 'error': None}


def lookup_package_by_barcode(barcode):
    """
    Find a package by its barcode.
    
    Returns:
        Package instance or None
    """
    if not barcode:
        return None
    return Package.objects.select_related(
        'product', 'batch', 'storage_location'
    ).filter(barcode=barcode.strip()).first()


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _get_supplier_id(batch):
    """Extract supplier ID number from batch. Falls back to 0."""
    if not batch.supplier:
        return 0
    
    # Try to extract numeric ID from supplier name
    # Legacy used Supply_meat.ids (auto-increment PK)
    # We use a simple hash-based approach for the new system
    supplier = batch.supplier.strip()
    
    # If supplier is purely numeric, use it directly
    try:
        return int(supplier)
    except (ValueError, TypeError):
        pass
    
    # Otherwise, use a deterministic 2-digit hash
    return abs(hash(supplier)) % 100


def _get_batch_number(batch):
    """Extract lot/batch number. Falls back to '0'."""
    if not batch.batch_number:
        return '0'
    return str(batch.batch_number)


def _get_product_prefix(product):
    """Get barcode prefix from product. Falls back to '0000'."""
    if not product.barcode_prefix:
        return '0000'
    return str(product.barcode_prefix)


def _build_barcode(supplier_id, batch_number, prefix, sequence):
    """
    Build barcode string from components.
    
    Format: {supplier_id}{batch_number}{prefix}{sequence:02d}
    
    Example: supplier=2, batch=18, prefix=0051, seq=3
             → "218005103"
    """
    return f"{supplier_id}{batch_number}{prefix}{sequence:02d}"


def calculate_package_price(product, weight_kg, mode='price_per_kg', value=None):
    """
    Calculate package selling price.
    
    Modes:
        - price_per_kg: price = value × weight_kg
        - cost_margin: price = cost_per_kg × weight_kg × (1 + margin%/100)
        - discount: price = current_price × (1 - discount%/100)
        - auto: price = selling_price_per_kg × weight_kg
    
    Returns:
        int: Rounded-up price in THB (using math.ceil like legacy)
    """
    if weight_kg <= 0:
        return 0
    
    if mode == 'auto' or mode == 'price_per_kg':
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
