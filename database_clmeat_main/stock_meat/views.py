from django.shortcuts import render, redirect

from django.utils import timezone

from django.http import (
    JsonResponse,
    HttpResponse,
)

from django.db import (
    transaction,
)

from django.db.models import (
    Max,
)

from django.views.decorators.http import (
    require_GET,
    require_POST,
)

try:
    from stock_meat.niimbot import (
        NIIMBOTController,
    )
except ImportError:
    NIIMBOTController = None

from stock_meat.models import (
    Product_info,
    Product_list,
    LoyverseSyncBatch,
    PriceChangeHistory,
    Transaction,
    ExpenseCategory,
    meat_parts,
)

from stock_meat.forms import (
    ProductInfoForm,
)

from stock_meat.loyverse_export import (
    generate_loyverse_csv,
)

import json
import math


niimbot_col = NIIMBOTController() if NIIMBOTController else None


# ============================================================
# HOME
# ============================================================

def home(request):

    # --------------------------------------------------------
    # สินค้าทั้งหมด
    # --------------------------------------------------------

    products = (
        Product_list.objects
        .select_related(
            'product',
            'product__name',
            'product__type_product',
            'product__import_from',
            'loyverse_sync_batch',
        )
        .order_by('-mfg')
    )

    # --------------------------------------------------------
    # รายการสินค้าที่เปิดใช้งานสำหรับจัดโปร/แก้ราคา
    # --------------------------------------------------------

    price_products = (
        products
        .filter(
            activated=True,
        )
    )

    # --------------------------------------------------------
    # เฉพาะสินค้าที่ยังไม่ Sync
    # --------------------------------------------------------

    pending_products = (
        products
        .filter(
            loyverse_synced=False,
            activated=True,
        )
    )

    # --------------------------------------------------------
    # Product_info ที่ยังมี Stock
    # --------------------------------------------------------

    source_products = (
        Product_info.objects
        .select_related(
            'name',
            'type_product',
            'import_from',
        )
        .filter(
            weight__gt=0
        )
        .order_by(
            'id'
        )
    )

    # --------------------------------------------------------
    # ประวัติ Sync
    # --------------------------------------------------------

    sync_batches = (
        LoyverseSyncBatch.objects
        .prefetch_related(
            'products',
            'products__product',
            'products__product__name',
        )
        .order_by(
            '-confirmed_at'
        )
    )

    context = {

        'products':
            products,

        'price_products':
            price_products,

        'pending_products':
            pending_products,

        'source_products':
            source_products,

        'sync_batches':
            sync_batches,

        'pending_count':
            pending_products.count(),

        'synced_count':
            products.filter(
                loyverse_synced=True
            ).count(),
    }

    return render(
        request,
        'home.html',
        context
    )


# ============================================================
# ADD PRODUCT INFO
# ============================================================

def add_product(request):

    if request.method == 'POST':

        form = ProductInfoForm(
            request.POST
        )

        if form.is_valid():

            product = form.save(
                commit=False
            )

            # ------------------------------------------------
            # Auto Lot
            #
            # ใช้:
            # ชิ้นส่วน + ร้านนำเข้า
            # ------------------------------------------------

            last_lot = (
                Product_info.objects
                .filter(
                    name=product.name,
                    import_from=product.import_from,
                )
                .aggregate(
                    max_lot=Max(
                        'lot_number'
                    )
                )
                .get(
                    'max_lot'
                )
            )

            if last_lot is None:

                product.lot_number = 1

            else:

                product.lot_number = (
                    last_lot + 1
                )

            product.save()

            # --------------------------------------------
            # Auto-add expense for meat purchase
            # --------------------------------------------

            cost = float(product.cost or 0)
            weight_kg = float(product.weight or 0) / 1000

            if cost > 0 and weight_kg > 0:
                total_cost = cost * weight_kg

                # Find or create ค่าเนื้อ category
                meat_cat = ExpenseCategory.objects.filter(
                    name__icontains='เนื้อ',
                    category_type='expense',
                ).first()

                part_name = ''
                source_name = ''
                if product.name:
                    part_name = product.name.name
                if product.import_from:
                    source_name = product.import_from.name_place

                Transaction.objects.create(
                    transaction_type='expense',
                    amount=total_cost,
                    category=meat_cat,
                    description=(
                        f'ซื้อ{part_name} '
                        f'{weight_kg:.2f}kg '
                        f'จาก{source_name} '
                        f'(Lot {product.lot_number})'
                    ),
                    receipt_date=timezone.now().date(),
                    notes=(
                        f'Auto: เพิ่ม Product Info #{product.id}'
                    ),
                )

            return redirect(
                'home'
            )

    else:

        form = ProductInfoForm()

    # --------------------------------------------------------
    # คำนวณ Lot ที่จะใช้แสดงหน้าเว็บ
    # --------------------------------------------------------

    context = {

        'product_info_form':
            form,
    }

    return render(
        request,
        'add_product.html',
        context
    )


