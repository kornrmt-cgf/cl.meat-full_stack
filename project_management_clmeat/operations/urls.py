from django.urls import path
from . import views

app_name = 'operations'

urlpatterns = [
    path('today/', views.today_view, name='today'),
    path('history/', views.history_view, name='history'),
    path('tasks/<int:pk>/', views.task_detail, name='task_detail'),
    path('tasks/<int:pk>/complete/', views.task_complete, name='task_complete'),
]
