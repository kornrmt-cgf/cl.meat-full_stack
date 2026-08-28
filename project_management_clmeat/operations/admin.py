"""
Django Admin configuration for Operations models.
"""
from django.contrib import admin
from .models import WorkerTask, TaskEvent, RotationEvent


@admin.register(WorkerTask)
class WorkerTaskAdmin(admin.ModelAdmin):
    list_display = ['id', 'package', 'task_type', 'scheduled_at', 'status', 'completed_at']
    list_filter = ['status', 'task_type']
    search_fields = ['package__product__name', 'package__barcode']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['package', 'rotation_plan']


@admin.register(TaskEvent)
class TaskEventAdmin(admin.ModelAdmin):
    list_display = ['id', 'task', 'event_type', 'timestamp', 'actor']
    list_filter = ['event_type']
    readonly_fields = ['created_at']
    raw_id_fields = ['task']


@admin.register(RotationEvent)
class RotationEventAdmin(admin.ModelAdmin):
    list_display = ['id', 'package', 'event_type', 'from_state', 'to_state', 'timestamp', 'actor']
    list_filter = ['event_type', 'from_state', 'to_state']
    search_fields = ['package__product__name', 'actor']
    readonly_fields = ['created_at']
    raw_id_fields = ['package']
