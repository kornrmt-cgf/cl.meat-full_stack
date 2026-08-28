"""
Inventory Models: Product, Batch, Package, StorageLocation
"""
from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal


class Product(models.Model):
    """Product definition (e.g., Pork Collar, Chicken Breast)."""
    
    CATEGORY_CHOICES = [
        ('PORK', 'Pork'),
        ('CHICKEN', 'Chicken'),
        ('BEEF', 'Beef'),
        ('LAMB', 'Lamb'),
        ('FISH', 'Fish'),
        ('OTHER', 'Other'),
    ]
    
    UNIT_CHOICES = [
        ('KG', 'Kilogram'),
        ('PIECE', 'Piece'),
    ]
    
    id = models.AutoField(primary_key=True)
    sku = models.CharField(max_length=50, unique=True, help_text="Stock Keeping Unit")
    barcode = models.CharField(max_length=100, blank=True, default='')
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, default='KG')
    # --- Pricing ---
    cost_per_kg = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Purchase cost per kilogram (THB)'
    )
    selling_price_per_kg = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Selling price per kilogram (THB)'
    )
    # --- Barcode prefix for generation ---
    barcode_prefix = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Prefix used in barcode generation (e.g., 0051)'
    )
    # --- Nutrition per 100g ---
    kcalories = models.DecimalField(
        max_digits=8, decimal_places=1, default=Decimal('0'),
        help_text='Kilocalories per 100g'
    )
    protein = models.DecimalField(
        max_digits=8, decimal_places=1, default=Decimal('0'),
        help_text='Protein in grams per 100g'
    )
    fat = models.DecimalField(
        max_digits=8, decimal_places=1, default=Decimal('0'),
        help_text='Fat in grams per 100g'
    )
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
        return self.name


class Batch(models.Model):
    """Batch/shipment of products received from supplier."""
    
    id = models.AutoField(primary_key=True)
    batch_number = models.CharField(max_length=50, unique=True, help_text="Unique batch identifier")
    supplier = models.CharField(max_length=200)
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


class StorageLocation(models.Model):
    """Physical storage location (freezer, thaw area, display, etc.)."""
    
    LOCATION_TYPE_CHOICES = [
        ('FREEZER', 'Freezer'),
        ('THAW_AREA', 'Thaw Area'),
        ('DISPLAY', 'Display Case'),
        ('STORAGE', 'General Storage'),
    ]
    
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPE_CHOICES)
    capacity = models.PositiveIntegerField(default=50, help_text="Maximum packages")
    thaw_capacity = models.PositiveIntegerField(
        default=20, help_text="Maximum concurrent thaw operations (for THAW_AREA type)"
    )
    min_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Minimum allowed temperature in Celsius'
    )
    max_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Maximum allowed temperature in Celsius'
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
        """Get current number of packages in this location."""
        return Package.objects.filter(
            storage_location=self,
            current_state__in=['PACKED', 'FREEZING', 'FROZEN', 'READY_FOR_THAW', 'THAWING', 'READY_FOR_SALE', 'ON_DISPLAY']
        ).count()
    
    @property
    def available_capacity(self):
        """Get available capacity."""
        return max(0, self.capacity - self.current_count)


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


