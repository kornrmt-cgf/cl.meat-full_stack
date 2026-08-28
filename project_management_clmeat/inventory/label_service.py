"""
Label Service — generates label data from Package records.

This is the single source of truth for label content.
Any label printer (NIIMBOT, Zebra, etc.) gets its data from here.
"""
from decimal import Decimal
from django.utils import timezone


# ============================================================
# LABEL DATA STRUCTURE
# ============================================================

def get_label_data(package):
    """
    Generate complete label data for a package.
    
    Args:
        package: Package instance (with product, batch select_related)
    
    Returns:
        dict with all label fields
    """
    product = package.product
    batch = package.batch
    packed_local = timezone.localtime(package.packed_at) if package.packed_at else None
    
    return {
        # Product info
        'product_name': product.name,
        'product_sku': product.sku,
        'category': product.get_category_display(),
        'category_emoji': _category_emoji(product.category),
        
        # Package info
        'barcode': package.barcode or '',
        'weight_kg': float(package.weight),
        'weight_display': _fmt_weight(package.weight),
        'weight_grams': _weight_to_grams(package.weight),
        
        # Pricing
        'selling_price': float(package.selling_price),
        'selling_price_per_kg': float(product.selling_price_per_kg),
        'cost_per_kg': float(product.cost_per_kg),
        'profit_per_kg': float(product.selling_price_per_kg - product.cost_per_kg),
        
        # Batch/production
        'batch_number': batch.batch_number,
        'supplier': batch.supplier,
        'production_date': packed_local.strftime('%d/%m/%Y') if packed_local else '',
        'production_time': packed_local.strftime('%H:%M') if packed_local else '',
        'mfg_display': packed_local.strftime('%d/%m/%Y %H:%M') if packed_local else '',
        
        # Nutrition (per 100g)
        'kcalories': float(product.kcalories),
        'protein': float(product.protein),
        'fat': float(product.fat),
        'has_nutrition': (
            product.kcalories > 0 or product.protein > 0 or product.fat > 0
        ),
        
        # Formatted strings for label printing
        'price_display': f"฿{int(package.selling_price)}",
        'price_per_kg_display': f"฿{int(product.selling_price_per_kg)}",
        'lot_display': f"MFG:{packed_local.strftime('%d/%m/%Y %H:%M')}" if packed_local else '',
        'source_display': batch.supplier or '',
    }


def get_niimbot_label_data(package):
    """
    Generate NIIMBOT-specific label data.
    
    NIIMBOT label format expects:
        product: str (product name)
        barcode: str
        weight: str (formatted weight in kg)
        price: str (package price, no currency symbol)
        price_per_kg: str (price per kg, no currency symbol)
        lot: str (MFG date string)
        from_at: str (supplier name)
        types: str (category emoji)
    
    Returns:
        dict matching NIIMBOTController.print_label() parameters
    """
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


# ============================================================
# NIIMBOT PRINT SERVICE
# ============================================================

class NIIMBOTPrintService:
    """
    NIIMBOT label printer adapter.
    
    Architecture:
        Django Package → LabelService → NIIMBOTPrintService → NIIMBOT App
    
    This adapter tries to use pyautogui for desktop automation.
    If pyautogui is not available (e.g., headless server), it returns
    the label data without printing.
    """
    
    def __init__(self):
        self._controller = None
        self._available = False
        self._load_controller()
    
    def _load_controller(self):
        """Try to load NIIMBOTController from legacy module."""
        try:
            import sys
            import os
            # Add legacy project to path if available
            legacy_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'database_clmeat_main'
            )
            if legacy_path not in sys.path:
                sys.path.insert(0, legacy_path)
            
            from stock_meat.niimbot import NIIMBOTController
            self._controller = NIIMBOTController()
            self._available = True
        except (ImportError, Exception) as e:
            self._controller = None
            self._available = False
    
    @property
    def is_available(self):
        """Check if NIIMBOT printing is available."""
        return self._available
    
    def print_label(self, package):
        """
        Print a label for the given package.
        
        Args:
            package: Package instance
        
        Returns:
            dict: {
                success: bool,
                printed: bool (True if actually printed),
                label_data: dict (full label data for inspection),
                niimbot_data: dict (NIIMBOT-specific format),
                error: str or None
            }
        """
        label_data = get_label_data(package)
        niimbot_data = get_niimbot_label_data(package)
        
        if not self._available:
            return {
                'success': True,
                'printed': False,
                'label_data': label_data,
                'niimbot_data': niimbot_data,
                'error': 'NIIMBOT not available (pyautogui not installed or headless environment)',
            }
        
        if not self._controller:
            return {
                'success': True,
                'printed': False,
                'label_data': label_data,
                'niimbot_data': niimbot_data,
                'error': 'NIIMBOT controller failed to initialize',
            }
        
        try:
            self._controller.print_label(
                product=niimbot_data['product'],
                barcode=niimbot_data['barcode'],
                weight=niimbot_data['weight'],
                price=niimbot_data['price'],
                price_per_kg=niimbot_data['price_per_kg'],
                lot=niimbot_data['lot'],
                from_at=niimbot_data['from_at'],
                types=niimbot_data['types'],
            )
            return {
                'success': True,
                'printed': True,
                'label_data': label_data,
                'niimbot_data': niimbot_data,
                'error': None,
            }
        except Exception as e:
            return {
                'success': False,
                'printed': False,
                'label_data': label_data,
                'niimbot_data': niimbot_data,
                'error': str(e),
            }
    
    def preview_label(self, package):
        """
        Preview label data without printing.
        
        Returns:
            dict: Complete label data for preview/display
        """
        return get_label_data(package)


# ============================================================
# HELPERS
# ============================================================

def _category_emoji(category):
    """Map product category to emoji."""
    mapping = {
        'PORK': '🐷',
        'CHICKEN': '🐔',
        'BEEF': '🐄',
        'LAMB': '🐑',
        'FISH': '🐟',
        'OTHER': '📦',
    }
    return mapping.get(category, '📦')


def _fmt_weight(weight_kg):
    """Format weight for display."""
    if weight_kg is None:
        return '-'
    return f"{float(weight_kg):.3f} กก."


def _weight_to_grams(weight_kg):
    """Convert kg to grams for label display."""
    if weight_kg is None:
        return 0
    return int(float(weight_kg) * 1000)
