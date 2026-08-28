"""
Inventory Models — the foundation of CL.MEAT stock management.

Canonical models for:
- Product: what the product is
- Batch: a production/receiving group
- Package: a physical sellable unit
- StorageLocation: where packages are stored
- StockMovement: every important movement trace
- TemperatureLog: temperature history

Design Principles:
- Package-level traceability
- Weight in KG (decimal)
- Prices in THB (decimal)
- Every state change is traceable
"""
from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal


# ============================================================
# PRODUCT CATEGORY
# ============================================================

class Category(models.Model):
    """Product category (PORK, CHICKEN, BEEF, etc.)."""

    code = models.CharField(max_length=20, unique=True, help_text="PORK, CHICKEN, etc.")
    name = models.CharField(max_length=100, help_text="English name")
    name_thai = models.CharField(max_length=100, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'

    def __str__(self):
        return self.name

    @property
    def emoji(self):
        mapping = {
            'PORK': '🐷', 'CHICKEN': '🐔', 'BEEF': '🐄',
            'LAMB': '🐑', 'FISH': '🐟', 'OTHER': '📦',
        }
        return mapping.get(self.code, '📦')


# ============================================================
# SUPPLIER
# ============================================================

class Supplier(models.Model):
    """Supplier / source of meat products."""

    name = models.CharField(max_length=200, unique=True)
    locations = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Supplier'
        verbose_name_plural = 'Suppliers'

    def __str__(self):
        return self.name


# ============================================================
# PRODUCT
# ============================================================

class Product(models.Model):
    """
    Product definition — what the product is.

    Unified from database_clmeat_main's Category + meat_parts + Product_info
    and project_management_clmeat's Product.
    """

    UNIT_CHOICES = [
        ('KG', 'Kilogram'),
        ('PIECE', 'Piece'),
    ]

    sku = models.CharField(max_length=50, unique=True, help_text="Stock Keeping Unit")
    name = models.CharField(max_length=200, help_text="Product name (e.g., Pork Neck)")
    name_thai = models.CharField(max_length=200, blank=True, default='')
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name='products'
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='products'
    )
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default='KG')

    # Pricing (per unit)
    cost_per_kg = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Purchase cost per kilogram (THB)'
    )
    selling_price_per_kg = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Selling price per kilogram (THB)'
    )

    # Barcode prefix for generation
    barcode_prefix = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Prefix used in barcode generation'
    )

    # Nutrition per 100g
    kcalories = models.DecimalField(max_digits=8, decimal_places=1, default=Decimal('0'))
    protein = models.DecimalField(max_digits=8, decimal_places=1, default=Decimal('0'))
    fat = models.DecimalField(max_digits=8, decimal_places=1, default=Decimal('0'))

    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Product'
        verbose_name_plural = 'Products'

    def __str__(self):
        return f"{self.name} ({self.sku})"

    @property
    def display_name(self):
        return self.name_thai or self.name


# ============================================================
# BATCH
# ============================================================

class Batch(models.Model):
    """
    Batch/shipment of products received from a supplier.

    Groups packages that arrived together for traceability.
    """

    batch_number = models.CharField(
        max_length=50, unique=True, help_text="Unique batch identifier (e.g., B-20260829-001)"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name='batches'
    )
    received_at = models.DateTimeField()
    notes = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-received_at']
        verbose_name = 'Batch'
        verbose_name_plural = 'Batches'

    def __str__(self):
        return f"Batch {self.batch_number} from {self.supplier}"


# ============================================================
# STORAGE LOCATION
# ============================================================

class StorageLocation(models.Model):
    """
    Physical storage location (freezer, thaw area, display, etc.).
    """

    LOCATION_TYPE_CHOICES = [
        ('FREEZER', 'Freezer'),
        ('THAW_AREA', 'Thaw Area'),
        ('DISPLAY', 'Display Case'),
        ('STORAGE', 'General Storage'),
    ]

    name = models.CharField(max_length=100)
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPE_CHOICES)
    capacity = models.PositiveIntegerField(default=50, help_text="Maximum packages")
    thaw_capacity = models.PositiveIntegerField(
        default=20, help_text="Max concurrent thaw operations (for THAW_AREA)"
    )
    min_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Minimum allowed temperature (°C)'
    )
    max_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Maximum allowed temperature (°C)'
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['location_type', 'name']
        verbose_name = 'Storage Location'
        verbose_name_plural = 'Storage Locations'

    def __str__(self):
        return f"{self.name} ({self.get_location_type_display()})"

    @property
    def current_count(self):
        from inventory.models import Package
        return Package.objects.filter(
            storage_location=self,
            current_state__in=[
                'PACKED', 'FREEZING', 'FROZEN', 'READY_FOR_THAW',
                'THAW_QUEUED', 'THAWING', 'READY_FOR_SALE', 'ON_DISPLAY'
            ]
        ).count()

    @property
    def available_capacity(self):
        return max(0, self.capacity - self.current_count)


