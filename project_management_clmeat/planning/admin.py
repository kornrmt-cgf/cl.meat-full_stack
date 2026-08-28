"""
Django Admin configuration for Planning models.
"""
from django.contrib import admin
from .models import FreezeProfile, ThawProfile, RotationPlan, ThawQueueEntry


@admin.register(FreezeProfile)
class FreezeProfileAdmin(admin.ModelAdmin):
    list_display = ['name', 'target_temperature', 'minimum_duration', 'default_duration', 'active']
    list_filter = ['active']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ThawProfile)
class ThawProfileAdmin(admin.ModelAdmin):
    list_display = ['name', 'default_duration', 'minimum_duration', 'active']
    list_filter = ['active']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(RotationPlan)
class RotationPlanAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'package', 'target_ready_at', 'status',
        'planned_thaw_start_at', 'planned_freeze_start_at'
    ]
    list_filter = ['status', 'freeze_profile', 'thaw_profile']
    search_fields = ['package__product__name', 'package__barcode']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['package', 'freeze_profile', 'thaw_profile']


@admin.register(ThawQueueEntry)
class ThawQueueEntryAdmin(admin.ModelAdmin):
    list_display = ['queue_position', 'package', 'status', 'planned_start_at', 'target_ready_at']
    list_filter = ['status']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['package', 'rotation_plan']
