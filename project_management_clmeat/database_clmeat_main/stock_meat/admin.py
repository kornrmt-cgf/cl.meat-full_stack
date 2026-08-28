from django.contrib import admin
from .models import (
    Category,
    Supply_meat,
    meat_parts,
    LoyverseSyncBatch,
    Product_info,
    Product_list,
    PriceChangeHistory,
    FreezeRotation,
    ExpenseCategory,
    Transaction,
    ProcessType,
    ProductProcessing,
    ElectricityBill,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['ids', 'name_type']
    search_fields = ['name_type']


@admin.register(Supply_meat)
class SupplyMeatAdmin(admin.ModelAdmin):
    list_display = ['ids', 'name_place', 'locations']
    search_fields = ['name_place']


@admin.register(meat_parts)
class MeatPartsAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'prefix_barcode', 'kcalories', 'protent', 'fat']
    list_filter = ['category']
    search_fields = ['name']


@admin.register(LoyverseSyncBatch)
class LoyverseSyncBatchAdmin(admin.ModelAdmin):
    list_display = ['id', 'confirmed_at', 'item_count']
    readonly_fields = ['confirmed_at']


@admin.register(Product_info)
class ProductInfoAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'name', 'type_product', 'import_from',
        'lot_number', 'weight', 'cost', 'selling_price_per_kg',
        'max_display_count', 'created_at',
    ]
    list_filter = ['type_product', 'import_from']
    search_fields = ['name__name', 'lot_number']
    list_editable = ['cost', 'selling_price_per_kg', 'max_display_count']


@admin.register(Product_list)
class ProductListAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'barcode', 'product', 'weight', 'selling_price',
        'storage_status', 'thaw_queue_position', 'loyverse_sku',
        'loyverse_synced', 'mfg',
    ]
    list_filter = ['storage_status', 'loyverse_synced', 'activated']
    search_fields = ['barcode', 'loyverse_sku']
    list_editable = ['selling_price', 'storage_status']


@admin.register(PriceChangeHistory)
class PriceChangeHistoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'product_list', 'old_price', 'new_price', 'mode', 'created_at', 'undone_at']
    list_filter = ['mode']


@admin.register(FreezeRotation)
class FreezeRotationAdmin(admin.ModelAdmin):
    list_display = ['id', 'product_list', 'action', 'performed_at', 'weight_at_action', 'status_before', 'status_after']
    list_filter = ['action']
    search_fields = ['product_list__barcode']


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'category_type', 'icon', 'is_active']
    list_filter = ['category_type', 'is_active']
    list_editable = ['is_active']


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'transaction_type', 'amount', 'category',
        'description', 'payment_method', 'receipt_date',
        'receipt_number', 'created_at',
    ]
    list_filter = ['transaction_type', 'payment_method', 'category']
    search_fields = ['description', 'receipt_number']
    list_editable = ['amount']


@admin.register(ProcessType)
class ProcessTypeAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'output_price_per_kg', 'is_active']
    list_editable = ['output_price_per_kg', 'is_active']


@admin.register(ProductProcessing)
class ProductProcessingAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'product_list', 'action', 'process_type',
        'input_weight', 'output_weight', 'yield_percent',
        'processed_at',
    ]
    list_filter = ['action', 'process_type']
    search_fields = ['product_list__barcode']
    readonly_fields = ['created_at']


@admin.register(ElectricityBill)
class ElectricityBillAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'month', 'year', 'units_used', 'total_amount',
        'meter_reading', 'previous_reading', 'created_at',
    ]
    list_filter = ['year', 'month']
    readonly_fields = ['total_amount', 'created_at']