# ============================================================
# PACKAGE STATE CHOICES
# ============================================================

class PackageState(models.TextChoices):
    """All possible states for a package."""
    PACKED = 'PACKED'
    FREEZING = 'FREEZING'
    FROZEN = 'FROZEN'
    READY_FOR_THAW = 'READY_FOR_THAW'
    THAW_QUEUED = 'THAW_QUEUED'
    THAWING = 'THAWING'
    READY_FOR_SALE = 'READY_FOR_SALE'
    ON_DISPLAY = 'ON_DISPLAY'
    REFREEZE_PENDING = 'REFREEZE_PENDING'
    PROCESSING = 'PROCESSING'
    DISCARDED = 'DISCARDED'
    COMPLETED = 'COMPLETED'


# ============================================================
# PACKAGE
# ============================================================

class Package(models.Model):
    """
    Individual physical meat package.

    The central entity for traceability.
    Every package has a unique barcode and tracks its complete lifecycle.
    """

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='packages')
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name='packages')
    barcode = models.CharField(max_length=100, unique=True, help_text="Unique barcode")
    weight = models.DecimalField(
        max_digits=6, decimal_places=3,
        validators=[MinValueValidator(Decimal('0.001'))],
        help_text="Weight in kilograms"
    )
    selling_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Package selling price (THB)'
    )
    packed_at = models.DateTimeField()
    current_state = models.CharField(
        max_length=20,
        choices=PackageState.choices,
        default=PackageState.PACKED
    )
    storage_location = models.ForeignKey(
        StorageLocation, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='packages'
    )

    # Loyverse integration (preserved from database_clmeat_main)
    loyverse_sku = models.CharField(max_length=40, unique=True, null=True, blank=True)
    loyverse_item_id = models.CharField(max_length=100, null=True, blank=True)
    loyverse_variant_id = models.CharField(max_length=100, null=True, blank=True)
    loyverse_synced = models.BooleanField(default=False)
    loyverse_synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-packed_at']
        verbose_name = 'Package'
        verbose_name_plural = 'Packages'

    def __str__(self):
        return f"{self.product.name} {self.weight}kg ({self.barcode})"

    @property
    def display_name(self):
        return f"{self.product.name} {self.weight}kg"

    @property
    def state_display(self):
        return self.get_current_state_display()

    @property
    def is_frozen(self):
        return self.current_state in ['FROZEN', 'READY_FOR_THAW', 'THAW_QUEUED']

    @property
    def is_displayable(self):
        return self.current_state == 'READY_FOR_SALE'

    @property
    def is_active(self):
        """Package is in an active lifecycle state (not terminal)."""
        return self.current_state not in ['COMPLETED', 'DISCARDED']

    def can_transition_to(self, target_state):
        from common.state_machine import can_transition
        return can_transition(self.current_state, target_state)

    @property
    def next_action(self):
        """Return the next valid action for this package."""
        from common.worker_actions import get_next_action
        return get_next_action(self)

    @property
    def current_task(self):
        """Get the current pending task for this package."""
        from operations.models import WorkerTask, TaskStatus
        return WorkerTask.objects.filter(
            package=self,
            status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
        ).order_by('scheduled_at').first()


# ============================================================
# PRICE CHANGE HISTORY
# ============================================================

class PriceChangeHistory(models.Model):
    """Audit trail for package price changes."""

    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name='price_changes')
    old_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    new_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    mode = models.CharField(
        max_length=30, default='manual',
        help_text='auto | price_per_kg | cost_margin | discount | manual'
    )
    value = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='The value used in calculation'
    )
    actor = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Price Change History'
        verbose_name_plural = 'Price Change Histories'

    def __str__(self):
        return f"Price #{self.id}: {self.package} ฿{self.old_price} → ฿{self.new_price}"


# ============================================================
# BARCODE SEQUENCE
# ============================================================

