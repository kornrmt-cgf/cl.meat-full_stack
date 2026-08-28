"""
Inventory Template Views.
"""
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import Product, Batch, Package, StorageLocation
from .forms import ProductForm, BatchForm, PackageForm, StorageLocationForm
from .selectors import get_all_packages, get_all_products, get_all_batches, get_storage_locations


@login_required
def product_edit(request, pk):
    """Edit a product — admin/manager only."""
    if not (request.user.is_superuser or request.user.has_perm('inventory.change_product')):
        messages.error(request, 'คุณไม่มีสิทธิ์แก้ไขสินค้า')
        return redirect('inventory:product_list')

    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, f'แก้ไขสินค้า {product.name} สำเร็จ')
            return redirect('inventory:product_list')
    else:
        form = ProductForm(instance=product)
    return render(request, 'inventory/product_form.html', {'form': form, 'title': f'แก้ไขสินค้า {product.name}'})


@login_required
def package_list(request):
    """List all packages with filters."""
    state_filter = request.GET.get('state', '')
    product_filter = request.GET.get('product', '')
    
    packages = get_all_packages()
    
    if state_filter:
        packages = packages.filter(current_state=state_filter)
    if product_filter:
        packages = packages.filter(product_id=product_filter)
    
    context = {
        'packages': packages,
        'products': get_all_products(),
        'states': Package.State.choices if hasattr(Package, 'State') else [],
        'current_state_filter': state_filter,
        'current_product_filter': product_filter,
    }
    return render(request, 'inventory/package_list.html', context)


@login_required
def package_detail(request, pk):
    """Package detail with timeline."""
    package = get_object_or_404(
        Package.objects.select_related('product', 'batch', 'storage_location'),
        pk=pk
    )
    
    # Get rotation events for timeline
    from operations.models import RotationEvent
    events = RotationEvent.objects.filter(package=package).order_by('timestamp')
    
    context = {
        'package': package,
        'events': events,
    }
    return render(request, 'inventory/package_detail.html', context)


@login_required
def package_create(request):
    """Create a new package."""
    if request.method == 'POST':
        form = PackageForm(request.POST)
        if form.is_valid():
            package = form.save()
            messages.success(request, f'Package {package.pk} created successfully.')
            return redirect('inventory:package_detail', pk=package.pk)
    else:
        form = PackageForm()
    
    return render(request, 'inventory/package_form.html', {'form': form, 'title': 'Create Package'})


@login_required
def product_list(request):
    """List all products."""
    products = get_all_products(active_only=False)
    return render(request, 'inventory/product_list.html', {'products': products})


@login_required
def product_create(request):
    """Create a new product."""
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            messages.success(request, f'Product {product.name} created successfully.')
            return redirect('inventory:product_list')
    else:
        form = ProductForm()
    
    return render(request, 'inventory/product_form.html', {'form': form, 'title': 'Create Product'})


@login_required
def batch_list(request):
    """List all batches."""
    batches = get_all_batches(active_only=False)
    return render(request, 'inventory/batch_list.html', {'batches': batches})


@login_required
def batch_create(request):
    """Create a new batch."""
    if request.method == 'POST':
        form = BatchForm(request.POST)
        if form.is_valid():
            batch = form.save()
            messages.success(request, f'Batch {batch.batch_number} created successfully.')
            return redirect('inventory:batch_list')
    else:
        form = BatchForm()
    
    return render(request, 'inventory/batch_form.html', {'form': form, 'title': 'Create Batch'})


@login_required
def location_list(request):
    """List all storage locations."""
    locations = get_storage_locations(active_only=False)
    return render(request, 'inventory/location_list.html', {'locations': locations})


@login_required
def location_create(request):
    """Create a new storage location."""
    if request.method == 'POST':
        form = StorageLocationForm(request.POST)
        if form.is_valid():
            location = form.save()
            messages.success(request, f'Location {location.name} created successfully.')
            return redirect('inventory:location_list')
    else:
        form = StorageLocationForm()
    
    return render(request, 'inventory/location_form.html', {'form': form, 'title': 'Create Location'})
