from django.urls import path
from . import views

app_name = 'worker'

urlpatterns = [
    path('', views.worker_home, name='scan'),
    path('urgent/', views.worker_urgent, name='urgent'),
    path('today/', views.worker_today, name='today'),
]
