import os
import requests


LOYVERSE_BASE_URL = (
    "https://api.loyverse.com/v1.0"
)


LOYVERSE_ACCESS_TOKEN = os.environ.get(
    "LOYVERSE_ACCESS_TOKEN", ""
)


# ==========================================================
# CREATE ITEM
# ==========================================================

def create_loyverse_item(product_list):

    # ------------------------------------------------------
    # SKU
    #
    # Product_list.id = 1
    # SKU = 3000
    #
    # Product_list.id = 2
    # SKU = 3001
    # ------------------------------------------------------

    sku = str(
        2999 + product_list.id
    )


    # ------------------------------------------------------
    # Product name
    # ------------------------------------------------------

    product_name = (
        product_list
        .product
        .name
        .name
    )


    # ------------------------------------------------------
    # Weight
    #
    # Product_list เก็บเป็น gram
    #
    # 270 g -> 0.27
    # ------------------------------------------------------

    weight_kg = (
        float(product_list.weight)
        / 1000
    )


    # ------------------------------------------------------
    # Name
    #
    # ตัวอย่าง:
    #
    # หมูบด 0.27
    # ------------------------------------------------------

    item_name = (
        f"{product_name} "
        f"{weight_kg:.2f}"
    )


    # ------------------------------------------------------
    # Handle
    #
    # ตัวอย่าง CSV:
    #
    # สะโพกหมู_0.27_20220
    #
    # ของเรา:
    #
    # หมูบด_0.27_3056
    # ------------------------------------------------------

    handle = (
        f"{product_name}_"
        f"{weight_kg:.2f}_"
        f"{sku}"
    )


    # ------------------------------------------------------
    # Barcode
    # ------------------------------------------------------

    barcode = str(
        product_list.barcode
    )


    # ------------------------------------------------------
    # Price
    # ------------------------------------------------------

    selling_price = float(
        product_list.selling_price
    )


    # ------------------------------------------------------
    # Cost
    #
    # ตรงนี้ต้องระวัง:
    #
    # ถ้า Product ของคุณมีต้นทุน
    # ให้เปลี่ยนตรงนี้เป็น field ต้นทุนจริง
    #
    # ตอนนี้ตั้งเป็น 0 ก่อน
    # ------------------------------------------------------

    cost = 0


    # ------------------------------------------------------
    # Payload
    # ------------------------------------------------------

    payload = {

        "handle":
            handle,

        "item_name":
            item_name,

        "description":
            (
                f"Product_list ID: "
                f"{product_list.id}"
            ),

        "reference_id":
            str(product_list.id),

        "track_stock":
            True,

        "sold_by_weight":
            False,

        "variants": [

            {

                "sku":
                    sku,

                "reference_variant_id":
                    str(product_list.id),

                "barcode":
                    barcode,

                "cost":
                    cost,

                "purchase_cost":
                    cost,

                "default_pricing_type":
                    "FIXED",

                "default_price":
                    selling_price,

            }

        ]

    }


    # ------------------------------------------------------
    # REQUEST
    # ------------------------------------------------------

    response = requests.post(

        f"{LOYVERSE_BASE_URL}/items",

        headers={

            "Authorization":
                f"Bearer {LOYVERSE_ACCESS_TOKEN}",

            "Content-Type":
                "application/json",

            "Accept":
                "application/json",

        },

        json=payload,

        timeout=30

    )


    # ------------------------------------------------------
    # ERROR
    # ------------------------------------------------------

    if not response.ok:

        print(
            "========== LOYVERSE ERROR =========="
        )

        print(
            "STATUS:",
            response.status_code
        )

        print(
            "RESPONSE:",
            response.text
        )

        print(
            "===================================="
        )

        raise Exception(
            f"Loyverse API Error "
            f"{response.status_code}: "
            f"{response.text}"
        )


    # ------------------------------------------------------
    # RESPONSE
    # ------------------------------------------------------

    data = response.json()


    # ======================================================
    # GET LOYVERSE IDS
    # ======================================================

    loyverse_item_id = (
        data.get("id")
    )


    variants = (
        data.get("variants")
        or []
    )


    loyverse_variant_id = None


    if variants:

        loyverse_variant_id = (
            variants[0]
            .get("variant_id")
        )


    # ======================================================
    # SAVE TO DJANGO
    # ======================================================

    product_list.loyverse_sku = sku

    product_list.loyverse_item_id = (
        loyverse_item_id
    )

    product_list.loyverse_variant_id = (
        loyverse_variant_id
    )

    product_list.loyverse_synced = True


    product_list.save(
        update_fields=[
            "loyverse_sku",
            "loyverse_item_id",
            "loyverse_variant_id",
            "loyverse_synced",
        ]
    )


    return data

def get_loyverse_stores():

    response = requests.get(

        f"{LOYVERSE_BASE_URL}/stores",

        headers={

            "Authorization":
                f"Bearer {LOYVERSE_ACCESS_TOKEN}",

            "Accept":
                "application/json",

        },

        timeout=30

    )


    if not response.ok:

        raise Exception(
            f"Loyverse Store Error: "
            f"{response.text}"
        )


    return response.json()


def get_store_id(store_name):

    data = get_loyverse_stores()


    stores = (
        data.get("stores")
        or []
    )


    for store in stores:

        if (
            store.get("name")
            == store_name
        ):

            return store.get("id")


    raise Exception(
        f"ไม่พบ Store: {store_name}"
    )


def set_loyverse_stock(
    variant_id,
    store_id,
    stock
):

    payload = {

        "inventory_levels": [

            {

                "variant_id":
                    variant_id,

                "store_id":
                    store_id,

                "stock_after":
                    stock,

            }

        ]

    }


    response = requests.post(

        f"{LOYVERSE_BASE_URL}/inventory",

        headers={

            "Authorization":
                f"Bearer {LOYVERSE_ACCESS_TOKEN}",

            "Content-Type":
                "application/json",

            "Accept":
                "application/json",

        },

        json=payload,

        timeout=30

    )


    if not response.ok:

        print(
            "INVENTORY ERROR:",
            response.text
        )

        raise Exception(
            response.text
        )


    return response.json()


def create_loyverse_product(
    product_list
):

    # ======================================================
    # 1. CREATE ITEM
    # ======================================================

    data = create_loyverse_item(
        product_list
    )


    # ======================================================
    # 2. GET VARIANT
    # ======================================================

    variants = (
        data.get("variants")
        or []
    )


    if not variants:

        raise Exception(
            "Loyverse ไม่ได้ส่ง variant กลับมา"
        )


    variant_id = (
        variants[0]
        .get("variant_id")
    )


    if not variant_id:

        raise Exception(
            "ไม่พบ variant_id"
        )


    # ======================================================
    # 3. GET CL.MEAT STORE
    # ======================================================

    store_id = get_store_id(
        "CL.MEAT"
    )


    # ======================================================
    # 4. SET STOCK = 1
    # ======================================================

    inventory = set_loyverse_stock(

        variant_id=

            variant_id,

        store_id=

            store_id,

        stock=

            1

    )


    return {

        "item":
            data,

        "variant_id":
            variant_id,

        "store_id":
            store_id,

        "inventory":
            inventory,

    }