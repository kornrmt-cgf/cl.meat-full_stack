"""
Label Service — generates label data from Package records.

Single source of truth for label content.
Any label printer (NIIMBOT, Zebra, etc.) gets data from here.
"""
from decimal import Decimal
from django.utils import timezone


def get_label_data(package):
    """Generate complete label data for a package."""
    product = package.product
    batch = package.batch
    packed_local = timezone.localtime(package.packed_at) if package.packed_at else None

    return {
        'product_name': product.name,
        'product_name_thai': product.name_thai or product.name,
        'product_sku': product.sku,
        'category': product.category.name if product.category else '',
        'category_emoji': product.category.emoji if product.category else '📦',
        'barcode': package.barcode or '',
        'weight_kg': float(package.weight),
        'weight_display': f"{float(package.weight):.3f} กก.",
        'weight_grams': int(float(package.weight) * 1000),
        'selling_price': float(package.selling_price),
        'selling_price_per_kg': float(product.selling_price_per_kg),
        'cost_per_kg': float(product.cost_per_kg),
        'profit_per_kg': float(product.selling_price_per_kg - product.cost_per_kg),
        'batch_number': batch.batch_number,
        'supplier': str(batch.supplier),
        'mfg_display': packed_local.strftime('%d/%m/%Y %H:%M') if packed_local else '',
        'lot_display': f"MFG:{packed_local.strftime('%d/%m/%Y %H:%M')}" if packed_local else '',
        'source_display': str(batch.supplier) if batch.supplier else '',
        'kcalories': float(product.kcalories),
        'protein': float(product.protein),
        'fat': float(product.fat),
        'price_display': f"฿{int(package.selling_price)}",
        'price_per_kg_display': f"฿{int(product.selling_price_per_kg)}",
    }


def get_niimbot_label_data(package):
    """Generate NIIMBOT-specific label data."""
    data = get_label_data(package)
    return {
        'product': data['product_name'],
        'barcode': data['barcode'],
        'weight': f"{data['weight_kg']:.3f}",
        'price': str(int(data['selling_price'])),
        'price_per_kg': str(int(data['selling_price_per_kg'])),
        'lot': data['lot_display'],
        'from_at': data['source_display'],
        'types': data['category_emoji'],
    }