# ============================================================
# CONFIRM LOYVERSE SYNC
# ============================================================

@require_POST
def confirm_loyverse_sync(request):

    selected_ids = (
        request.POST.getlist(
            'product_ids'
        )
    )

    if not selected_ids:

        return JsonResponse(
            {
                'success': False,
                'message':
                    'กรุณาเลือกรายการก่อน'
            },
            status=400
        )

    with transaction.atomic():

        # ----------------------------------------------------
        # เอาเฉพาะรายการที่ยังไม่ได้ Sync
        # ----------------------------------------------------

        products = list(
            Product_list.objects
            .select_for_update()
            .filter(
                id__in=selected_ids,
                loyverse_synced=False,
                activated=True,
            )
        )

        if not products:

            return JsonResponse(
                {
                    'success': False,
                    'message':
                        'ไม่พบรายการที่รอ Sync'
                },
                status=400
            )

        # ----------------------------------------------------
        # สร้าง Folder / Batch
        # ----------------------------------------------------

        batch = (
            LoyverseSyncBatch.objects.create()
        )

        now = timezone.now()

        # ----------------------------------------------------
        # อัปเดต Product_list
        # ----------------------------------------------------

        for product in products:

            product.loyverse_synced = True

            product.loyverse_synced_at = now

            product.loyverse_sync_batch = batch

            product.save(
                update_fields=[
                    'loyverse_synced',
                    'loyverse_synced_at',
                    'loyverse_sync_batch',
                ]
            )

    return JsonResponse(
        {
            'success': True,

            'message':
                'ยืนยันการ Sync เรียบร้อย',

            'batch_id':
                batch.id,

            'count':
                len(products),
        }
    )


# ============================================================
# EXPORT LOYVERSE CSV FROM WEB
# ============================================================

@require_GET
def export_loyverse_csv(request):
    """
    Backward compatible:
        /export-loyverse/                 -> pending (เหมือนเดิม)
        /export-loyverse/?scope=synced   -> รายการ Sync แล้ว
        /export-loyverse/?scope=all      -> ทั้งหมด
        /export-loyverse/?ids=1,2,3      -> เฉพาะรายการที่เลือก
    """

    scope = request.GET.get("scope", "pending")

    if scope not in {"pending", "synced", "all"}:
        scope = "pending"

    raw_ids = request.GET.get("ids", "")
    product_ids = []

    if raw_ids:
        for value in raw_ids.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                product_ids.append(int(value))
            except ValueError:
                continue

    csv_content, count = generate_loyverse_csv(
        product_ids=product_ids or None,
        scope=scope,
    )

    response = HttpResponse(
        "\ufeff" + csv_content,
        content_type="text/csv; charset=utf-8",
    )

    if product_ids:
        filename = "loyverse_selected.csv"
    elif scope == "synced":
        filename = "loyverse_synced.csv"
    elif scope == "all":
        filename = "loyverse_all.csv"
    else:
        filename = "loyverse_products.csv"

    response["Content-Disposition"] = (
        "attachment; "
        f'filename="{filename}"'
    )

    return response


# ============================================================
# BULK SALE PRICE / PROMOTION
# ============================================================