class Package(models.Model):
    """Individual physical meat package."""
    
    id = models.AutoField(primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='packages')
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name='packages')
    barcode = models.CharField(max_length=100, blank=True, default='')
    weight = models.DecimalField(
        max_digits=6, 
        decimal_places=3,
        validators=[MinValueValidator(Decimal('0.001'))],
        help_text="Weight in kilograms"
    )
    selling_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Package selling price (THB) — computed from weight × price/kg'
    )
    packed_at = models.DateTimeField()
    current_state = models.CharField(
        max_length=20, 
        choices=PackageState.choices, 
        default=PackageState.PACKED
    )
    storage_location = models.ForeignKey(
        StorageLocation, 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL,
        related_name='packages'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-packed_at']
        verbose_name = 'Package'
        verbose_name_plural = 'Packages'
    
    def __str__(self):
        return f"{self.product.name} {self.weight}kg ({self.barcode or self.pk})"
    
    @property
    def display_name(self):
        """Human-readable package name."""
        return f"{self.product.name} {self.weight}kg"
    
    @property
    def state_display(self):
        """Human-readable state."""
        return self.get_current_state_display()
    
    @property
    def is_frozen(self):
        """Check if package is in frozen state."""
        return self.current_state in ['FROZEN', 'READY_FOR_THAW', 'THAW_QUEUED']
    
    @property
    def is_displayable(self):
        """Check if package can be moved to display."""
        return self.current_state == 'READY_FOR_SALE'
    
    def can_transition_to(self, target_state):
        """Check if this package can transition to target state."""
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


class PriceChangeHistory(models.Model):
    """Audit trail for package price changes."""
    
    id = models.AutoField(primary_key=True)
    package = models.ForeignKey('Package', on_delete=models.CASCADE, related_name='price_changes')
    old_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    new_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    mode = models.CharField(
        max_length=30, default='manual',
        help_text='Change mode: manual, cost_margin, discount, price_per_kg, auto'
    )
    value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='The value used in calculation (margin %, discount %, or price/kg)'
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


class BarcodeSequence(models.Model):
    """Tracks the last barcode sequence number per product to prevent duplicates."""
    
    id = models.AutoField(primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='barcode_sequences')
    batch_number = models.CharField(max_length=50, help_text='Batch number this sequence belongs to')
    supplier_id = models.PositiveIntegerField(default=0, help_text='Supplier ID for barcode composition')
    last_sequence = models.PositiveIntegerField(default=0, help_text='Last used sequence number')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['product', 'batch_number', 'supplier_id']
        verbose_name = 'Barcode Sequence'
        verbose_name_plural = 'Barcode Sequences'
    
    def __str__(self):
        return f"{self.product.name} / {self.batch_number}: seq={self.last_sequence}"


class ProductPlanningProfile(models.Model):
    """Planning parameters per product — demand, coverage, safety stock."""

    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name='planning_profile',
        primary_key=True
    )
    avg_daily_usage_kg = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0'),
        help_text='Average daily usage in kg'
    )
    safety_stock_days = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal('1.0'),
        help_text='Minimum days of stock to maintain as safety buffer'
    )
    target_coverage_days = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal('7.0'),
        help_text='Target number of days of stock to keep on hand'
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

    @property
    def daily_usage_display(self):
        return f"{self.avg_daily_usage_kg} กก./วัน"


class TemperatureLog(models.Model):
    """Manual or sensor temperature reading for a storage location."""

    TEMPERATURE_SOURCE_CHOICES = [
        ('MANUAL', 'Manual Check'),
        ('SENSOR', 'IoT Sensor'),
        ('BLUETOOTH', 'Bluetooth Probe'),
    ]

    TEMPERATURE_STATUS_CHOICES = [
        ('OK', 'OK'),
        ('WARNING', 'Warning'),
        ('CRITICAL', 'Critical'),
    ]

    id = models.AutoField(primary_key=True)
    location = models.ForeignKey(
        StorageLocation, on_delete=models.CASCADE, related_name='temperature_logs'
    )
    actual_temperature = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text='Measured temperature in Celsius'
    )
    target_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Expected target temperature'
    )
    min_allowed = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Minimum allowed temperature'
    )
    max_allowed = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Maximum allowed temperature'
    )
    status = models.CharField(
        max_length=10, choices=TEMPERATURE_STATUS_CHOICES, default='OK'
    )
    source = models.CharField(
        max_length=15, choices=TEMPERATURE_SOURCE_CHOICES, default='MANUAL'
    )
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
        """Calculate status based on allowed range."""
        if self.min_allowed is not None and self.actual_temperature < self.min_allowed:
            return 'CRITICAL'
        if self.max_allowed is not None and self.actual_temperature > self.max_allowed:
            return 'CRITICAL'
        # Warning zone: within 2°C of limits
        if self.max_allowed is not None and self.actual_temperature > (self.max_allowed - 2):
            return 'WARNING'
        if self.min_allowed is not None and self.actual_temperature < (self.min_allowed + 2):
            return 'WARNING'
        return 'OK'
