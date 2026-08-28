"""
Django Admin configuration for Inventory models.
"""
from django.contrib import admin
from .models import Product, Batch, Package, StorageLocation


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['sku', 'name', 'category', 'unit', 'active']
    list_filter = ['category', 'active']
    search_fields = ['sku', 'name', 'barcode']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ['batch_number', 'supplier', 'received_at', 'active']
    list_filter = ['active']
    search_fields = ['batch_number', 'supplier']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ['id', 'product', 'weight', 'batch', 'current_state', 'storage_location', 'packed_at']
    list_filter = ['current_state', 'product', 'batch']
    search_fields = ['barcode', 'product__name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['product', 'batch', 'storage_location']


@admin.register(StorageLocation)
class StorageLocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'location_type', 'capacity', 'active']
    list_filter = ['location_type', 'active']
    readonly_fields = ['created_at', 'updated_at']