class BarcodeSequence(models.Model):
    """Tracks the last barcode sequence number per product/batch/supplier."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='barcode_sequences')
    batch_number = models.CharField(max_length=50)
    supplier_id = models.PositiveIntegerField(default=0)
    last_sequence = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['product', 'batch_number', 'supplier_id']
        verbose_name = 'Barcode Sequence'
        verbose_name_plural = 'Barcode Sequences'

    def __str__(self):
        return f"{self.product.name} / {self.batch_number}: seq={self.last_sequence}"


# ============================================================
# PRODUCT PLANNING PROFILE
# ============================================================

class ProductPlanningProfile(models.Model):
    """Planning parameters per product — demand, coverage, safety stock."""

    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name='planning_profile', primary_key=True
    )
    avg_daily_usage_kg = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0'),
        help_text='Average daily usage in kg'
    )
    safety_stock_days = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal('1.0'),
        help_text='Minimum days of stock as safety buffer'
    )
    target_coverage_days = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal('7.0'),
        help_text='Target days of stock to keep on hand'
    )
    min_order_qty_kg = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0'),
        help_text='Minimum quantity to prepare per batch'
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Product Planning Profile'
        verbose_name_plural = 'Product Planning Profiles'

    def __str__(self):
        return f"{self.product.name}: {self.avg_daily_usage_kg} kg/day"


# ============================================================
# STOCK MOVEMENT
# ============================================================

class StockMovement(models.Model):
    """
    Records every important movement of a package.

    Provides full traceability: where, when, who, why.
    """

    MOVEMENT_TYPE_CHOICES = [
        ('RECEIVED', '📦 Received into storage'),
        ('MOVED', '🔄 Moved between locations'),
        ('FROZE', '🧊 Frozen'),
        ('THAW_QUEUED', '📋 Queued for thaw'),
        ('THAWING', '🔄 Thawing started'),
        ('THAW_COMPLETE', '✅ Thaw complete'),
        ('DISPLAYED', '🛒 Moved to display'),
        ('PULLED', '⏸️ Pulled from display'),
        ('REFREEZE', '🧊 Returned to freeze'),
        ('SOLD', '💰 Sold'),
        ('PROCESSED', '🔪 Processed'),
        ('DISCARDED', '🗑️ Discarded'),
        ('ADJUSTED', '⚖️ Weight/price adjusted'),
    ]

    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPE_CHOICES)
    from_location = models.ForeignKey(
        StorageLocation, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='outgoing_movements'
    )
    to_location = models.ForeignKey(
        StorageLocation, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='incoming_movements'
    )
    weight_at_movement = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal('0'),
        help_text='Weight at time of movement (kg)'
    )
    actor = models.CharField(max_length=100, blank=True, default='')
    reason = models.TextField(blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Stock Movement'
        verbose_name_plural = 'Stock Movements'

    def __str__(self):
        return f"{self.get_movement_type_display()}: {self.package.barcode}"


# ============================================================
# TEMPERATURE LOG
# ============================================================

class TemperatureLog(models.Model):
    """
    Manual or sensor temperature reading for a storage location.
    """

    SOURCE_CHOICES = [
        ('MANUAL', 'Manual Check'),
        ('SENSOR', 'IoT Sensor'),
        ('BLUETOOTH', 'Bluetooth Probe'),
    ]
    STATUS_CHOICES = [
        ('OK', 'OK'),
        ('WARNING', 'Warning'),
        ('CRITICAL', 'Critical'),
    ]

    location = models.ForeignKey(StorageLocation, on_delete=models.CASCADE, related_name='temperature_logs')
    actual_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, help_text='Measured temperature (°C)'
    )
    target_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    min_allowed = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    max_allowed = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OK')
    source = models.CharField(max_length=15, choices=SOURCE_CHOICES, default='MANUAL')
    recorded_by = models.CharField(max_length=100, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']
        verbose_name = 'Temperature Log'
        verbose_name_plural = 'Temperature Logs'

    def __str__(self):
        return f'{self.location.name}: {self.actual_temperature}°C ({self.status})'

    @property
    def temperature_status(self):
        if self.min_allowed is not None and self.actual_temperature < self.min_allowed:
            return 'CRITICAL'
        if self.max_allowed is not None and self.actual_temperature > self.max_allowed:
            return 'CRITICAL'
        if self.max_allowed is not None and self.actual_temperature > (self.max_allowed - 2):
            return 'WARNING'
        if self.min_allowed is not None and self.actual_temperature < (self.min_allowed + 2):
            return 'WARNING'
        return 'OK'
