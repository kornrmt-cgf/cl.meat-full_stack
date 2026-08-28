from django.contrib import admin

from django.urls import path

from stock_meat import views
from stock_meat import freeze_queue
from stock_meat import finance
from stock_meat import processing
from stock_meat import dashboard
from stock_meat import electricity
from stock_meat import sold_items


urlpatterns = [

    path(
        'admin/',
        admin.site.urls
    ),

    # ========================================================
    # HOME
    # ========================================================

    path(
        '',
        views.home,
        name='home'
    ),

    # ========================================================
    # ADD PRODUCT INFO
    # ========================================================

    path(
        'add-product/',
        views.add_product,
        name='add_product'
    ),

    # ========================================================
    # BARCODE
    # ========================================================

    path(
        'api/next-barcode/<int:product_id>/',
        views.next_barcode,
        name='next_barcode'
    ),

    # ========================================================
    # PACK
    # ========================================================

    path(
        'api/pack-product/',
        views.pack_product,
        name='pack_product'
    ),

    # ========================================================
    # NIIMBOT
    # ========================================================

    path(
        'api/print-niimbot/',
        views.print_niimbot,
        name='print_niimbot'
    ),

    # ========================================================
    # LOYVERSE
    # ========================================================

    path(
        'export-loyverse/',
        views.export_loyverse_csv,
        name='export_loyverse_csv'
    ),

    path(
        'api/confirm-loyverse-sync/',
        views.confirm_loyverse_sync,
        name='confirm_loyverse_sync'
    ),

    # ========================================================
    # BULK PRICE / PROMOTION
    # ========================================================

    path(
        'api/bulk-update-prices/',
        views.bulk_update_prices,
        name='bulk_update_prices'
    ),

    path(
        'api/undo-bulk-prices/',
        views.undo_bulk_prices,
        name='undo_bulk_prices'
    ),

    path(
        'get-meat-parts/',
        views.get_meat_parts,
        name='get_meat_parts'
    ),

    # ========================================================
    # FREEZE QUEUE MANAGEMENT
    # ========================================================

    path(
        'freeze-queue/',
        views.freeze_queue_page,
        name='freeze_queue_page'
    ),

    path(
        'api/freeze-queue/',
        freeze_queue.freeze_dashboard,
        name='freeze_dashboard'
    ),

    path(
        'api/start-thaw/',
        freeze_queue.start_thaw,
        name='start_thaw'
    ),

    path(
        'api/complete-thaw/',
        freeze_queue.complete_thaw,
        name='complete_thaw'
    ),

    path(
        'api/pull-from-display/',
        freeze_queue.pull_from_display,
        name='pull_from_display'
    ),

    path(
        'api/auto-rotation-check/',
        freeze_queue.auto_rotation_check,
        name='auto_rotation_check'
    ),

    path(
        'api/update-thaw-queue/',
        freeze_queue.update_thaw_queue,
        name='update_thaw_queue'
    ),

    path(
        'api/bulk-start-thaw/',
        freeze_queue.bulk_start_thaw,
        name='bulk_start_thaw'
    ),

    path(
        'api/rotation-history/',
        freeze_queue.rotation_history,
        name='rotation_history'
    ),

    path(
        'api/freeze-available-products/',
        freeze_queue.freeze_available_products,
        name='freeze_available_products'
    ),

    path(
        'api/add-to-queue/',
        freeze_queue.add_to_queue,
        name='add_to_queue'
    ),


    path(
        'api/pending-products/',
        freeze_queue.pending_products,
        name='pending_products'
    ),

    path(
        'api/set-product-status/',
        freeze_queue.set_product_status,
        name='set_product_status'
    ),

    path(
        'api/add-to-thaw-queue/',
        freeze_queue.add_to_thaw_queue,
        name='add_to_thaw_queue'
    ),

    path(
        'api/schedule-thaw/',
        freeze_queue.schedule_thaw,
        name='schedule_thaw'
    ),

    path(
        'api/create-rotation-plan/',
        freeze_queue.create_rotation_plan,
        name='create_rotation_plan'
    ),

    path(
        'api/worker-tasks/',
        freeze_queue.worker_tasks,
        name='worker_tasks'
    ),

    path(
        'api/complete-task/',
        freeze_queue.complete_task,
        name='complete_task'
    ),

    path(
        'api/rotation-plans/',
        freeze_queue.rotation_plans,
        name='rotation_plans'
    ),

    # ========================================================
    # SOLD ITEMS (Loyverse Receipt Sync)
    # ========================================================

    path(
        'sold-items/',
        sold_items.sold_items_page,
        name='sold_items_page'
    ),

    path(
        'api/sold-items/sync/',
        sold_items.sync_loyverse_receipts,
        name='sold_items_sync'
    ),

    path(
        'api/sold-items/list/',
        sold_items.sold_items_list,
        name='sold_items_list'
    ),

    path(
        'api/sold-items/summary/',
        sold_items.sold_items_summary,
        name='sold_items_summary'
    ),

    # ========================================================
    # FINANCE (Income & Expense)
    # ========================================================

    path(
        'finance/',
        finance.finance_page,
        name='finance_page'
    ),

    path(
        'api/finance/categories/',
        finance.get_categories,
        name='finance_categories'
    ),

    path(
        'api/finance/add-category/',
        finance.add_category,
        name='finance_add_category'
    ),

    path(
        'api/finance/add-transaction/',
        finance.add_transaction,
        name='finance_add_transaction'
    ),

    path(
        'api/finance/delete-transaction/',
        finance.delete_transaction,
        name='finance_delete_transaction'
    ),

    path(
        'api/finance/transactions/',
        finance.list_transactions,
        name='finance_list_transactions'
    ),

    path(
        'api/finance/summary/',
        finance.get_summary,
        name='finance_summary'
    ),

    # ========================================================
    # PROCESSING ZONE
    # ========================================================

    path(
        'processing/',
        processing.processing_page,
        name='processing_page'
    ),

    path(
        'api/processing/types/',
        processing.get_process_types,
        name='processing_types'
    ),

    path(
        'api/processing/add-type/',
        processing.add_process_type,
        name='processing_add_type'
    ),

    path(
        'api/processing/submit/',
        processing.submit_processing,
        name='processing_submit'
    ),

    path(
        'api/processing/list/',
        processing.list_processing,
        name='processing_list'
    ),

    path(
        'api/processing/products/',
        processing.get_processable_products,
        name='processing_products'
    ),

    # ========================================================
    # DASHBOARD
    # ========================================================

    path(
        'dashboard/',
        dashboard.dashboard_page,
        name='dashboard_page'
    ),

    path(
        'api/dashboard/data/',
        dashboard.get_dashboard_data,
        name='dashboard_data'
    ),

    # ========================================================
    # ELECTRICITY
    # ========================================================

    path(
        'electricity/',
        electricity.electricity_page,
        name='electricity_page'
    ),

    path(
        'api/electricity/list/',
        electricity.list_electricity_bills,
        name='electricity_list'
    ),

    path(
        'api/electricity/add/',
        electricity.add_electricity_bill,
        name='electricity_add'
    ),


    path(
        'api/electricity/latest-meter/',
        electricity.latest_meter,
        name='electricity_latest_meter'
    ),

    path(
        'api/electricity/delete/',
        electricity.delete_electricity_bill,
        name='electricity_delete'
    ),

    path(
        'api/electricity/daily/',
        electricity.daily_electricity_list,
        name='electricity_daily'
    ),
    path(
        'api/electricity/daily/add/',
        electricity.daily_electricity_add,
        name='electricity_daily_add'
    ),
    path(
        'api/electricity/daily/delete/',
        electricity.daily_electricity_delete,
        name='electricity_daily_delete'
    ),

    # ========================================================
    # MEAT PARTS PRICE DASHBOARD
    # ========================================================

    path(
        'meat-prices/',
        views.meat_prices_page,
        name='meat_prices_page'
    ),

    path(
        'api/meat-prices/',
        dashboard.meat_parts_prices,
        name='meat_parts_prices'
    ),
]