import csv

from io import StringIO

from .models import Product_list


HEADERS = [
    "Handle",
    "SKU",
    "Name",
    "Category",
    "Description",
    "Sold by weight",
    "Option 1 name",
    "Option 1 value",
    "Option 2 name",
    "Option 2 value",
    "Option 3 name",
    "Option 3 value",
    "Cost",
    "Barcode",
    "SKU of included item",
    "Quantity of included item",
    "Track stock",
    "Available for sale [CL.MEAT]",
    "Price [CL.MEAT]",
    "In stock [CL.MEAT]",
    "Low stock [CL.MEAT]",
    'Modifier - "ไก่"',
]


def get_unsynced_products():
    """
    เดิม: ใช้สำหรับ Export รายการที่ยังไม่ได้ Sync
    คง behavior เดิมไว้เพื่อไม่ให้ workflow เดิมเสีย
    """
    return (
        Product_list.objects
        .select_related(
            "product",
            "product__name",
            "product__type_product",
            "product__import_from",
        )
        .filter(
            loyverse_synced=False,
            activated=True,
        )
        .order_by("id")
    )


def get_export_products(
    product_ids=None,
    scope="pending",
):
    """
    scope:
        pending = ยังไม่ Sync
        synced  = Sync แล้ว
        all     = ทั้งหมด

    product_ids:
        ถ้าส่งมา จะ Export เฉพาะ ID ที่เลือก
        โดยไม่สน scope
    """

    queryset = (
        Product_list.objects
        .select_related(
            "product",
            "product__name",
            "product__type_product",
            "product__import_from",
        )
        .filter(
            activated=True,
        )
        .order_by("id")
    )

    if product_ids:
        queryset = queryset.filter(id__in=product_ids)

    elif scope == "synced":
        queryset = queryset.filter(
            loyverse_synced=True
        )

    elif scope == "all":
        pass

    else:
        queryset = queryset.filter(
            loyverse_synced=False
        )

    return queryset


def build_loyverse_rows(products):
    rows = []

    for item in products:

        if not item.product:
            continue

        product_info = item.product

        # ----------------------------------------------------
        # Meat name
        # ----------------------------------------------------

        meat_name = ""

        if product_info.name:
            meat_name = product_info.name.name

        # ----------------------------------------------------
        # Weight
        # ----------------------------------------------------

        weight = float(item.weight or 0)

        # ----------------------------------------------------
        # SKU
        # ----------------------------------------------------

        sku = item.loyverse_sku or item.barcode

        # ----------------------------------------------------
        # Display name
        # ----------------------------------------------------

        display_name = (
            f"{meat_name} "
            f"{int(weight)} กรัม"
        )

        # ----------------------------------------------------
        # Handle
        # ----------------------------------------------------

        handle = (
            f"{meat_name}_"
            f"{weight}_"
            f"{sku}"
        )

        # ----------------------------------------------------
        # Category
        # ----------------------------------------------------

        category = ""

        if product_info.type_product:
            category = product_info.type_product.name_type

        # ----------------------------------------------------
        # Cost
        #
        # ต้นทุน/kg → ต้นทุนต่อแพ็ค
        # ใช้ต้นทุนปัจจุบันของ Product_info
        # ----------------------------------------------------

        cost_per_kg = float(product_info.cost or 0)

        cost = (
            cost_per_kg
            * (weight / 1000)
        )

        # ----------------------------------------------------
        # Selling price
        #
        # จุดสำคัญ:
        # Product_list.selling_price คือราคาที่จะ Export
        # ดังนั้นหลังจัดโปรแล้ว CSV จะใช้ราคาที่แก้ใหม่ทันที
        # ----------------------------------------------------

        selling_price = float(
            item.selling_price or 0
        )

        row = [
            handle,
            sku,
            display_name,
            category,
            "",
            "N",
            "",
            "",
            "",
            "",
            "",
            "",
            f"{cost:.2f}",
            item.barcode,
            "",
            "",
            "Y",
            "Y",
            f"{selling_price:.2f}",
            "1",
            "0",
            "N",
        ]

        rows.append(row)

    return rows


def generate_loyverse_csv(
    product_ids=None,
    scope="pending",
):
    """
    สร้าง CSV สำหรับ Loyverse

    - ไม่ส่ง IDs + scope=pending -> behavior เดิม
    - scope=synced -> Export เฉพาะรายการที่ Sync แล้ว
    - scope=all -> Export ทั้งหมด
    - product_ids -> Export เฉพาะรายการที่เลือก
    """

    products = get_export_products(
        product_ids=product_ids,
        scope=scope,
    )

    rows = build_loyverse_rows(products)

    output = StringIO(
        newline=""
    )

    writer = csv.writer(
        output
    )

    writer.writerow(HEADERS)
    writer.writerows(rows)

    return (
        output.getvalue(),
        len(rows),
    )
