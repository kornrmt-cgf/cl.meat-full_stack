"""
Inventory API Views: JSON responses for frontend.
"""
import json
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .selectors import get_all_packages, get_package_detail
from .services import create_package
from common.time_service import format_display


@require_http_methods(["GET"])
def package_list_api(request):
    """List packages with optional filters."""
    state = request.GET.get('state')
    product = request.GET.get('product')
    
    packages = get_all_packages(state=state, product=product)
    
    data = []
    for p in packages:
        data.append({
            'id': p.pk,
            'product_name': p.product.name,
            'weight': str(p.weight),
            'barcode': p.barcode,
            'current_state': p.current_state,
            'state_display': p.get_current_state_display(),
            'packed_at': format_display(p.packed_at),
            'batch_number': p.batch.batch_number,
            'storage_location': p.storage_location.name if p.storage_location else None,
        })
    
    return JsonResponse({'packages': data})


@require_http_methods(["GET"])
def package_detail_api(request, pk):
    """Get package detail."""
    try:
        package = get_package_detail(pk)
    except Exception:
        return JsonResponse({'error': 'Package not found'}, status=404)
    
    # Get timeline events
    from operations.models import RotationEvent
    events = RotationEvent.objects.filter(package=package).order_by('timestamp')
    
    timeline = []
    for event in events:
        timeline.append({
            'event_type': event.event_type,
            'from_state': event.from_state,
            'to_state': event.to_state,
            'timestamp': format_display(event.timestamp),
            'actor': event.actor,
            'reason': event.reason,
        })
    
    data = {
        'id': package.pk,
        'product_name': package.product.name,
        'product_sku': package.product.sku,
        'weight': str(package.weight),
        'barcode': package.barcode,
        'current_state': package.current_state,
        'state_display': package.get_current_state_display(),
        'packed_at': format_display(package.packed_at),
        'batch_number': package.batch.batch_number,
        'storage_location': package.storage_location.name if package.storage_location else None,
        'timeline': timeline,
    }
    
    return JsonResponse(data)


@require_http_methods(["POST"])
def package_create_api(request):
    """Create a new package via API."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    try:
        from .models import Product, Batch
        product = Product.objects.get(pk=data['product_id'])
        batch = Batch.objects.get(pk=data['batch_id'])
        
        package = create_package(
            product=product,
            batch=batch,
            weight=data['weight'],
            barcode=data.get('barcode', ''),
        )
        
        return JsonResponse({
            'id': package.pk,
            'message': 'Package created successfully'
        }, status=201)
        
    except Product.DoesNotExist:
        return JsonResponse({'error': 'Product not found'}, status=404)
    except Batch.DoesNotExist:
        return JsonResponse({'error': 'Batch not found'}, status=404)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_http_methods(["GET"])
def package_timeline_api(request, pk):
    """Get package timeline."""
    try:
        package = get_package_detail(pk)
    except Exception:
        return JsonResponse({'error': 'Package not found'}, status=404)
    
    from operations.models import RotationEvent
    events = RotationEvent.objects.filter(package=package).order_by('timestamp')
    
    timeline = []
    for event in events:
        timeline.append({
            'event_type': event.event_type,
            'from_state': event.from_state,
            'to_state': event.to_state,
            'timestamp': format_display(event.timestamp),
            'actor': event.actor,
        })
    
    return JsonResponse({'timeline': timeline})