def calculate_package_price(product_list, mode, value):
    """
    mode=cost_margin:
        ราคาขาย = ต้นทุนปัจจุบัน/kg
                 × น้ำหนัก/1000
                 × (1 + margin%)

    mode=discount:
        ราคาขาย = ราคาขายเดิมต่อแพ็ค
                 × (1 - discount%)

    mode=price_per_kg:
        ราคาขาย = ราคาที่กำหนด/kg
                 × น้ำหนัก/1000

    ใช้ math.ceil เหมือนระบบแพ็คเดิม
    """

    weight = float(product_list.weight or 0)

    if weight <= 0:
        return 0

    if mode == "cost_margin":
        cost_per_kg = float(product_list.product.cost or 0)
        price = (
            cost_per_kg
            * (weight / 1000)
            * (1 + value / 100)
        )

    elif mode == "discount":
        current_price = float(product_list.selling_price or 0)
        price = current_price * (1 - value / 100)

    elif mode == "price_per_kg":
        price = value * (weight / 1000)

    else:
        raise ValueError("ไม่รู้จักรูปแบบการคำนวณราคา")

    return max(0, math.ceil(price))


@require_POST
def bulk_update_prices(request):
    """
    เปลี่ยนราคาขายหลายรายการพร้อมกัน
    รองรับทั้งสินค้าที่ Sync แล้วและยังไม่ Sync

    POST:
        product_ids[] = Product_list IDs
        mode = cost_margin | discount | price_per_kg
        value = ตัวเลขตาม mode
    """

    selected_ids = request.POST.getlist("product_ids")
    mode = request.POST.get("mode", "")
    value_raw = request.POST.get("value", "")

    if not selected_ids:
        return JsonResponse(
            {
                "success": False,
                "message": "กรุณาเลือกรายการสินค้า",
            },
            status=400,
        )

    try:
        value = float(value_raw)
    except (TypeError, ValueError):
        return JsonResponse(
            {
                "success": False,
                "message": "ค่าที่ใช้คำนวณราคาไม่ถูกต้อง",
            },
            status=400,
        )

    if mode == "discount":
        if value < 0 or value > 100:
            return JsonResponse(
                {
                    "success": False,
                    "message": "ส่วนลดต้องอยู่ระหว่าง 0-100%",
                },
                status=400,
            )

    elif mode == "cost_margin":
        if value < -100 or value > 1000:
            return JsonResponse(
                {
                    "success": False,
                    "message":
                        "กำไร/ส่วนเพิ่มต้องอยู่ระหว่าง -100 ถึง 1000%",
                },
                status=400,
            )

    elif mode == "price_per_kg":
        if value < 0:
            return JsonResponse(
                {
                    "success": False,
                    "message": "ราคาขาย/kg ต้องไม่ติดลบ",
                },
                status=400,
            )

    else:
        return JsonResponse(
            {
                "success": False,
                "message": "รูปแบบการคำนวณราคาไม่ถูกต้อง",
            },
            status=400,
        )

    try:
        ids = [int(value) for value in selected_ids]
    except ValueError:
        return JsonResponse(
            {
                "success": False,
                "message": "พบ Product ID ไม่ถูกต้อง",
            },
            status=400,
        )

    with transaction.atomic():
        products = list(
            Product_list.objects
            .select_for_update()
            .select_related(
                "product",
                "product__name",
            )
            .filter(
                id__in=ids,
                activated=True,
                product__isnull=False,
            )
        )

        if not products:
            return JsonResponse(
                {
                    "success": False,
                    "message": "ไม่พบรายการสินค้าที่เลือก",
                },
                status=404,
            )

        updated = 0
        total_before = 0
        total_after = 0
        preview = []

        for product_list in products:
            old_price = float(product_list.selling_price or 0)

            new_price = calculate_package_price(
                product_list,
                mode,
                value,
            )

            # เก็บประวัติราคาก่อนเปลี่ยน เพื่อให้ Undo ได้
            PriceChangeHistory.objects.create(
                product_list=product_list,
                old_price=old_price,
                new_price=new_price,
                mode=mode,
                value=value,
            )

            product_list.selling_price = new_price
            product_list.save(update_fields=["selling_price"])

            total_before += old_price
            total_after += new_price
            updated += 1

            preview.append(
                {
                    "id": product_list.id,
                    "barcode": product_list.barcode,
                    "weight": float(product_list.weight or 0),
                    "cost_per_kg": float(
                        product_list.product.cost or 0
                    ),
                    "old_price": old_price,
                    "new_price": float(new_price),
                }
            )

    mode_labels = {
        "cost_margin": "คำนวณจากต้นทุนปัจจุบัน + % กำไร",
        "discount": "ลดจากราคาขายเดิม",
        "price_per_kg": "กำหนดราคาขายต่อกิโล",
    }

    return JsonResponse(
        {
            "success": True,
            "message": f"ปรับราคา {updated} รายการเรียบร้อย",
            "count": updated,
            "mode": mode,
            "mode_label": mode_labels.get(mode, mode),
            "value": value,
            "total_before": total_before,
            "total_after": total_after,
            "preview": preview,
        }
    )


