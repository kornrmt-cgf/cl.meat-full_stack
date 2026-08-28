from django.urls import path
from . import views

app_name = 'planning'

urlpatterns = [
    path('', views.plan_list, name='plan_list'),
    path('monthly/', views.monthly_planner, name='monthly_planner'),
    path('queue/', views.queue_view, name='queue'),
    path('queue/<int:pk>/', views.queue_detail, name='queue_detail'),
    path('queue/<int:pk>/edit/', views.queue_edit, name='queue_edit'),
    path('create/', views.plan_create, name='plan_create'),
    path('<int:pk>/', views.plan_detail, name='plan_detail'),
    path('<int:pk>/edit/', views.plan_edit, name='plan_edit'),
]
