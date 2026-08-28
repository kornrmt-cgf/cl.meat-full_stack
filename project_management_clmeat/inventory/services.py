"""
Inventory Business Logic Services.
"""
from django.db import transaction
from django.utils import timezone
from .models import Product, Batch, Package, StorageLocation, PackageState


def create_product(sku, name, category, unit='KG', barcode='', active=True):
    """
    Create a new product.
    
    Args:
        sku: Stock Keeping Unit (unique)
        name: Product name
        category: Product category
        unit: Unit of measurement
        barcode: Optional barcode
        active: Whether product is active
        
    Returns:
        Product instance
    """
    if Product.objects.filter(sku=sku).exists():
        raise ValueError(f"Product with SKU '{sku}' already exists")
    
    return Product.objects.create(
        sku=sku,
        name=name,
        category=category,
        unit=unit,
        barcode=barcode,
        active=active
    )


def create_batch(batch_number, supplier, received_at=None, notes='', active=True):
    """
    Create a new batch.
    
    Args:
        batch_number: Unique batch identifier
        supplier: Supplier name
        received_at: When batch was received
        notes: Optional notes
        active: Whether batch is active
        
    Returns:
        Batch instance
    """
    if Batch.objects.filter(batch_number=batch_number).exists():
        raise ValueError(f"Batch '{batch_number}' already exists")
    
    if received_at is None:
        received_at = timezone.now()
    
    return Batch.objects.create(
        batch_number=batch_number,
        supplier=supplier,
        received_at=received_at,
        notes=notes,
        active=active
    )


def create_package(product, batch, weight, barcode='', packed_at=None, storage_location=None):
    """
    Create a new package.
    
    Args:
        product: Product instance
        batch: Batch instance
        weight: Weight in kg
        barcode: Optional barcode
        packed_at: When package was packed
        storage_location: Optional storage location
        
    Returns:
        Package instance
    """
    if packed_at is None:
        packed_at = timezone.now()
    
    return Package.objects.create(
        product=product,
        batch=batch,
        weight=weight,
        barcode=barcode,
        packed_at=packed_at,
        current_state=PackageState.PACKED,
        storage_location=storage_location
    )


def create_storage_location(name, location_type, capacity=50, active=True):
    """
    Create a new storage location.
    
    Args:
        name: Location name
        location_type: Type of location
        capacity: Maximum packages
        active: Whether location is active
        
    Returns:
        StorageLocation instance
    """
    return StorageLocation.objects.create(
        name=name,
        location_type=location_type,
        capacity=capacity,
        active=active
    )


def get_package_by_id(package_id):
    """
    Get package by ID.
    
    Args:
        package_id: Package ID
        
    Returns:
        Package instance
        
    Raises:
        Package.DoesNotExist: If package not found
    """
    return Package.objects.select_related('product', 'batch', 'storage_location').get(pk=package_id)


def get_packages_by_state(state, product=None):
    """
    Get packages by state.
    
    Args:
        state: Package state
        product: Optional product filter
        
    Returns:
        QuerySet of packages
    """
    queryset = Package.objects.filter(current_state=state).select_related('product', 'batch')
    if product:
        queryset = queryset.filter(product=product)
    return queryset


def get_available_packages(product=None):
    """
    Get packages available for planning (FROZEN state).
    
    Args:
        product: Optional product filter
        
    Returns:
        QuerySet of available packages
    """
    return get_packages_by_state(PackageState.FROZEN, product)


def move_package_to_location(package, location):
    """
    Move package to a storage location.
    
    Args:
        package: Package instance
        location: StorageLocation instance
        
    Returns:
        Updated package
    """
    if not location.active:
        raise ValueError(f"Location '{location.name}' is not active")
    
    if location.available_capacity <= 0:
        raise ValueError(f"Location '{location.name}' is at full capacity")
    
    package.storage_location = location
    package.save(update_fields=['storage_location', 'updated_at'])
    
    return package