# ============================================================
# UNDO BULK PRICE / PROMOTION
# ============================================================

@require_POST
def undo_bulk_prices(request):
    """
    ย้อนราคาของรายการที่เลือกกลับไปเป็นราคาก่อนการปรับครั้งล่าสุด

    จะหา PriceChangeHistory ที่ยังไม่ถูก Undo ล่าสุดของแต่ละ Product_list
    และคืน selling_price กลับไปที่ old_price
    """

    selected_ids = request.POST.getlist("product_ids")

    if not selected_ids:
        return JsonResponse(
            {
                "success": False,
                "message": "กรุณาเลือกรายการสินค้าที่ต้องการ Undo",
            },
            status=400,
        )

    try:
        ids = [int(value) for value in selected_ids]
    except (TypeError, ValueError):
        return JsonResponse(
            {
                "success": False,
                "message": "พบ Product ID ไม่ถูกต้อง",
            },
            status=400,
        )

    restored = []

    with transaction.atomic():

        products = {
            product.id: product
            for product in Product_list.objects
            .select_for_update()
            .filter(id__in=ids)
        }

        for product_id in ids:

            product = products.get(product_id)

            if not product:
                continue

            history = (
                PriceChangeHistory.objects
                .select_for_update()
                .filter(
                    product_list_id=product_id,
                    undone_at__isnull=True,
                )
                .order_by("-created_at", "-id")
                .first()
            )

            if not history:
                continue

            current_price = float(product.selling_price or 0)
            restore_price = float(history.old_price or 0)

            product.selling_price = restore_price
            product.save(update_fields=["selling_price"])

            history.undone_at = timezone.now()
            history.save(update_fields=["undone_at"])

            restored.append(
                {
                    "id": product.id,
                    "barcode": product.barcode,
                    "old_price": current_price,
                    "restored_price": restore_price,
                }
            )

    if not restored:
        return JsonResponse(
            {
                "success": False,
                "message": "รายการที่เลือกไม่มีประวัติราคาที่สามารถ Undo ได้",
            },
            status=400,
        )

    return JsonResponse(
        {
            "success": True,
            "message": f"Undo ราคา {len(restored)} รายการเรียบร้อย",
            "count": len(restored),
            "restored": restored,
        }
    )


# ============================================================
# NEXT BARCODE
# ============================================================

