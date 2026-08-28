from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('', views.user_list, name='user_list'),
    path('create/', views.user_create, name='user_create'),
    path('<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('<int:pk>/reset-password/', views.user_reset_password, name='user_reset_password'),
    path('<int:pk>/toggle-active/', views.user_toggle_active, name='user_toggle_active'),
]
