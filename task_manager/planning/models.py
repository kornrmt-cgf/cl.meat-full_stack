"""
Planning Models — rotation scheduling with configurable profiles.

Core entities:
- FreezeProfile: configurable freeze parameters
- ThawProfile: configurable thaw parameters with weight-based duration
- RotationCycle: one freeze-thaw-display cycle for a package
- RotationPlan: central plan linking a cycle to target ready time
- ThawQueueEntry: queue entries for thaw operations

Design: AUTO mode calculates from profiles, CUSTOM mode allows overrides.
RotationCycle enables repeated rotation cycles per package.
"""
from django.db import models
from datetime import timedelta
from decimal import Decimal


# ============================================================
# FREEZE PROFILE
# ============================================================

class FreezeProfile(models.Model):
    """Freeze profile — configurable operational rules for freezing."""

    name = models.CharField(max_length=100)
    target_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="Target temperature (°C)"
    )
    minimum_duration = models.DurationField(help_text="Minimum freeze duration")
    default_duration = models.DurationField(help_text="Default freeze duration")
    buffer_duration = models.DurationField(
        default=timedelta(hours=0), help_text="Buffer time added to duration"
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Freeze Profile'
        verbose_name_plural = 'Freeze Profiles'

    def __str__(self):
        return f"{self.name} ({self.target_temperature}°C)"


# ============================================================
# THAW PROFILE
# ============================================================

class ThawProfile(models.Model):
    """
    Thaw profile — configurable operational rules for thawing.

    Thaw times are CONFIGURABLE ESTIMATES, not universal safety rules.
    Duration is weight-based with interpolation between thresholds.

    The weight_threshold_kg determines which duration is used:
    - weight <= threshold → minimum_duration
    - threshold < weight <= 2× threshold → interpolated
    - weight > 2× threshold → default_duration × weight_scale_factor

    Safety buffer is always added on top.
    """

    name = models.CharField(max_length=100)

    # Duration configuration
    default_duration = models.DurationField(help_text="Default thaw duration")
    minimum_duration = models.DurationField(help_text="Minimum thaw duration")
    buffer_duration = models.DurationField(
        default=timedelta(hours=0), help_text="Safety buffer"
    )

    # Weight-based duration scaling
    weight_threshold_kg = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal('0.500'),
        help_text="Weight (kg) where min→default transition occurs"
    )
    weight_scale_factor = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('1.20'),
        help_text="Multiplier for packages > 2× threshold"
    )

    # Temperature configuration
    target_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('3.00'),
        help_text="Target thaw temperature (°C)"
    )
    min_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('1.00')
    )
    max_temperature = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('5.00')
    )

    # Capacity
    thaw_capacity = models.PositiveIntegerField(default=20)

    # Category applicability
    category = models.CharField(
        max_length=20, blank=True, default='',
        help_text="Product category code (blank = all)"
    )

    notes = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Thaw Profile'
        verbose_name_plural = 'Thaw Profiles'

    def __str__(self):
        return f"{self.name} ({self.default_duration})"


# ============================================================
# PLAN STATUS
# ============================================================

class PlanStatus(models.TextChoices):
    DRAFT = 'DRAFT'
    PLANNED = 'PLANNED'
    READY = 'READY'
    IN_PROGRESS = 'IN_PROGRESS'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    AT_RISK = 'AT_RISK'
    OVERDUE = 'OVERDUE'


# ============================================================
# ROTATION CYCLE
# ============================================================

class RotationCycle(models.Model):
    """
    One complete freeze-thaw-display cycle for a package.

    A package can have multiple cycles (refreeze → display → refreeze).
    Each cycle tracks timestamps for every phase.
    History is append-only; completed cycles are never overwritten.
    """

    CYCLE_STATUS_CHOICES = [
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]

    package = models.ForeignKey(
        'inventory.Package', on_delete=models.PROTECT, related_name='rotation_cycles'
    )
    cycle_number = models.PositiveIntegerField(
        help_text='Cycle number for this package (1, 2, 3, ...)'
    )
    status = models.CharField(
        max_length=20, choices=CYCLE_STATUS_CHOICES, default='IN_PROGRESS'
    )

    # Freeze timestamps
    freeze_started_at = models.DateTimeField(null=True, blank=True)
    freeze_completed_at = models.DateTimeField(null=True, blank=True)

    # Thaw timestamps
    thaw_started_at = models.DateTimeField(null=True, blank=True)
    thaw_completed_at = models.DateTimeField(null=True, blank=True)

    # Display timestamps
    display_started_at = models.DateTimeField(null=True, blank=True)
    display_ended_at = models.DateTimeField(null=True, blank=True)

    # Outcome
    OUTCOME_CHOICES = [
        ('', 'Pending'),
        ('SOLD', 'Sold'),
        ('DISCARDED', 'Discarded'),
        ('REFROZEN', 'Refrozen'),
        ('CANCELLED', 'Cancelled'),
    ]
    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES, default='')
    outcome_reason = models.TextField(blank=True, default='')
    outcome_actor = models.CharField(max_length=100, blank=True, default='')
    outcome_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['package', 'cycle_number']
        unique_together = ['package', 'cycle_number']
        verbose_name = 'Rotation Cycle'
        verbose_name_plural = 'Rotation Cycles'

    def __str__(self):
        return f"Cycle {self.cycle_number} for {self.package.display_name} ({self.status})"

    @property
    def duration_freeze(self):
        """Actual freeze duration if both timestamps exist."""
        if self.freeze_started_at and self.freeze_completed_at:
            return self.freeze_completed_at - self.freeze_started_at
        return None

    @property
    def duration_thaw(self):
        """Actual thaw duration if both timestamps exist."""
        if self.thaw_started_at and self.thaw_completed_at:
            return self.thaw_completed_at - self.thaw_started_at
        return None


