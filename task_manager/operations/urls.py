"""
URL configuration สำหรับ operations app — Worker Operations
"""
from django.urls import path

from . import views

app_name = 'operations'

urlpatterns = [
    # Task board
    path('', views.WorkerTaskListView.as_view(), name='task-list'),
    path('<int:pk>/', views.WorkerTaskDetailView.as_view(), name='task-detail'),

    # Task actions
    path('<int:pk>/claim/', views.WorkerClaimTaskView.as_view(), name='task-claim'),
    path('<int:pk>/start/', views.WorkerStartTaskView.as_view(), name='task-start'),
    path('<int:pk>/complete/', views.WorkerCompleteTaskView.as_view(), name='task-complete'),
    path('<int:pk>/cancel/', views.WorkerCancelTaskView.as_view(), name='task-cancel'),

    # History
    path('history/', views.WorkerTaskHistoryView.as_view(), name='task-history'),

    # AJAX endpoints
    path('ajax/scan/', views.BarcodeScanView.as_view(), name='barcode-scan'),
    path('ajax/task/<int:pk>/status/', views.TaskStatusAJAXView.as_view(), name='task-status-ajax'),
    path('ajax/tasks/count/', views.TaskListAJAXView.as_view(), name='task-list-ajax'),
]
