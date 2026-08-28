from django.urls import path
from . import api

app_name = 'inventory_api'

urlpatterns = [
    path('', api.package_list_api, name='package_list'),
    path('<int:pk>/', api.package_detail_api, name='package_detail'),
    path('create/', api.package_create_api, name='package_create'),
    path('<int:pk>/timeline/', api.package_timeline_api, name='package_timeline'),
]
