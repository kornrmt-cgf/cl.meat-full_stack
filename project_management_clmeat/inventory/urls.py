from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    path('products/<int:pk>/edit/', views.product_edit, name='product_edit'),
    path('', views.package_list, name='package_list'),
    path('packages/<int:pk>/', views.package_detail, name='package_detail'),
    path('packages/create/', views.package_create, name='package_create'),
    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.product_create, name='product_create'),
    path('batches/', views.batch_list, name='batch_list'),
    path('batches/create/', views.batch_create, name='batch_create'),
    path('locations/', views.location_list, name='location_list'),
    path('locations/create/', views.location_create, name='location_create'),
]