@require_GET
def next_barcode(
    request,
    product_id
):

    try:

        product_info = (
            Product_info.objects
            .select_related(
                'name',
                'import_from',
            )
            .get(
                id=product_id
            )
        )

    except Product_info.DoesNotExist:

        return JsonResponse(
            {
                'success': False,
                'message':
                    'ไม่พบ Product_info นี้'
            },
            status=404
        )

    if product_info.weight <= 0:

        return JsonResponse(
            {
                'success': False,
                'message':
                    'Stock หมดแล้ว'
            }
        )

    source_number = (
        get_source_number(
            product_info
        )
    )

    lot_number = (
        get_lot_number(
            product_info
        )
    )

    prefix = (
        get_product_prefix(
            product_info
        )
    )

    # --------------------------------------------------------
    # ใช้ MAX sequence
    #
    # ไม่ใช้ count + 1
    # เพื่อป้องกัน Barcode ซ้ำ
    # หลังลบข้อมูล
    # --------------------------------------------------------

    next_number = (
        get_next_pack_number(
            product_info
        )
    )

    barcode = build_barcode(
        source_number,
        lot_number,
        prefix,
        next_number
    )

    return JsonResponse(
        {
            'success': True,

            'barcode':
                barcode,

            'product_id':
                product_info.id,

            'remaining_stock':
                product_info.weight,

            'product_name':
                (
                    product_info.name.name
                    if product_info.name
                    else ''
                ),

            'import_from':
                (
                    product_info
                    .import_from
                    .name_place
                    if product_info.import_from
                    else ''
                ),

            'lot_number':
                lot_number,

            'selling_price_per_kg':
                float(
                    product_info
                    .selling_price_per_kg
                    or 0
                ),

            'profit_percent':
                float(
                    product_info
                    .profit_percent
                ),
        }
    )


# ============================================================
# PACK PRODUCT
# ============================================================

@require_POST
def pack_product(request):

    product_id = (
        request.POST.get(
            'product_id'
        )
    )

    weight_raw = (
        request.POST.get(
            'weight'
        )
    )

    if not product_id:

        return JsonResponse(
            {
                'success': False,
                'message':
                    'ไม่พบ Product ID'
            },
            status=400
        )

    if not weight_raw:

        return JsonResponse(
            {
                'success': False,
                'message':
                    'กรุณาระบุน้ำหนัก'
            },
            status=400
        )

    try:

        weight = float(
            weight_raw
        )

    except (
        TypeError,
        ValueError
    ):

        return JsonResponse(
            {
                'success': False,
                'message':
                    'น้ำหนักไม่ถูกต้อง'
            },
            status=400
        )

    if weight <= 0:

        return JsonResponse(
            {
                'success': False,
                'message':
                    'น้ำหนักต้องมากกว่า 0'
            },
            status=400
        )

    product_list = None
    product_info = None

    try:

        with transaction.atomic():

            product_info = (
                Product_info.objects
                .select_for_update()
                .select_related(
                    'name',
                    'type_product',
                    'import_from',
                )
                .get(
                    id=product_id
                )
            )

            current_stock = float(
                product_info.weight
            )

            if current_stock <= 0:

                return JsonResponse(
                    {
                        'success': False,
                        'message':
                            'Stock หมดแล้ว',
                        'remaining_stock':
                            current_stock,
                    }
                )

            if weight > current_stock:

                return JsonResponse(
                    {
                        'success': False,

                        'message':
                            (
                                'น้ำหนักที่แพ็ค '
                                'มากกว่า Stock '
                                f'ที่เหลือ '
                                f'{current_stock:.2f} g'
                            ),

                        'remaining_stock':
                            current_stock,
                    }
                )

            # ------------------------------------------------
            # ราคาขาย
            # ------------------------------------------------

            selling_price_per_kg = float(
                product_info
                .selling_price_per_kg
                or 0
            )

            selling_price = math.ceil(
                (
                    weight / 1000
                )
                *
                selling_price_per_kg
            )

            # ------------------------------------------------
            # Barcode
            # ------------------------------------------------

            source_number = (
                get_source_number(
                    product_info
                )
            )

            lot_number = (
                get_lot_number(
                    product_info
                )
            )

            prefix = (
                get_product_prefix(
                    product_info
                )
            )

            next_number = (
                get_next_pack_number(
                    product_info
                )
            )

            barcode = build_barcode(
                source_number,
                lot_number,
                prefix,
                next_number
            )

            # ------------------------------------------------
            # สร้าง Product_list
            # ------------------------------------------------

            product_list = (
                Product_list.objects.create(

                    product=product_info,

                    barcode=barcode,

                    weight=weight,

                    selling_price=
                        selling_price,

                    activated=True,

                    loyverse_synced=False,

                    storage_status='pending',
                )
            )

            # ------------------------------------------------
            # SKU
            # ------------------------------------------------

            product_list.loyverse_sku = str(
                30000
                +
                product_list.id
            )

            product_list.save(
                update_fields=[
                    'loyverse_sku'
                ]
            )

            # ------------------------------------------------
            # หัก Stock
            # ------------------------------------------------

            product_info.weight = (
                current_stock - weight
            )

            if abs(
                product_info.weight
            ) < 0.000001:

                product_info.weight = 0

            product_info.save(
                update_fields=[
                    'weight'
                ]
            )

            remaining_stock = float(
                product_info.weight
            )

    except Product_info.DoesNotExist:

        return JsonResponse(
            {
                'success': False,
                'message':
                    'ไม่พบ Product_info'
            },
            status=404
        )

    # ========================================================
    # NIIMBOT
    # ========================================================

    try:

        niimbot_col.print_label(

            product=(
                product_info.name.name
                if product_info.name
                else ''
            ),

            price=int(
                product_list.selling_price
            ),

            weight=(
                f"{float(product_list.weight/1000):0.3f}"
            ),

            lot=(
                "MFG:"
                +
                timezone.localtime(
                    product_list.mfg
                ).strftime(
                    '%d/%m/%Y %H:%M'
                )
            ),

            barcode=str(
                product_list.barcode
            ),

            from_at=str(
                "BETAGRO"
            ),

            price_per_kg= int(
                product_info.selling_price_per_kg
                ),

            types=(
                "🐷"
                if product_info.type_product
                and product_info.type_product.name_type == "หมูสดใส่ถุง"
                else "🐔"
            )
        )

    except Exception as e:

        print(
            "NIIMBOT ERROR:",
            repr(e)
        )

    return JsonResponse(
        {
            'success': True,

            'id':
                product_list.id,

            'product_id':
                product_info.id,

            'product_name':
                (
                    product_info.name.name
                    if product_info.name
                    else ''
                ),

            'barcode':
                product_list.barcode,

            'weight':
                float(
                    product_list.weight
                ),

            'remaining_stock':
                remaining_stock,

            'selling_price':
                float(
                    product_list.selling_price
                ),

            'selling_price_per_kg':
                selling_price_per_kg,

            'import_from':
                (
                    product_info
                    .import_from
                    .name_place
                    if product_info.import_from
                    else ''
                ),

            'lot_number':
                lot_number,

            'prefix':
                prefix,

            'mfg':
                timezone.localtime(
                    product_list.mfg
                ).strftime(
                    '%d/%m/%Y %H:%M'
                ),

            'loyverse_sku':
                product_list.loyverse_sku,

            'loyverse_synced':
                product_list.loyverse_synced,
        }
    )


