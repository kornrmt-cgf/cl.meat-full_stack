"""
Inventory Selectors: Read-only queries and data access.
"""
from django.db.models import Count, Sum, Q
from .models import Product, Batch, Package, StorageLocation, PackageState


def get_all_products(active_only=True):
    """Get all products."""
    queryset = Product.objects.all()
    if active_only:
        queryset = queryset.filter(active=True)
    return queryset


def get_all_batches(active_only=True):
    """Get all batches."""
    queryset = Batch.objects.all()
    if active_only:
        queryset = queryset.filter(active=True)
    return queryset


def get_all_packages(product=None, batch=None, state=None, product_id=None):
    """Get all packages with optional filters."""
    queryset = Package.objects.select_related('product', 'batch', 'storage_location')
    
    if product:
        queryset = queryset.filter(product=product)
    if product_id:
        queryset = queryset.filter(product_id=product_id)
    if batch:
        queryset = queryset.filter(batch=batch)
    if state:
        queryset = queryset.filter(current_state=state)
    
    return queryset


def get_package_detail(package_id):
    """Get package with all related data."""
    return Package.objects.select_related(
        'product', 'batch', 'storage_location'
    ).get(pk=package_id)


def get_storage_locations(location_type=None, active_only=True):
    """Get storage locations."""
    queryset = StorageLocation.objects.all()
    if location_type:
        queryset = queryset.filter(location_type=location_type)
    if active_only:
        queryset = queryset.filter(active=True)
    return queryset


def get_package_stats():
    """Get package statistics."""
    stats = Package.objects.aggregate(
        total=Count('id'),
        frozen=Count('id', filter=Q(current_state=PackageState.FROZEN)),
        thawing=Count('id', filter=Q(current_state=PackageState.THAWING)),
        display=Count('id', filter=Q(current_state=PackageState.ON_DISPLAY)),
    )
    return stats


def get_product_stats():
    """Get product statistics."""
    return Product.objects.filter(active=True).annotate(
        package_count=Count('packages'),
        total_weight=Sum('packages__weight')
    )


def get_batch_stats(batch_id):
    """Get statistics for a specific batch."""
    batch = Batch.objects.get(pk=batch_id)
    packages = batch.packages.all()
    
    return {
        'batch': batch,
        'total_packages': packages.count(),
        'total_weight': packages.aggregate(total=Sum('weight'))['total'] or 0,
        'packages_by_state': {
            state: packages.filter(current_state=state).count()
            for state in PackageState.choices
        }
    }
