"""
Planning Models: FreezeProfile, ThawProfile, RotationPlan, ThawQueueEntry
"""
from django.db import models
from datetime import timedelta
from decimal import Decimal


class FreezeProfile(models.Model):
    """Freeze profile defining store-specific operational rules."""
    
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    target_temperature = models.DecimalField(max_digits=5, decimal_places=2, help_text="Target temperature in Celsius")
    minimum_duration = models.DurationField(help_text="Minimum freeze duration")
    default_duration = models.DurationField(help_text="Default freeze duration")
    buffer_duration = models.DurationField(default=timedelta(hours=0), help_text="Buffer time added to duration")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
        verbose_name = 'Freeze Profile'
        verbose_name_plural = 'Freeze Profiles'
    
    def __str__(self):
        return f"{self.name} ({self.target_temperature}°C)"


class ThawProfile(models.Model):
    """Thaw profile defining store-specific operational rules.
    
    Thaw times are CONFIGURABLE ESTIMATES, not universal safety rules.
    Actual thaw time depends on starting temperature, package geometry,
    airflow, refrigerator temperature, and other factors.
    
    The weight_threshold_kg determines which duration is used:
    - weight <= weight_threshold_kg → use minimum_duration
    - weight > weight_threshold_kg → interpolate between minimum and default
    - weight > 2× threshold → use default_duration × weight_scale_factor
    
    Safety buffer is always added on top of calculated duration.
    """
    
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    
    # --- Duration configuration ---
    default_duration = models.DurationField(
        help_text="Default thaw duration for packages above threshold weight"
    )
    minimum_duration = models.DurationField(
        help_text="Minimum thaw duration for small packages"
    )
    buffer_duration = models.DurationField(
        default=timedelta(hours=0),
        help_text="Safety buffer added on top of calculated duration"
    )
    
    # --- Weight-based duration scaling ---
    weight_threshold_kg = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal('0.500'),
        help_text="Weight (kg) at which minimum_duration transitions to default_duration"
    )
    weight_scale_factor = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('1.20'),
        help_text="Multiplier for duration when package exceeds 2× threshold (e.g., 1.20 = +20%)"
    )
    
    # --- Temperature configuration ---
    target_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('3.00'),
        help_text="Target thaw temperature in Celsius (e.g., 3°C for refrigerated thawing)"
    )
    min_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('1.00'),
        help_text="Minimum allowed thaw temperature in Celsius"
    )
    max_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('5.00'),
        help_text="Maximum allowed thaw temperature in Celsius"
    )
    
    # --- Capacity ---
    thaw_capacity = models.PositiveIntegerField(
        default=20,
        help_text="Maximum concurrent thaw operations for this profile"
    )
    
    # --- Applicability ---
    category = models.CharField(
        max_length=20, blank=True, default='',
        choices=[('', 'All Categories')] + [
            ('PORK', 'Pork'), ('CHICKEN', 'Chicken'),
            ('BEEF', 'Beef'), ('LAMB', 'Lamb'),
            ('FISH', 'Fish'), ('OTHER', 'Other'),
        ],
        help_text="Product category this profile applies to (blank = all)"
    )
    
    # --- Notes ---
    notes = models.TextField(
        blank=True, default='',
        help_text="Documentation of assumptions and basis for this profile"
    )
    
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
        verbose_name = 'Thaw Profile'
        verbose_name_plural = 'Thaw Profiles'
    
    def __str__(self):
        return f"{self.name} ({self.default_duration})"
    
    @property
    def temperature_range_display(self):
        return f"{self.min_temperature}–{self.max_temperature}°C"
    
    @property
    def duration_range_display(self):
        return f"{self.minimum_duration} – {self.default_duration}"


class PlanStatus(models.TextChoices):
    """Status choices for RotationPlan."""
    DRAFT = 'DRAFT'
    PLANNED = 'PLANNED'
    READY = 'READY'
    IN_PROGRESS = 'IN_PROGRESS'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    AT_RISK = 'AT_RISK'
    OVERDUE = 'OVERDUE'


class RotationPlan(models.Model):
    """Central planning entity linking package to target ready time."""
    
    id = models.AutoField(primary_key=True)
    package = models.OneToOneField('inventory.Package', on_delete=models.PROTECT, related_name='rotation_plan')
    target_ready_at = models.DateTimeField(help_text="When package should be ready for sale")
    planned_thaw_start_at = models.DateTimeField(help_text="Calculated thaw start time")
    planned_thaw_queue_at = models.DateTimeField(help_text="When to add to thaw queue")
    planned_freeze_start_at = models.DateTimeField(help_text="When to start freezing")
    planned_freeze_end_at = models.DateTimeField(help_text="When freeze should complete")
    freeze_profile = models.ForeignKey(FreezeProfile, on_delete=models.PROTECT, related_name='rotation_plans')
    thaw_profile = models.ForeignKey(ThawProfile, on_delete=models.PROTECT, related_name='rotation_plans')
    freeze_duration = models.DurationField(help_text="Actual freeze duration used")
    thaw_duration = models.DurationField(help_text="Actual thaw duration used")
    freeze_override = models.DurationField(null=True, blank=True, help_text="Manual freeze duration override")
    thaw_override = models.DurationField(null=True, blank=True, help_text="Manual thaw duration override")
    override_reason = models.TextField(blank=True, default='')
    overridden_by = models.CharField(max_length=100, blank=True, default='')
    overridden_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, 
        choices=PlanStatus.choices, 
        default=PlanStatus.DRAFT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['target_ready_at']
        verbose_name = 'Rotation Plan'
        verbose_name_plural = 'Rotation Plans'
    
    def __str__(self):
        return f"Plan {self.id}: {self.package.display_name} → {self.target_ready_at}"


class QueueStatus(models.TextChoices):
    """Status choices for ThawQueueEntry."""
    QUEUED = 'QUEUED'
    READY_TO_START = 'READY_TO_START'
    STARTED = 'STARTED'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    OVERDUE = 'OVERDUE'


class ThawQueueEntry(models.Model):
    """Queue entry for thaw operations."""
    
    id = models.AutoField(primary_key=True)
    package = models.OneToOneField('inventory.Package', on_delete=models.PROTECT, related_name='thaw_queue_entry')
    rotation_plan = models.ForeignKey(RotationPlan, on_delete=models.PROTECT, related_name='queue_entries')
    queue_position = models.PositiveIntegerField(help_text="Position in queue (1 = first)")
    planned_start_at = models.DateTimeField(help_text="When thaw should start")
    target_ready_at = models.DateTimeField(help_text="Target ready time")
    status = models.CharField(
        max_length=20, 
        choices=QueueStatus.choices, 
        default=QueueStatus.QUEUED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['queue_position']
        verbose_name = 'Thaw Queue Entry'
        verbose_name_plural = 'Thaw Queue Entries'
    
    def __str__(self):
        return f"Queue #{self.queue_position}: {self.package.display_name}"