# ============================================================
# BARCODE HELPERS
# ============================================================

def build_barcode(
    source_number,
    lot_number,
    prefix,
    next_number
):

    return (
        f"{source_number}"
        f"{lot_number}"
        f"{prefix}"
        f"{next_number:02d}"
    )


def get_next_pack_number(product_info):

    # --------------------------------------------------------
    # Barcode format:
    #
    # source + lot + prefix + sequence
    #
    # ตัวอย่าง:
    # 12800501
    # 12800502
    # 12800503
    #
    # โดย sequence อยู่หลัง prefix เสมอ
    # --------------------------------------------------------

    source_number = get_source_number(product_info)
    lot_number = get_lot_number(product_info)
    prefix = get_product_prefix(product_info)

    barcode_prefix = (
        f"{source_number}"
        f"{lot_number}"
        f"{prefix}"
    )

    barcode_prefix_clean = barcode_prefix.replace('-', '')

    existing_barcodes = (
        Product_list.objects
        .filter(
            product=product_info
        )
        .values_list(
            'barcode',
            flat=True
        )
    )

    max_number = 0

    for barcode in existing_barcodes:

        if not barcode:
            continue

        try:

            # ------------------------------------------------
            # รองรับทั้ง Barcode แบบเก่า
            #
            # 12-8-005-02
            #
            # และแบบใหม่
            #
            # 12800502
            #
            # โดยลบ '-' ออกก่อนตรวจสอบ
            # ------------------------------------------------

            clean_barcode = str(barcode).replace('-', '')

            # Barcode ต้องขึ้นต้นด้วย prefix ของสินค้านี้
            if not clean_barcode.startswith(barcode_prefix_clean):
                continue

            # เอาเฉพาะส่วน sequence หลัง prefix
            sequence_text = clean_barcode[
                len(barcode_prefix_clean):
            ]

            if not sequence_text:
                continue

            sequence = int(sequence_text)

            if sequence > max_number:
                max_number = sequence

        except (ValueError, TypeError):
            continue

    return max_number + 1


