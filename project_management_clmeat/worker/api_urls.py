from django.urls import path
from . import api

app_name = 'worker_api'

urlpatterns = [
    path('scan/', api.scan_barcode, name='scan'),
    path('action/', api.execute_action, name='action'),
    path('urgent/', api.urgent_tasks, name='urgent'),
    path('stats/', api.worker_stats, name='stats'),
]
