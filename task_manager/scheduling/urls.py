"""
URL configuration สำหรับ scheduling app
"""

from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    # Employee views
    path("week/", views.WeekView.as_view(), name="week"),

    # Manager views
    path("schedule/", views.ManagerScheduleView.as_view(), name="manager-schedule"),
    path("schedule/week/", views.ManagerWeekView.as_view(), name="manager-week"),

    # HTMX endpoints
    path("htmx/reorder/<int:pk>/", views.ReorderHTMXView.as_view(), name="htmx-reorder"),
    path("htmx/reschedule/<int:pk>/", views.RescheduleHTMXView.as_view(), name="htmx-reschedule"),
    path("htmx/conflict-check/", views.ConflictCheckHTMXView.as_view(), name="htmx-conflict-check"),
    path("htmx/generate-recurring/", views.GenerateRecurringView.as_view(), name="htmx-generate-recurring"),

    # TaskTemplate views
    path("templates/", views.TaskTemplateListView.as_view(), name="template-list"),
    path("templates/create/", views.TaskTemplateCreateView.as_view(), name="template-create"),
    path("templates/<int:pk>/edit/", views.TaskTemplateUpdateView.as_view(), name="template-edit"),
    path("templates/<int:pk>/delete/", views.TaskTemplateDeleteView.as_view(), name="template-delete"),
    path("templates/<int:pk>/create-task/", views.TaskTemplateCreateTaskView.as_view(), name="template-create-task"),
]