def get_source_number(
    product_info
):

    if not product_info.import_from:

        return '0'

    return str(
        product_info
        .import_from
        .ids
    )


def get_lot_number(
    product_info
):

    value = getattr(
        product_info,
        'lot_number',
        None
    )

    if value is None:

        return '0'

    return str(
        value
    )


def get_product_prefix(
    product_info
):

    if not product_info.name:

        return '0000'

    prefix = (
        product_info
        .name
        .prefix_barcode
    )

    if not prefix:

        return '0000'

    return str(
        prefix
    )


# ============================================================
# PRINT NIIMBOT
# ============================================================

@require_POST
def print_niimbot(request):

    try:

        data = json.loads(
            request.body
        )

        product_id = data.get(
            "product_id"
        )

        if not product_id:

            return JsonResponse(
                {
                    "success": False,
                    "error":
                        "ไม่พบ Product ID"
                },
                status=400
            )

        product_list = (
            Product_list.objects
            .select_related(
                "product",
                "product__name"
            )
            .get(
                id=product_id
            )
        )

        mfg = timezone.localtime(
            product_list.mfg
        )

        niimbot = (
            NIIMBOTController()
        )

        niimbot.print_label(

            product=(
                product_list
                .product
                .name
                .name
            ),

            price=int(
                product_list
                .selling_price
            ),

            weight=(
                f"{float(product_list.weight/1000):0.3f}"
            ),

            lot=(
                f"MFG:"
                f"{mfg.strftime('%d/%m/%Y %H:%M')}"
            ),

            from_at=str(
                "BETAGRO"
            ),

            price_per_kg= int(
                product_list.product.selling_price_per_kg
                ),

            types=(
                "🐷"
                if product_list.product.type_product
                and product_list.product.type_product.name_type == "หมูสดใส่ถุง"
                else "🐔"
            ),



            barcode=str(
                product_list.barcode
            ),
        )

        return JsonResponse(
            {
                "success": True,
                "message":
                    "สั่งพิมพ์เรียบร้อย"
            }
        )

    except Product_list.DoesNotExist:

        return JsonResponse(
            {
                "success": False,
                "error":
                    "ไม่พบรายการสินค้า"
            },
            status=404
        )

    except Exception as e:

        print(
            "NIIMBOT ERROR:",
            repr(e)
        )

        return JsonResponse(
            {
                "success": False,
                "error":
                    str(e)
            },
            status=500
        )

# ============================================================
# FREEZE QUEUE PAGE
# ============================================================

def freeze_queue_page(request):
    """Render freeze queue management page"""
    return render(
        request,
        'freeze_queue.html',
    )


def meat_prices_page(request):
    """Dashboard ราคา meat_parts เปรียบเทียบแต่ละรอบ"""
    return render(
        request,
        'meat_prices.html',
    )


@require_GET
def get_meat_parts(request):

    category_id = request.GET.get(
        'category_id'
    )

    if not category_id:

        return JsonResponse(
            {
                'success': False,
                'parts': [],
            }
        )

    try:

        category_id = int(
            category_id
        )

    except (
        TypeError,
        ValueError
    ):

        return JsonResponse(
            {
                'success': False,
                'parts': [],
            },
            status=400
        )

    parts = (
        meat_parts.objects
        .filter(
            category_id=category_id
        )
        .order_by('name')
    )

    return JsonResponse(
        {
            'success': True,

            'parts': [
                {
                    'id': part.id,
                    'name': part.name,
                }

                for part in parts
            ]
        }
    )