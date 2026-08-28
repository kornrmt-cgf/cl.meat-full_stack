from django.urls import path
from . import api

app_name = 'planning_api'

urlpatterns = [
    path('eligible-packages/', api.eligible_packages_api, name='eligible_packages'),
    path('stock-analysis/', api.stock_analysis_api, name='stock_analysis'),
    path('barcode-check/', api.barcode_check_api, name='barcode_check'),
    path('', api.plan_list_api, name='plan_list'),
    path('create/', api.plan_create_api, name='plan_create'),
    path('calendar/', api.plan_calendar_api, name='plan_calendar'),
    path('<int:pk>/', api.plan_detail_api, name='plan_detail'),
    path('<int:pk>/recalculate/', api.plan_recalculate_api, name='plan_recalculate'),
    path('queue/', api.queue_list_api, name='queue_list'),
    path('queue/add/', api.queue_add_api, name='queue_add'),
    path('queue/<int:pk>/remove/', api.queue_remove_api, name='queue_remove'),
    path('conflicts/', api.conflicts_api, name='conflicts'),
    path('dashboard/', api.planning_dashboard_api, name='planning_dashboard'),
]
