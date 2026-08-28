"""
URL configuration สำหรับ tasks app
"""

from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    # Views หลัก
    path("", views.TaskListView.as_view(), name="list"),
    path("today/", views.TodayView.as_view(), name="today"),
    path("tomorrow/", views.TomorrowView.as_view(), name="tomorrow"),
    path("create/", views.TaskCreateView.as_view(), name="create"),
    path("<int:pk>/", views.TaskDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.TaskUpdateView.as_view(), name="edit"),
    path("<int:pk>/assign/", views.TaskAssignView.as_view(), name="assign"),

    # Open Tasks
    path("open/", views.OpenTasksView.as_view(), name="open_tasks"),
    path("<int:pk>/claim/", views.TaskClaimView.as_view(), name="claim"),

    # Actions
    path("<int:pk>/accept/", views.TaskAcceptView.as_view(), name="accept"),
    path("<int:pk>/start/", views.TaskStartView.as_view(), name="start"),
    path("<int:pk>/complete/", views.TaskCompleteView.as_view(), name="complete"),
    path("<int:pk>/problem/", views.TaskProblemView.as_view(), name="problem"),
    path("<int:pk>/error/", views.TaskErrorView.as_view(), name="error"),
    path("<int:pk>/postpone/", views.TaskPostponeView.as_view(), name="postpone"),
    path("<int:pk>/cancel/", views.TaskCancelView.as_view(), name="cancel"),

    # HTMX endpoints
    path("htmx/list/", views.TaskListHTMXView.as_view(), name="htmx-list"),
    path("htmx/today/", views.TodayHTMXView.as_view(), name="htmx-today"),
]