# ============================================================
# ROTATION PLAN
# ============================================================

class RotationPlan(models.Model):
    """
    Central planning entity linking a package to its target ready time.

    Contains all calculated timings and supports manual overrides.
    Now references RotationCycle for multi-cycle support.
    """

    package = models.ForeignKey(
        'inventory.Package', on_delete=models.PROTECT, related_name='rotation_plans'
    )
    rotation_cycle = models.ForeignKey(
        RotationCycle, null=True, blank=True,
        on_delete=models.PROTECT, related_name='plans'
    )
    target_ready_at = models.DateTimeField(help_text="When package should be ready for sale")
    planned_thaw_start_at = models.DateTimeField(help_text="Calculated thaw start time")
    planned_thaw_queue_at = models.DateTimeField(help_text="When to add to thaw queue")
    planned_freeze_start_at = models.DateTimeField(help_text="When to start freezing")
    planned_freeze_end_at = models.DateTimeField(help_text="When freeze should complete")

    freeze_profile = models.ForeignKey(FreezeProfile, on_delete=models.PROTECT, related_name='rotation_plans')
    thaw_profile = models.ForeignKey(ThawProfile, on_delete=models.PROTECT, related_name='rotation_plans')

    freeze_duration = models.DurationField(help_text="Actual freeze duration used")
    thaw_duration = models.DurationField(help_text="Actual thaw duration used")

    # Manual overrides (CUSTOM mode)
    freeze_override = models.DurationField(null=True, blank=True, help_text="Manual freeze override")
    thaw_override = models.DurationField(null=True, blank=True, help_text="Manual thaw override")
    override_reason = models.TextField(blank=True, default='')
    overridden_by = models.CharField(max_length=100, blank=True, default='')
    overridden_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20, choices=PlanStatus.choices, default=PlanStatus.DRAFT
    )

    # Legacy linkage for migration
    legacy_rotation_schedule_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Reference to database_clmeat_main RotationSchedule.id'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['target_ready_at']
        verbose_name = 'Rotation Plan'
        verbose_name_plural = 'Rotation Plans'

    def __str__(self):
        return f"Plan {self.id}: {self.package.display_name} → {self.target_ready_at}"

    @property
    def is_override(self):
        return self.freeze_override is not None or self.thaw_override is not None


# ============================================================
# QUEUE STATUS
# ============================================================

class QueueStatus(models.TextChoices):
    QUEUED = 'QUEUED'
    READY_TO_START = 'READY_TO_START'
    STARTED = 'STARTED'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    OVERDUE = 'OVERDUE'


# ============================================================
# THAW QUEUE ENTRY
# ============================================================

class ThawQueueEntry(models.Model):
    """Queue entry for thaw operations."""

    package = models.ForeignKey(
        'inventory.Package', on_delete=models.PROTECT, related_name='thaw_queue_entries'
    )
    rotation_cycle = models.ForeignKey(
        RotationCycle, null=True, blank=True,
        on_delete=models.PROTECT, related_name='queue_entries'
    )
    rotation_plan = models.ForeignKey(
        RotationPlan, on_delete=models.PROTECT, related_name='queue_entries'
    )
    queue_position = models.PositiveIntegerField(help_text="Position in queue (1 = first)")
    planned_start_at = models.DateTimeField(help_text="When thaw should start")
    target_ready_at = models.DateTimeField(help_text="Target ready time")
    status = models.CharField(
        max_length=20, choices=QueueStatus.choices, default=QueueStatus.QUEUED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['queue_position']
        verbose_name = 'Thaw Queue Entry'
        verbose_name_plural = 'Thaw Queue Entries'

    def __str__(self):
        return f"Queue #{self.queue_position}: {self.package.display_name}"
