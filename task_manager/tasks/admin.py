"""
Admin configuration สำหรับ tasks app
"""

from django.contrib import admin

from .models import Task, TaskActivity, TaskAssignment, TaskDependency, TaskReport


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "priority", "category", "deadline", "created_by")
    list_filter = ("status", "priority", "category")
    search_fields = ("title", "description")
    raw_id_fields = ("created_by", "team")


@admin.register(TaskAssignment)
class TaskAssignmentAdmin(admin.ModelAdmin):
    list_display = ("task", "assigned_to", "assigned_by", "accepted_at", "started_at", "completed_at")
    raw_id_fields = ("task", "assigned_to", "assigned_by")


@admin.register(TaskActivity)
class TaskActivityAdmin(admin.ModelAdmin):
    list_display = ("task", "user", "action", "created_at")
    list_filter = ("action",)
    raw_id_fields = ("task", "user")


@admin.register(TaskReport)
class TaskReportAdmin(admin.ModelAdmin):
    list_display = ("task", "reported_by", "report_type", "title", "resolved")
    list_filter = ("report_type", "resolved")
    raw_id_fields = ("task", "reported_by")


@admin.register(TaskDependency)
class TaskDependencyAdmin(admin.ModelAdmin):
    list_display = ("task", "depends_on")
    raw_id_fields = ("task", "depends_on")
