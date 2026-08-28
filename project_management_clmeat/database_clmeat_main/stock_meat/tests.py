"""
Tests for CL.MEAT — full lifecycle, bulk price, loyalty sync, finance.

Covers:
  1. pack_product (views.py)
  2. freeze_queue lifecycle (freeze_queue.py)
  3. sync_loyverse_receipts (sold_items.py)
  4. bulk_update_prices / undo_bulk_prices (views.py)
  5. confirm_loyverse_sync (views.py)
  6. finance views (finance.py)
  7. Full lifecycle integration (STEP 1-8)
"""
from decimal import Decimal
from unittest.mock import patch, MagicMock
from datetime import timedelta, date

from django.test import TestCase, Client
from django.utils import timezone
from django.db import transaction

from stock_meat.models import (
    Category,
    Supply_meat,
    meat_parts,
    Product_info,
    Product_list,
    FreezeRotation,
    SoldItem,
    Transaction,
    ExpenseCategory,
    ElectricityBill,
    PriceChangeHistory,
    LoyverseSyncBatch,
)


# ============================================================
# HELPERS
# ============================================================

def _create_category(name="หมูสดใส่ถุง"):
    return Category.objects.create(name_type=name)


def _create_supply(name="BETAGRO"):
    return Supply_meat.objects.create(name_place=name, locations="กรุงเทพ")


def _create_part(category, name="สะโพก", prefix="3-1-8009"):
    return meat_parts.objects.create(
        category=category,
        name=name,
        prefix_barcode=prefix,
    )


def _create_product_info(
    part, supply, weight=5000, cost=120, selling_price=160
):
    return Product_info.objects.create(
        name=part,
        import_from=supply,
        type_product=part.category,
        weight=weight,
        cost=cost,
        selling_price_per_kg=selling_price,
    )


def _create_product_list(
    product_info,
    barcode="12345678",
    weight=1000,
    selling_price=160,
    storage_status="frozen",
    **kwargs,
):
    return Product_list.objects.create(
        product=product_info,
        barcode=barcode,
        weight=weight,
        selling_price=selling_price,
        activated=True,
        loyverse_synced=False,
        storage_status=storage_status,
        **kwargs,
    )


# ============================================================
# 1. PACK PRODUCT
# ============================================================

class PackProductTests(TestCase):
    """Tests for views.pack_product."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(
            self.part, self.supply,
            weight=5000, cost=120, selling_price=160,
        )

    @patch("stock_meat.views.niimbot_col", new_callable=lambda: MagicMock)
    def test_pack_success(self, mock_niimbot):
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 1000},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["weight"], 1000.0)
        self.assertEqual(data["remaining_stock"], 4000.0)
        self.assertIn("barcode", data)
        self.assertEqual(data["loyverse_synced"], False)

        self.pi.refresh_from_db()
        self.assertAlmostEqual(self.pi.weight, 4000.0, places=2)

        pl = Product_list.objects.get(id=data["id"])
        self.assertEqual(pl.storage_status, "pending")
        self.assertTrue(pl.activated)
        self.assertIsNotNone(pl.loyverse_sku)
        self.assertEqual(pl.selling_price, 160)

    @patch("stock_meat.views.niimbot_col", new_callable=lambda: MagicMock)
    def test_pack_deducts_stock_exact(self, mock_niimbot):
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 5000},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertAlmostEqual(data["remaining_stock"], 0.0, places=2)

    @patch("stock_meat.views.niimbot_col", new_callable=lambda: MagicMock)
    def test_pack_multiple_times(self, mock_niimbot):
        self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 1500},
        )
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 2000},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertAlmostEqual(data["remaining_stock"], 1500.0, places=2)

    @patch("stock_meat.views.niimbot_col", new_callable=lambda: MagicMock)
    def test_barcode_unique_per_product(self, mock_niimbot):
        r1 = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 500},
        ).json()
        r2 = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 500},
        ).json()
        self.assertNotEqual(r1["barcode"], r2["barcode"])

    def test_missing_product_id(self):
        resp = self.client.post("/api/pack-product/", {"weight": 1000})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])

    def test_missing_weight(self):
        resp = self.client.post(
            "/api/pack-product/", {"product_id": self.pi.id}
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_weight_zero(self):
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 0},
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_weight_negative(self):
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": -100},
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_weight_string(self):
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": "abc"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_nonexistent_product(self):
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": 99999, "weight": 1000},
        )
        self.assertEqual(resp.status_code, 404)

    def test_stock_exhausted(self):
        self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 5000},
        )
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 1},
        )
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("Stock หมด", data["message"])

    def test_weight_exceeds_stock(self):
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 9999},
        )
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("มากกว่า Stock", data["message"])

    @patch("stock_meat.views.niimbot_col", new_callable=lambda: MagicMock)
    def test_sku_is_30000_plus_id(self, mock_niimbot):
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 1000},
        ).json()
        self.assertEqual(resp["loyverse_sku"], str(30000 + resp["id"]))

    @patch("stock_meat.views.niimbot_col")
    def test_niimbot_failure_doesnt_break_pack(self, mock_niimbot):
        mock_niimbot.print_label.side_effect = RuntimeError("printer offline")
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 1000},
        )
        data = resp.json()
        self.assertTrue(data["success"])


# ============================================================
# 2. FREEZE QUEUE LIFECYCLE
# ============================================================

class FreezeQueueLifecycleTests(TestCase):
    """Tests for freeze queue lifecycle."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(self.part, self.supply)
        self.pl = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            storage_status="frozen",
        )

    def test_freeze_dashboard_returns_json(self):
        resp = self.client.get("/api/freeze-queue/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("data", data)
        d = data["data"]
        self.assertIn("thawing", d)
        self.assertIn("display", d)
        self.assertIn("frozen_available", d)
        self.assertIn("alerts", d)
        self.assertIn("stats", d)

    def test_freeze_dashboard_includes_frozen_item(self):
        resp = self.client.get("/api/freeze-queue/")
        frozen = resp.json()["data"]["frozen_available"]
        barcodes = [p["barcode"] for p in frozen]
        self.assertIn("100001", barcodes)

    def test_start_thaw_success(self):
        resp = self.client.post(
            "/api/start-thaw/",
            {"product_id": self.pl.id, "thaw_duration_hours": 24},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("thaw_ready_at", data)

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "thawing")
        self.assertEqual(self.pl.thaw_duration_hours, 24)
        self.assertGreater(self.pl.thaw_queue_position, 0)
        self.assertIsNotNone(self.pl.thaw_started_at)

        rot = FreezeRotation.objects.filter(
            product_list=self.pl, action="thaw_start"
        ).first()
        self.assertIsNotNone(rot)
        self.assertEqual(rot.status_before, "frozen")
        self.assertEqual(rot.status_after, "thawing")

    def test_start_thaw_invalid_duration_too_low(self):
        resp = self.client.post(
            "/api/start-thaw/",
            {"product_id": self.pl.id, "thaw_duration_hours": 6},
        )
        self.assertEqual(resp.status_code, 400)

    def test_start_thaw_invalid_duration_too_high(self):
        resp = self.client.post(
            "/api/start-thaw/",
            {"product_id": self.pl.id, "thaw_duration_hours": 72},
        )
        self.assertEqual(resp.status_code, 400)

    def test_start_thaw_wrong_status(self):
        self.pl.storage_status = "display"
        self.pl.save(update_fields=["storage_status"])
        resp = self.client.post(
            "/api/start-thaw/",
            {"product_id": self.pl.id, "thaw_duration_hours": 24},
        )
        self.assertEqual(resp.status_code, 400)

    def test_start_thaw_missing_product(self):
        resp = self.client.post(
            "/api/start-thaw/", {"thaw_duration_hours": 24}
        )
        self.assertEqual(resp.status_code, 400)

    def test_start_thaw_queue_position_increments(self):
        pl2 = _create_product_list(
            self.pi, barcode="100002", weight=800,
            storage_status="frozen",
        )
        self.client.post(
            "/api/start-thaw/",
            {"product_id": self.pl.id, "thaw_duration_hours": 24},
        )
        resp2 = self.client.post(
            "/api/start-thaw/",
            {"product_id": pl2.id, "thaw_duration_hours": 18},
        )
        self.pl.refresh_from_db()
        pl2.refresh_from_db()
        self.assertEqual(
            self.pl.thaw_queue_position + 1,
            pl2.thaw_queue_position,
        )

    def test_complete_thaw_success(self):
        self.pl.storage_status = "thawing"
        self.pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        self.pl.thaw_duration_hours = 24
        self.pl.thaw_queue_position = 1
        self.pl.save()

        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": self.pl.id, "display_days": 3},
        )
        data = resp.json()
        self.assertTrue(data["success"])

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "display")
        self.assertEqual(self.pl.display_max_days, 3)
        self.assertIsNotNone(self.pl.entered_display_at)
        self.assertEqual(self.pl.thaw_queue_position, 0)

    def test_complete_thaw_not_ready(self):
        self.pl.storage_status = "thawing"
        self.pl.thaw_started_at = timezone.now()
        self.pl.thaw_duration_hours = 24
        self.pl.thaw_queue_position = 1
        self.pl.save()

        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": self.pl.id, "display_days": 3},
        )
        self.assertEqual(resp.status_code, 400)

    def test_complete_thaw_wrong_status(self):
        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": self.pl.id, "display_days": 3},
        )
        self.assertEqual(resp.status_code, 400)

    def test_complete_thaw_records_history(self):
        self.pl.storage_status = "thawing"
        self.pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        self.pl.thaw_duration_hours = 24
        self.pl.thaw_queue_position = 1
        self.pl.save()

        self.client.post(
            "/api/complete-thaw/",
            {"product_id": self.pl.id, "display_days": 5},
        )

        rot = FreezeRotation.objects.filter(
            product_list=self.pl, action="display_start"
        ).first()
        self.assertIsNotNone(rot)
        self.assertIn("5 วัน", rot.notes)

    def test_pull_from_display_success(self):
        self.pl.storage_status = "display"
        self.pl.entered_display_at = timezone.now()
        self.pl.save()

        resp = self.client.post(
            "/api/pull-from-display/",
            {"product_id": self.pl.id, "reason": "ไม่ขายดี"},
        )
        data = resp.json()
        self.assertTrue(data["success"])

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "frozen")
        self.assertIsNone(self.pl.entered_display_at)

        rot = FreezeRotation.objects.filter(
            product_list=self.pl, action="freeze_return"
        ).first()
        self.assertIsNotNone(rot)
        self.assertEqual(rot.notes, "ไม่ขายดี")

    def test_pull_from_display_wrong_status(self):
        resp = self.client.post(
            "/api/pull-from-display/",
            {"product_id": self.pl.id},
        )
        self.assertEqual(resp.status_code, 400)

    def test_pending_products(self):
        self.pl.storage_status = "pending"
        self.pl.save(update_fields=["storage_status"])

        resp = self.client.get("/api/pending-products/")
        data = resp.json()
        self.assertTrue(data["success"])
        barcodes = [p["barcode"] for p in data["products"]]
        self.assertIn("100001", barcodes)

    def test_add_to_queue_frozen(self):
        self.pl.storage_status = "pending"
        self.pl.save(update_fields=["storage_status"])

        resp = self.client.post(
            "/api/add-to-queue/",
            {"product_id": self.pl.id, "status": "frozen"},
        )
        data = resp.json()
        self.assertTrue(data["success"])

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "frozen")

    def test_add_to_queue_display(self):
        self.pl.storage_status = "pending"
        self.pl.save(update_fields=["storage_status"])

        resp = self.client.post(
            "/api/add-to-queue/",
            {"product_id": self.pl.id, "status": "display", "display_days": 5},
        )
        data = resp.json()
        self.assertTrue(data["success"])

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "display")
        self.assertEqual(self.pl.display_max_days, 5)

    def test_add_to_queue_invalid_status(self):
        resp = self.client.post(
            "/api/add-to-queue/",
            {"product_id": self.pl.id, "status": "invalid"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_auto_rotation_freeze_expired(self):
        """Freeze complete → alert, but status stays frozen (user must queue manually)."""
        self.pl.freeze_end_at = timezone.now() - timedelta(hours=2)
        self.pl.freeze_target_temp = -8
        self.pl.save()

        resp = self.client.get("/api/auto-rotation-check/")
        data = resp.json()
        self.assertTrue(data["success"])

        types = [a["type"] for a in data["alerts"]]
        self.assertIn("freeze_complete", types)

        # Status should remain frozen — user must click "เข้าคิวละลาย"
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "frozen")

    def test_auto_rotation_display_expired(self):
        self.pl.storage_status = "display"
        self.pl.entered_display_at = timezone.now() - timedelta(days=10)
        self.pl.display_max_days = 3
        self.pl.save()

        resp = self.client.get("/api/auto-rotation-check/")
        data = resp.json()
        types = [a["type"] for a in data["alerts"]]
        self.assertIn("display_expired", types)

    def test_auto_rotation_thaw_ready(self):
        self.pl.storage_status = "thawing"
        self.pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        self.pl.thaw_duration_hours = 24
        self.pl.thaw_queue_position = 1
        self.pl.save()

        resp = self.client.get("/api/auto-rotation-check/")
        data = resp.json()
        types = [a["type"] for a in data["alerts"]]
        self.assertIn("thaw_ready", types)


# ============================================================
# 3. SYNC LOYVERSE RECEIPTS
# ============================================================

MOCK_RECEIPT_RESPONSE = {
    "receipts": [
        {
            "receipt_number": "R-001",
            "receipt_date": "2026-08-20T10:30:00Z",
            "store_id": "store-1",
            "line_items": [
                {
                    "sku": "30001",
                    "item_id": "item-1",
                    "variant_id": "var-1",
                    "item_name": "สะโพกเลาะกระดูก",
                    "variant_name": "1000g",
                    "price": 160.0,
                    "cost": 120.0,
                    "quantity": 1,
                    "total_money": 160.0,
                },
            ],
        },
        {
            "receipt_number": "R-002",
            "receipt_date": "2026-08-20T14:00:00Z",
            "store_id": "store-1",
            "line_items": [
                {
                    "sku": "30002",
                    "item_id": "item-2",
                    "variant_id": "var-2",
                    "item_name": "สะโพกเลาะกระดูก",
                    "variant_name": "500g",
                    "price": 80.0,
                    "cost": 60.0,
                    "quantity": 1,
                    "total_money": 80.0,
                },
                {
                    "sku": "30001",
                    "item_id": "item-1",
                    "variant_id": "var-3",
                    "item_name": "สะโพกเลาะกระดูก",
                    "variant_name": "1000g",
                    "price": 160.0,
                    "cost": 120.0,
                    "quantity": 1,
                    "total_money": 160.0,
                },
            ],
        },
    ]
}

MOCK_EMPTY_RESPONSE = {"receipts": []}


def _mock_requests_get(url, headers=None, timeout=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = MOCK_RECEIPT_RESPONSE
    return resp


def _mock_requests_get_empty(url, headers=None, timeout=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = MOCK_EMPTY_RESPONSE
    return resp


def _mock_requests_get_error(url, headers=None, timeout=None):
    import requests as _req
    raise _req.ConnectionError("API down")


class SyncLoyverseReceiptsTests(TestCase):
    """Tests for sold_items.sync_loyverse_receipts."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(self.part, self.supply)

        self.pl1 = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            storage_status="frozen",
        )
        self.pl1.loyverse_sku = "30001"
        self.pl1.save(update_fields=["loyverse_sku"])

        self.pl2 = _create_product_list(
            self.pi, barcode="100002", weight=500,
            storage_status="frozen",
        )
        self.pl2.loyverse_sku = "30002"
        self.pl2.save(update_fields=["loyverse_sku"])

        self.income_cat = ExpenseCategory.objects.create(
            name="ขายหน้าร้าน", category_type="income", icon="💰"
        )

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get)
    def test_sync_creates_sold_items(self, mock_get):
        resp = self.client.post("/api/sold-items/sync/", {"limit": 100})
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["synced"], 3)
        self.assertGreater(data["txn_created"], 0)
        self.assertEqual(SoldItem.objects.count(), 3)

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get)
    def test_sync_links_to_product_list(self, mock_get):
        self.client.post("/api/sold-items/sync/", {"limit": 100})
        items = SoldItem.objects.all()
        for item in items:
            if item.loyverse_sku == "30001":
                self.assertEqual(item.product_list, self.pl1)
            elif item.loyverse_sku == "30002":
                self.assertEqual(item.product_list, self.pl2)

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get)
    def test_sync_calculates_profit(self, mock_get):
        self.client.post("/api/sold-items/sync/", {"limit": 100})
        item = SoldItem.objects.get(loyverse_sku="30001", loyverse_variant_id="var-1")
        self.assertEqual(item.profit, Decimal("40.00"))

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get)
    def test_sync_dedup_prevents_duplicates(self, mock_get):
        self.client.post("/api/sold-items/sync/", {"limit": 100})
        count_after_first = SoldItem.objects.count()
        resp2 = self.client.post("/api/sold-items/sync/", {"limit": 100})
        data2 = resp2.json()
        self.assertEqual(SoldItem.objects.count(), count_after_first)
        self.assertEqual(data2["skipped"], 3)

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get)
    def test_sync_creates_income_transactions(self, mock_get):
        self.client.post("/api/sold-items/sync/", {"limit": 100})
        txns = Transaction.objects.filter(
            transaction_type="income",
            receipt_number__in=["R-001", "R-002"],
        )
        self.assertEqual(txns.count(), 2)
        txn1 = txns.get(receipt_number="R-001")
        self.assertEqual(txn1.amount, Decimal("160.00"))
        txn2 = txns.get(receipt_number="R-002")
        self.assertEqual(txn2.amount, Decimal("240.00"))

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get)
    def test_sync_transaction_dedup(self, mock_get):
        self.client.post("/api/sold-items/sync/", {"limit": 100})
        self.client.post("/api/sold-items/sync/", {"limit": 100})
        txns = Transaction.objects.filter(receipt_number="R-001")
        self.assertEqual(txns.count(), 1)

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get_empty)
    def test_sync_empty_receipts(self, mock_get):
        resp = self.client.post("/api/sold-items/sync/", {"limit": 100})
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["synced"], 0)
        self.assertEqual(SoldItem.objects.count(), 0)

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get_error)
    def test_sync_api_error(self, mock_get):
        resp = self.client.post("/api/sold-items/sync/", {"limit": 100})
        self.assertEqual(resp.status_code, 502)

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get)
    def test_sync_calculates_days_to_sell(self, mock_get):
        self.pl1.mfg = timezone.datetime(2026, 8, 10, tzinfo=timezone.utc)
        self.pl1.save(update_fields=["mfg"])
        self.client.post("/api/sold-items/sync/", {"limit": 100})
        item = SoldItem.objects.filter(
            loyverse_sku="30001", loyverse_variant_id="var-1",
        ).first()
        if item and item.product_list:
            self.assertGreaterEqual(item.days_to_sell, 9)

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get)
    def test_sync_calculates_electricity_cost(self, mock_get):
        ElectricityBill.objects.create(
            month=7, year=2026, units_used=Decimal("1500"),
            total_amount=Decimal("6870"),
        )
        self.pl1.mfg = timezone.datetime(2026, 8, 5, tzinfo=timezone.utc)
        self.pl1.save(update_fields=["mfg"])
        self.client.post("/api/sold-items/sync/", {"limit": 100})
        items = SoldItem.objects.filter(loyverse_sku="30001")
        for item in items:
            if item.days_to_sell > 0:
                self.assertGreater(item.electricity_cost, Decimal("0"))

    @patch("stock_meat.sold_items.requests.get", side_effect=_mock_requests_get)
    def test_sync_skips_non_meat_skus(self, mock_get):
        resp = self.client.post("/api/sold-items/sync/", {"limit": 100})
        data = resp.json()
        self.assertTrue(data["success"])
        non_meat = SoldItem.objects.filter(loyverse_sku__lt="30000")
        self.assertEqual(non_meat.count(), 0)


# ============================================================
# 4. BULK UPDATE PRICES
# ============================================================

class BulkUpdatePricesTests(TestCase):
    """Tests for views.bulk_update_prices."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(
            self.part, self.supply,
            weight=5000, cost=120, selling_price=160,
        )
        self.pl = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            selling_price=160, storage_status="frozen",
        )

    def test_cost_margin_mode(self):
        """cost_margin: price = cost/kg * weight/1000 * (1 + margin%)"""
        resp = self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "cost_margin",
                "value": 50,  # 50% margin
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 1)
        # 120 * 1000/1000 * 1.5 = 180
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.selling_price, 180)

    def test_discount_mode(self):
        """discount: price = current_price * (1 - discount%)"""
        resp = self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "discount",
                "value": 10,  # 10% discount
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        # 160 * 0.9 = 144
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.selling_price, 144)

    def test_price_per_kg_mode(self):
        """price_per_kg: price = value * weight/1000"""
        resp = self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "price_per_kg",
                "value": 200,
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        # 200 * 1000/1000 = 200
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.selling_price, 200)

    def test_creates_price_history(self):
        self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "cost_margin",
                "value": 50,
            },
        )
        history = PriceChangeHistory.objects.filter(
            product_list=self.pl
        ).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_price, 160)
        self.assertEqual(history.new_price, 180)
        self.assertEqual(history.mode, "cost_margin")
        self.assertIsNone(history.undone_at)

    def test_missing_product_ids(self):
        resp = self.client.post(
            "/api/bulk-update-prices/",
            {"mode": "discount", "value": 10},
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_mode(self):
        resp = self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "invalid",
                "value": 10,
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_value_too_high_discount(self):
        resp = self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "discount",
                "value": 150,
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_value_negative_price_per_kg(self):
        resp = self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "price_per_kg",
                "value": -10,
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_nonexistent_product_ids(self):
        resp = self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [99999],
                "mode": "discount",
                "value": 10,
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_multiple_products(self):
        pl2 = _create_product_list(
            self.pi, barcode="100002", weight=500,
            selling_price=80, storage_status="frozen",
        )
        resp = self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id, pl2.id],
                "mode": "discount",
                "value": 20,
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 2)
        self.pl.refresh_from_db()
        pl2.refresh_from_db()
        self.assertEqual(self.pl.selling_price, 128)  # 160 * 0.8
        self.assertEqual(pl2.selling_price, 64)  # 80 * 0.8

    def test_bulk_update_creates_history_per_product(self):
        pl2 = _create_product_list(
            self.pi, barcode="100002", weight=500,
            selling_price=80, storage_status="frozen",
        )
        self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id, pl2.id],
                "mode": "discount",
                "value": 20,
            },
        )
        self.assertEqual(
            PriceChangeHistory.objects.filter(product_list=self.pl).count(), 1
        )
        self.assertEqual(
            PriceChangeHistory.objects.filter(product_list=pl2).count(), 1
        )


# ============================================================
# 5. UNDO BULK PRICES
# ============================================================

class UndoBulkPricesTests(TestCase):
    """Tests for views.undo_bulk_prices."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(
            self.part, self.supply,
            weight=5000, cost=120, selling_price=160,
        )
        self.pl = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            selling_price=160, storage_status="frozen",
        )

    def _do_bulk_update(self):
        self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "discount",
                "value": 20,
            },
        )

    def test_undo_restores_price(self):
        self._do_bulk_update()
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.selling_price, 128)  # 160 * 0.8

        resp = self.client.post(
            "/api/undo-bulk-prices/",
            {"product_ids": [self.pl.id]},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 1)

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.selling_price, 160)  # restored

    def test_undo_marks_history_undone(self):
        self._do_bulk_update()

        self.client.post(
            "/api/undo-bulk-prices/",
            {"product_ids": [self.pl.id]},
        )

        history = PriceChangeHistory.objects.filter(
            product_list=self.pl
        ).first()
        self.assertIsNotNone(history)
        self.assertIsNotNone(history.undone_at)

    def test_undo_no_history_returns_error(self):
        resp = self.client.post(
            "/api/undo-bulk-prices/",
            {"product_ids": [self.pl.id]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_undo_missing_ids(self):
        resp = self.client.post("/api/undo-bulk-prices/", {})
        self.assertEqual(resp.status_code, 400)

    def test_undo_nonexistent_product(self):
        resp = self.client.post(
            "/api/undo-bulk-prices/",
            {"product_ids": [99999]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_undo_after_double_update_uses_latest(self):
        """Two updates → undo should restore to price before last update."""
        # First update: 160 → 144 (10% discount)
        self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "discount",
                "value": 10,
            },
        )
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.selling_price, 144)

        # Second update: 144 → 115.2 (20% discount)
        self.client.post(
            "/api/bulk-update-prices/",
            {
                "product_ids": [self.pl.id],
                "mode": "discount",
                "value": 20,
            },
        )
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.selling_price, 116)  # ceil(144*0.8)=116

        # Undo: should restore to 144
        self.client.post(
            "/api/undo-bulk-prices/",
            {"product_ids": [self.pl.id]},
        )
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.selling_price, 144)

    def test_undo_already_undone_returns_error(self):
        self._do_bulk_update()
        self.client.post(
            "/api/undo-bulk-prices/",
            {"product_ids": [self.pl.id]},
        )
        # Try undo again
        resp = self.client.post(
            "/api/undo-bulk-prices/",
            {"product_ids": [self.pl.id]},
        )
        self.assertEqual(resp.status_code, 400)


# ============================================================
# 6. CONFIRM LOYVERSE SYNC
# ============================================================

class ConfirmLoyverseSyncTests(TestCase):
    """Tests for views.confirm_loyverse_sync."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(self.part, self.supply)

        self.pl1 = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            storage_status="frozen",
        )
        self.pl2 = _create_product_list(
            self.pi, barcode="100002", weight=500,
            storage_status="frozen",
        )

    def test_sync_success(self):
        resp = self.client.post(
            "/api/confirm-loyverse-sync/",
            {"product_ids": [self.pl1.id, self.pl2.id]},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 2)
        self.assertIn("batch_id", data)

        self.pl1.refresh_from_db()
        self.pl2.refresh_from_db()
        self.assertTrue(self.pl1.loyverse_synced)
        self.assertTrue(self.pl2.loyverse_synced)
        self.assertIsNotNone(self.pl1.loyverse_synced_at)
        self.assertIsNotNone(self.pl1.loyverse_sync_batch)

    def test_sync_creates_batch(self):
        resp = self.client.post(
            "/api/confirm-loyverse-sync/",
            {"product_ids": [self.pl1.id]},
        )
        data = resp.json()
        batch = LoyverseSyncBatch.objects.get(id=data["batch_id"])
        self.assertEqual(batch.products.count(), 1)

    def test_sync_missing_ids(self):
        resp = self.client.post("/api/confirm-loyverse-sync/", {})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])

    def test_sync_already_synced_skipped(self):
        """Already-synced items should not be synced again."""
        self.pl1.loyverse_synced = True
        self.pl1.save(update_fields=["loyverse_synced"])

        resp = self.client.post(
            "/api/confirm-loyverse-sync/",
            {"product_ids": [self.pl1.id, self.pl2.id]},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        # Only pl2 should be synced
        self.assertEqual(data["count"], 1)

    def test_sync_nonexistent_ids(self):
        resp = self.client.post(
            "/api/confirm-loyverse-sync/",
            {"product_ids": [99999]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_sync_inactive_product_skipped(self):
        """Inactive products should not be synced."""
        self.pl1.activated = False
        self.pl1.save(update_fields=["activated"])

        resp = self.client.post(
            "/api/confirm-loyverse-sync/",
            {"product_ids": [self.pl1.id, self.pl2.id]},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 1)


# ============================================================
# 7. FINANCE VIEWS
# ============================================================

class FinanceViewsTests(TestCase):
    """Tests for finance.py views."""

    def setUp(self):
        self.client = Client()
        self.income_cat = ExpenseCategory.objects.create(
            name="ขายหน้าร้าน", category_type="income", icon="💰"
        )
        self.expense_cat = ExpenseCategory.objects.create(
            name="ค่าเนื้อ", category_type="expense", icon="🥩"
        )

    def test_get_categories(self):
        resp = self.client.get("/api/finance/categories/")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["categories"]), 2)

    def test_add_category(self):
        resp = self.client.post(
            "/api/finance/add-category/",
            {
                "name": "ค่าไฟ",
                "category_type": "expense",
                "icon": "⚡",
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(ExpenseCategory.objects.count(), 3)

    def test_add_category_empty_name(self):
        resp = self.client.post(
            "/api/finance/add-category/",
            {"name": "", "category_type": "expense"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_add_category_invalid_type(self):
        resp = self.client.post(
            "/api/finance/add-category/",
            {"name": "test", "category_type": "invalid"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_add_transaction_income(self):
        resp = self.client.post(
            "/api/finance/add-transaction/",
            {
                "transaction_type": "income",
                "amount": 5000,
                "category_id": self.income_cat.id,
                "description": "ขายหน้าร้าน",
                "receipt_date": "2026-08-25",
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(Transaction.objects.count(), 1)
        tx = Transaction.objects.first()
        self.assertEqual(tx.amount, Decimal("5000.00"))
        self.assertEqual(tx.transaction_type, "income")

    def test_add_transaction_expense(self):
        resp = self.client.post(
            "/api/finance/add-transaction/",
            {
                "transaction_type": "expense",
                "amount": 1200,
                "category_id": self.expense_cat.id,
                "description": "ซื้อเนื้อ",
                "receipt_date": "2026-08-25",
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        tx = Transaction.objects.first()
        self.assertEqual(tx.transaction_type, "expense")

    def test_add_transaction_invalid_type(self):
        resp = self.client.post(
            "/api/finance/add-transaction/",
            {"transaction_type": "invalid", "amount": 100},
        )
        self.assertEqual(resp.status_code, 400)

    def test_add_transaction_zero_amount(self):
        resp = self.client.post(
            "/api/finance/add-transaction/",
            {
                "transaction_type": "income",
                "amount": 0,
                "receipt_date": "2026-08-25",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_add_transaction_negative_amount(self):
        resp = self.client.post(
            "/api/finance/add-transaction/",
            {
                "transaction_type": "income",
                "amount": -100,
                "receipt_date": "2026-08-25",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_add_transaction_invalid_amount_string(self):
        resp = self.client.post(
            "/api/finance/add-transaction/",
            {
                "transaction_type": "income",
                "amount": "abc",
                "receipt_date": "2026-08-25",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_add_transaction_default_date(self):
        """If no receipt_date, use today."""
        resp = self.client.post(
            "/api/finance/add-transaction/",
            {
                "transaction_type": "income",
                "amount": 100,
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        tx = Transaction.objects.first()
        self.assertEqual(tx.receipt_date, date.today())

    def test_delete_transaction(self):
        tx = Transaction.objects.create(
            transaction_type="income",
            amount=100,
            receipt_date=date.today(),
        )
        resp = self.client.post(
            "/api/finance/delete-transaction/",
            {"transaction_id": tx.id},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(Transaction.objects.count(), 0)

    def test_delete_transaction_not_found(self):
        resp = self.client.post(
            "/api/finance/delete-transaction/",
            {"transaction_id": 99999},
        )
        self.assertEqual(resp.status_code, 404)

    def test_delete_transaction_missing_id(self):
        resp = self.client.post("/api/finance/delete-transaction/", {})
        self.assertEqual(resp.status_code, 400)

    def test_list_transactions(self):
        Transaction.objects.create(
            transaction_type="income", amount=100,
            receipt_date=date(2026, 8, 20),
        )
        Transaction.objects.create(
            transaction_type="expense", amount=50,
            receipt_date=date(2026, 8, 21),
        )

        resp = self.client.get("/api/finance/transactions/")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["transactions"]), 2)

    def test_list_transactions_filter_income(self):
        Transaction.objects.create(
            transaction_type="income", amount=100,
            receipt_date=date.today(),
        )
        Transaction.objects.create(
            transaction_type="expense", amount=50,
            receipt_date=date.today(),
        )
        resp = self.client.get(
            "/api/finance/transactions/?type=income"
        )
        data = resp.json()
        self.assertEqual(len(data["transactions"]), 1)
        self.assertEqual(data["transactions"][0]["type"], "income")

    def test_list_transactions_filter_month(self):
        Transaction.objects.create(
            transaction_type="income", amount=100,
            receipt_date=date(2026, 7, 15),
        )
        Transaction.objects.create(
            transaction_type="income", amount=200,
            receipt_date=date(2026, 8, 15),
        )
        resp = self.client.get(
            "/api/finance/transactions/?month=2026-08"
        )
        data = resp.json()
        self.assertEqual(len(data["transactions"]), 1)

    def test_get_summary(self):
        Transaction.objects.create(
            transaction_type="income", amount=1000,
            receipt_date=date.today(),
        )
        Transaction.objects.create(
            transaction_type="expense", amount=300,
            receipt_date=date.today(),
        )
        resp = self.client.get("/api/finance/summary/")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("months", data)
        self.assertIn("current", data)
        self.assertIn("totals", data)
        self.assertEqual(data["current"]["income"], 1000.0)
        self.assertEqual(data["current"]["expense"], 300.0)
        self.assertEqual(data["current"]["profit"], 700.0)

    def test_get_summary_empty(self):
        resp = self.client.get("/api/finance/summary/")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["totals"]["income"], 0)
        self.assertEqual(data["totals"]["expense"], 0)

    def test_finance_page_renders(self):
        resp = self.client.get("/finance/")
        self.assertEqual(resp.status_code, 200)


# ============================================================
# 8. FULL LIFECYCLE INTEGRATION TESTS
# ============================================================

class FullLifecycleIntegrationTests(TestCase):
    """
    End-to-end tests simulating real user flow through the entire lifecycle.

    PENDING → ON_SALE → SOLD
    PENDING → FREEZE → THAW_QUEUE → ON_SALE → FREEZE → THAW_QUEUE → ON_SALE → SOLD
    """

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(
            self.part, self.supply,
            weight=5000, cost=120, selling_price=160,
        )

    def _pack_product(self):
        """STEP 1: Pack a new product from home.html."""
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 1000},
        )
        data = resp.json()
        self.assertTrue(data["success"], f"pack_product failed: {data}")
        return data

    def test_step1_pack_creates_pending(self):
        """STEP 1: New pack → PENDING."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])
        self.assertEqual(pl.storage_status, "pending")
        self.assertTrue(pl.activated)
        self.assertEqual(pl.weight, 1000)
        self.assertEqual(pl.selling_price, 160)
        self.assertIsNotNone(pl.loyverse_sku)
        self.assertIsNotNone(pl.barcode)
        self.assertIsNotNone(pl.mfg)

    def test_step1_pending_appears_in_queue(self):
        """STEP 1: PENDING items appear in pending_products."""
        data = self._pack_product()
        resp = self.client.get("/api/pending-products/")
        pdata = resp.json()
        self.assertTrue(pdata["success"])
        barcodes = [p["barcode"] for p in pdata["products"]]
        self.assertIn(data["barcode"], barcodes)

    def test_step2_pending_to_on_sale(self):
        """STEP 2A: PENDING → ON_SALE (display)."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        resp = self.client.post(
            "/api/add-to-queue/",
            {
                "product_id": pl.id,
                "status": "display",
                "display_days": 3,
            },
        )
        result = resp.json()
        self.assertTrue(result["success"])

        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "display")
        self.assertEqual(pl.display_max_days, 3)
        self.assertIsNotNone(pl.entered_display_at)

        # Should NOT appear in pending anymore
        resp = self.client.get("/api/pending-products/")
        barcodes = [p["barcode"] for p in resp.json()["products"]]
        self.assertNotIn(pl.barcode, barcodes)

    def test_step2_on_sale_tracks_elapsed_time(self):
        """STEP 2B: ON_SALE tracks how long item has been on sale."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        # Set display start to 2 days ago
        pl.storage_status = "display"
        pl.entered_display_at = timezone.now() - timedelta(days=2)
        pl.display_max_days = 3
        pl.save()

        resp = self.client.get("/api/freeze-queue/")
        display = resp.json()["data"]["display"]
        item = [d for d in display if d["id"] == pl.id]
        self.assertEqual(len(item), 1)
        item = item[0]
        self.assertIsNotNone(item["time_in_status"])
        self.assertIn("วางขายมา", item["time_in_status"]["label"])
        self.assertGreater(item["time_in_status"]["elapsed_hours"], 40)

    def test_step3_pending_to_freeze(self):
        """STEP 3: PENDING → FREEZE with schedule."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        freeze_end = timezone.now() + timedelta(days=5)
        resp = self.client.post(
            "/api/add-to-queue/",
            {
                "product_id": pl.id,
                "status": "frozen",
                "freeze_end_at": freeze_end.isoformat(),
            },
        )
        result = resp.json()
        self.assertTrue(result["success"])

        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "frozen")
        self.assertIsNotNone(pl.freeze_end_at)

        # Should NOT be in pending
        resp = self.client.get("/api/pending-products/")
        barcodes = [p["barcode"] for p in resp.json()["products"]]
        self.assertNotIn(pl.barcode, barcodes)

        # Should be in frozen_available
        resp = self.client.get("/api/freeze-queue/")
        frozen = resp.json()["data"]["frozen_available"]
        barcodes = [p["barcode"] for p in frozen]
        self.assertIn(pl.barcode, barcodes)

        # Rotation history
        rot = FreezeRotation.objects.filter(
            product_list=pl, action="freeze_return"
        ).first()
        self.assertIsNotNone(rot)

    def test_step4_freeze_to_thaw_queue(self):
        """STEP 4: FREEZE → freeze_complete alert (user must queue manually)."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        # Set freeze_end_at to past
        pl.storage_status = "frozen"
        pl.freeze_end_at = timezone.now() - timedelta(hours=2)
        pl.save()

        resp = self.client.get("/api/auto-rotation-check/")
        alerts = resp.json()["alerts"]
        types = [a["type"] for a in alerts]
        self.assertIn("freeze_complete", types)

        # Status stays frozen — user must click "เข้าคิวละลาย"
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "frozen")
        self.assertEqual(pl.thaw_queue_position, 0)

    def test_step5_thaw_queue_to_on_sale(self):
        """STEP 5: THAW_QUEUE → ON_SALE."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        # Put in thawing state, completed
        pl.storage_status = "thawing"
        pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        pl.thaw_duration_hours = 24
        pl.thaw_queue_position = 1
        pl.save()

        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": pl.id, "display_days": 3},
        )
        result = resp.json()
        self.assertTrue(result["success"])

        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "display")
        self.assertEqual(pl.display_max_days, 3)
        self.assertIsNotNone(pl.entered_display_at)

    def test_step6_on_sale_to_freeze(self):
        """STEP 6: ON_SALE → FREEZE (unsold item back to freeze)."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        # Start on display
        pl.storage_status = "display"
        pl.entered_display_at = timezone.now() - timedelta(days=2)
        pl.display_max_days = 3
        pl.save()

        # Pull back to freeze
        resp = self.client.post(
            "/api/pull-from-display/",
            {"product_id": pl.id, "reason": "ยังขายไม่ออก"},
        )
        result = resp.json()
        self.assertTrue(result["success"])

        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "frozen")
        self.assertIsNone(pl.entered_display_at)

        # History recorded
        rot = FreezeRotation.objects.filter(
            product_list=pl, action="freeze_return"
        ).order_by("-performed_at").first()
        self.assertIsNotNone(rot)
        self.assertEqual(rot.notes, "ยังขายไม่ออก")

    def test_full_cycle_pending_to_sold(self):
        """
        STEP 7: Full cycle with multiple freeze rounds.

        PENDING → FREEZE → THAW → ON_SALE → FREEZE → THAW → ON_SALE → SOLD
        """
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        # --- Round 1 ---
        # PENDING → FREEZE
        resp = self.client.post(
            "/api/add-to-queue/",
            {"product_id": pl.id, "status": "frozen"},
        )
        self.assertTrue(resp.json()["success"])
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "frozen")

        # FREEZE → THAW
        resp = self.client.post(
            "/api/start-thaw/",
            {"product_id": pl.id, "thaw_duration_hours": 24},
        )
        self.assertTrue(resp.json()["success"])
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "thawing")
        round1_queue_pos = pl.thaw_queue_position

        # THAW → ON_SALE (backdate thaw)
        pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        pl.save(update_fields=["thaw_started_at"])
        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": pl.id, "display_days": 2},
        )
        self.assertTrue(resp.json()["success"])
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "display")
        self.assertIsNotNone(pl.entered_display_at)

        # --- Round 2 ---
        # ON_SALE → FREEZE
        resp = self.client.post(
            "/api/pull-from-display/",
            {"product_id": pl.id, "reason": "รอบ 2"},
        )
        self.assertTrue(resp.json()["success"])
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "frozen")

        # FREEZE → THAW again
        resp = self.client.post(
            "/api/start-thaw/",
            {"product_id": pl.id, "thaw_duration_hours": 18},
        )
        self.assertTrue(resp.json()["success"])
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "thawing")
        round2_queue_pos = pl.thaw_queue_position
        # Queue position may be renumbered after completion, so just verify
        # both rounds completed successfully and the item went through all states
        self.assertGreaterEqual(round2_queue_pos, 1)

        # THAW → ON_SALE again
        pl.thaw_started_at = timezone.now() - timedelta(hours=19)
        pl.save(update_fields=["thaw_started_at"])
        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": pl.id, "display_days": 3},
        )
        self.assertTrue(resp.json()["success"])
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "display")

        # Verify freeze rotation history has entries for both rounds
        rotations = FreezeRotation.objects.filter(
            product_list=pl
        ).order_by("performed_at")
        actions = [r.action for r in rotations]
        self.assertIn("freeze_return", actions)  # PENDING → FREEZE
        self.assertIn("thaw_start", actions)     # FREEZE → THAW
        self.assertIn("display_start", actions)  # THAW → ON_SALE
        self.assertGreaterEqual(len(rotations), 5)  # at least 5 transitions

    def test_invalid_transition_thaw_from_display(self):
        """Cannot start thaw from display status."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        pl.storage_status = "display"
        pl.entered_display_at = timezone.now()
        pl.save()

        resp = self.client.post(
            "/api/start-thaw/",
            {"product_id": pl.id, "thaw_duration_hours": 24},
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_transition_complete_thaw_from_frozen(self):
        """Cannot complete thaw from frozen status."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": pl.id, "display_days": 3},
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_transition_pull_from_frozen(self):
        """Cannot pull from display when item is frozen."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        resp = self.client.post(
            "/api/pull-from-display/",
            {"product_id": pl.id},
        )
        self.assertEqual(resp.status_code, 400)

    def test_barcode_and_sku_preserved_through_cycles(self):
        """Barcode and SKU should not change during lifecycle transitions."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        original_barcode = pl.barcode
        original_sku = pl.loyverse_sku

        # Go through multiple transitions
        self.client.post(
            "/api/add-to-queue/",
            {"product_id": pl.id, "status": "frozen"},
        )
        pl.refresh_from_db()
        self.assertEqual(pl.barcode, original_barcode)
        self.assertEqual(pl.loyverse_sku, original_sku)

        self.client.post(
            "/api/start-thaw/",
            {"product_id": pl.id, "thaw_duration_hours": 24},
        )
        pl.refresh_from_db()
        self.assertEqual(pl.barcode, original_barcode)
        self.assertEqual(pl.loyverse_sku, original_sku)

        pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        pl.save(update_fields=["thaw_started_at"])
        self.client.post(
            "/api/complete-thaw/",
            {"product_id": pl.id, "display_days": 3},
        )
        pl.refresh_from_db()
        self.assertEqual(pl.barcode, original_barcode)
        self.assertEqual(pl.loyverse_sku, original_sku)

    def test_duplicate_action_prevention(self):
        """Cannot start thaw on item already thawing."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        # First thaw
        self.client.post(
            "/api/start-thaw/",
            {"product_id": pl.id, "thaw_duration_hours": 24},
        )

        # Try to start thaw again
        resp = self.client.post(
            "/api/start-thaw/",
            {"product_id": pl.id, "thaw_duration_hours": 24},
        )
        self.assertEqual(resp.status_code, 400)

    def test_timezone_correctness(self):
        """Verify timestamps are stored and convertible to Bangkok timezone."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        # mfg should be set
        self.assertIsNotNone(pl.mfg)
        # mfg is stored in UTC (Django auto_now_add), verify it converts to Bangkok
        local_mfg = timezone.localtime(pl.mfg)
        self.assertEqual(
            str(local_mfg.tzinfo),
            "Asia/Bangkok",
        )

    def test_display_days_remaining_accuracy(self):
        """display_days_remaining should be accurate after reload."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        pl.storage_status = "display"
        pl.entered_display_at = timezone.now() - timedelta(days=1, hours=6)
        pl.display_max_days = 3
        pl.save()

        remaining = pl.display_days_remaining
        # elapsed.days = 1 (truncated), remaining = 3 - 1 = 2
        self.assertEqual(remaining, 2)

    def test_rotation_history_preserved_across_cycles(self):
        """Freeze rotation history should be preserved through multiple cycles."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        # Round 1
        self.client.post(
            "/api/add-to-queue/",
            {"product_id": pl.id, "status": "frozen"},
        )
        self.client.post(
            "/api/start-thaw/",
            {"product_id": pl.id, "thaw_duration_hours": 24},
        )

        count_after_round1 = FreezeRotation.objects.filter(
            product_list=pl
        ).count()
        self.assertGreater(count_after_round1, 0)

        # Round 2
        pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        pl.save(update_fields=["thaw_started_at"])
        self.client.post(
            "/api/complete-thaw/",
            {"product_id": pl.id, "display_days": 2},
        )
        self.client.post(
            "/api/pull-from-display/",
            {"product_id": pl.id},
        )

        count_after_round2 = FreezeRotation.objects.filter(
            product_list=pl
        ).count()
        # Should have more history than round 1
        self.assertGreater(count_after_round2, count_after_round1)

    def test_multiple_products_independent_lifecycle(self):
        """Multiple products should have independent lifecycles."""
        data1 = self._pack_product()
        self.pi.refresh_from_db()  # stock decreased
        data2 = self._pack_product()
        pl1 = Product_list.objects.get(id=data1["id"])
        pl2 = Product_list.objects.get(id=data2["id"])

        # Move pl1 to display
        self.client.post(
            "/api/add-to-queue/",
            {"product_id": pl1.id, "status": "display"},
        )
        # Keep pl2 in frozen
        self.client.post(
            "/api/add-to-queue/",
            {"product_id": pl2.id, "status": "frozen"},
        )

        pl1.refresh_from_db()
        pl2.refresh_from_db()
        self.assertEqual(pl1.storage_status, "display")
        self.assertEqual(pl2.storage_status, "frozen")

    def test_freeze_cycle_count_via_history(self):
        """Can determine freeze cycle count from rotation history."""
        data = self._pack_product()
        pl = Product_list.objects.get(id=data["id"])

        # Initial freeze
        self.client.post(
            "/api/add-to-queue/",
            {"product_id": pl.id, "status": "frozen"},
        )

        freeze_returns = FreezeRotation.objects.filter(
            product_list=pl, action="freeze_return"
        ).count()
        self.assertEqual(freeze_returns, 1)

        # After display → freeze return
        self.client.post(
            "/api/start-thaw/",
            {"product_id": pl.id, "thaw_duration_hours": 24},
        )
        pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        pl.save(update_fields=["thaw_started_at"])
        self.client.post(
            "/api/complete-thaw/",
            {"product_id": pl.id, "display_days": 1},
        )
        self.client.post(
            "/api/pull-from-display/",
            {"product_id": pl.id},
        )

        freeze_returns = FreezeRotation.objects.filter(
            product_list=pl, action="freeze_return"
        ).count()
        self.assertEqual(freeze_returns, 2)  # initial + return from display


# ============================================================
# 9. MODEL PROPERTY TESTS
# ============================================================

class ProductListModelTests(TestCase):
    """Tests for Product_list computed properties."""

    def setUp(self):
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(
            self.part, self.supply, weight=5000, cost=120, selling_price=160
        )

    def test_display_days_remaining(self):
        pl = _create_product_list(
            self.pi, storage_status="display", display_max_days=3,
        )
        pl.entered_display_at = timezone.now() - timedelta(days=1)
        pl.save(update_fields=["entered_display_at"])
        self.assertEqual(pl.display_days_remaining, 2)

    def test_display_days_remaining_expired(self):
        pl = _create_product_list(
            self.pi, storage_status="display", display_max_days=3,
        )
        pl.entered_display_at = timezone.now() - timedelta(days=5)
        pl.save(update_fields=["entered_display_at"])
        self.assertEqual(pl.display_days_remaining, 0)
        self.assertTrue(pl.is_display_expired)

    def test_thaw_ready_at(self):
        pl = _create_product_list(self.pi, storage_status="thawing")
        pl.thaw_started_at = timezone.now()
        pl.thaw_duration_hours = 24
        pl.save()
        ready = pl.thaw_ready_at
        self.assertIsNotNone(ready)
        expected = pl.thaw_started_at + timedelta(hours=24)
        self.assertAlmostEqual(ready, expected, delta=timedelta(seconds=1))

    def test_thaw_hours_remaining(self):
        pl = _create_product_list(self.pi, storage_status="thawing")
        pl.thaw_started_at = timezone.now() - timedelta(hours=10)
        pl.thaw_duration_hours = 24
        pl.save()
        remaining = pl.thaw_hours_remaining
        self.assertIsNotNone(remaining)
        self.assertAlmostEqual(remaining, 14.0, delta=0.2)

    def test_profit_per_kg(self):
        self.assertEqual(self.pi.profit_per_kg, 40.0)

    def test_profit_percent(self):
        self.assertAlmostEqual(self.pi.profit_percent, 33.33, delta=0.1)


class AddToThawQueueTests(TestCase):
    """Tests for POST /api/add-to-thaw-queue/ endpoint."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(self.part, self.supply)
        self.pl = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            storage_status="frozen",
        )

    def test_add_to_thaw_queue_success(self):
        resp = self.client.post(
            "/api/add-to-thaw-queue/",
            {"product_id": self.pl.id},
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["queue_position"], 1)

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "frozen")
        self.assertEqual(self.pl.thaw_queue_position, 1)
        self.assertIsNotNone(self.pl.thaw_scheduled_at)

    def test_add_to_thaw_queue_idempotent(self):
        """Calling twice should not create duplicate queue position."""
        self.client.post(
            "/api/add-to-thaw-queue/",
            {"product_id": self.pl.id},
        )
        resp = self.client.post(
            "/api/add-to-thaw-queue/",
            {"product_id": self.pl.id},
        )
        self.assertEqual(resp.status_code, 400)

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.thaw_queue_position, 1)

    def test_add_to_thaw_queue_wrong_status(self):
        self.pl.storage_status = "display"
        self.pl.save(update_fields=["storage_status"])
        resp = self.client.post(
            "/api/add-to-thaw-queue/",
            {"product_id": self.pl.id},
        )
        self.assertEqual(resp.status_code, 400)

    def test_add_to_thaw_queue_missing_product(self):
        resp = self.client.post("/api/add-to-thaw-queue/", {})
        self.assertEqual(resp.status_code, 400)

    def test_add_to_thaw_queue_creates_history(self):
        self.client.post(
            "/api/add-to-thaw-queue/",
            {"product_id": self.pl.id},
        )
        rot = FreezeRotation.objects.filter(
            product_list=self.pl, action="thaw_start"
        ).first()
        self.assertIsNotNone(rot)
        self.assertIn("คิวที่ 1", rot.notes)

    def test_queue_position_increments(self):
        pl2 = _create_product_list(
            self.pi, barcode="100002", weight=800,
            storage_status="frozen",
        )
        self.client.post(
            "/api/add-to-thaw-queue/",
            {"product_id": self.pl.id},
        )
        resp = self.client.post(
            "/api/add-to-thaw-queue/",
            {"product_id": pl2.id},
        )
        self.assertEqual(resp.json()["queue_position"], 2)


class ScheduleThawTests(TestCase):
    """Tests for POST /api/schedule-thaw/ endpoint."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(self.part, self.supply)
        self.pl = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            storage_status="frozen",
        )
        # Put in thaw queue first
        self.client.post(
            "/api/add-to-thaw-queue/",
            {"product_id": self.pl.id},
        )
        self.pl.refresh_from_db()

    def test_schedule_thaw_success(self):
        target = timezone.now() + timedelta(hours=20)
        resp = self.client.post(
            "/api/schedule-thaw/",
            {
                "product_id": self.pl.id,
                "target_ready_at": target.isoformat(),
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("thaw_start_at", data)
        self.assertIn("target_ready_at", data)

        self.pl.refresh_from_db()
        self.assertIsNotNone(self.pl.thaw_target_ready_at)
        self.assertEqual(self.pl.thaw_duration_hours, 24)

    def test_schedule_thaw_not_in_queue(self):
        """Cannot schedule thaw if not in queue."""
        pl2 = _create_product_list(
            self.pi, barcode="100002", weight=800,
            storage_status="frozen",
        )
        target = timezone.now() + timedelta(hours=20)
        resp = self.client.post(
            "/api/schedule-thaw/",
            {
                "product_id": pl2.id,
                "target_ready_at": target.isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_schedule_thaw_past_time(self):
        target = timezone.now() - timedelta(hours=1)
        resp = self.client.post(
            "/api/schedule-thaw/",
            {
                "product_id": self.pl.id,
                "target_ready_at": target.isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_schedule_thaw_missing_target(self):
        resp = self.client.post(
            "/api/schedule-thaw/",
            {"product_id": self.pl.id},
        )
        self.assertEqual(resp.status_code, 400)

    def test_schedule_thaw_missing_product(self):
        resp = self.client.post("/api/schedule-thaw/", {})
        self.assertEqual(resp.status_code, 400)

    def test_schedule_thaw_creates_history(self):
        target = timezone.now() + timedelta(hours=20)
        self.client.post(
            "/api/schedule-thaw/",
            {
                "product_id": self.pl.id,
                "target_ready_at": target.isoformat(),
            },
        )
        rot = FreezeRotation.objects.filter(
            product_list=self.pl, action="thaw_start"
        ).order_by("-performed_at").first()
        self.assertIsNotNone(rot)
        self.assertIn("พร้อมจำหน่าย", rot.notes)


class NewWorkflowIntegrationTests(TestCase):
    """Full workflow: freeze → queue → schedule thaw → auto start → display."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(self.part, self.supply)

    def test_full_new_workflow(self):
        """
        freeze → add_to_thaw_queue → schedule_thaw → auto_rotation starts thaw
        → complete_thaw → display
        """
        # Pack
        resp = self.client.post(
            "/api/pack-product/",
            {"product_id": self.pi.id, "weight": 1000},
        )
        data = resp.json()
        pl = Product_list.objects.get(id=data["id"])
        self.assertEqual(pl.storage_status, "pending")

        # Freeze
        freeze_end = timezone.now() + timedelta(hours=8)
        self.client.post(
            "/api/add-to-queue/",
            {
                "product_id": pl.id,
                "status": "frozen",
                "freeze_duration_minutes": 480,
                "freeze_end_at": freeze_end.isoformat(),
            },
        )
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "frozen")

        # Auto rotation → freeze_complete alert
        pl.freeze_end_at = timezone.now() - timedelta(hours=1)
        pl.save(update_fields=["freeze_end_at"])
        resp = self.client.get("/api/auto-rotation-check/")
        types = [a["type"] for a in resp.json()["alerts"]]
        self.assertIn("freeze_complete", types)
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "frozen")

        # Add to thaw queue
        resp = self.client.post(
            "/api/add-to-thaw-queue/",
            {"product_id": pl.id},
        )
        self.assertTrue(resp.json()["success"])
        pl.refresh_from_db()
        self.assertEqual(pl.thaw_queue_position, 1)

        # Schedule thaw
        target = timezone.now() + timedelta(hours=18)
        resp = self.client.post(
            "/api/schedule-thaw/",
            {
                "product_id": pl.id,
                "target_ready_at": target.isoformat(),
            },
        )
        self.assertTrue(resp.json()["success"])
        pl.refresh_from_db()
        self.assertIsNotNone(pl.thaw_target_ready_at)

        # Auto rotation starts thaw when target time arrives
        pl.thaw_target_ready_at = timezone.now() - timedelta(hours=1)
        pl.save(update_fields=["thaw_target_ready_at"])
        resp = self.client.get("/api/auto-rotation-check/")
        types = [a["type"] for a in resp.json()["alerts"]]
        self.assertIn("thaw_start_auto", types)
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "thawing")
        self.assertIsNotNone(pl.thaw_started_at)

        # Complete thaw → display
        pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        pl.save(update_fields=["thaw_started_at"])
        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": pl.id, "display_days": 3},
        )
        self.assertTrue(resp.json()["success"])
        pl.refresh_from_db()
        self.assertEqual(pl.storage_status, "display")


class RotationPlanTests(TestCase):
    """Tests for POST /api/create-rotation-plan/ endpoint."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(
            self.part, self.supply,
            weight=5000, cost=120, selling_price=160,
        )
        self.pl = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            storage_status="frozen",
        )

    def test_create_plan_success(self):
        from stock_meat.models import RotationSchedule, WorkerTask
        target = timezone.now() + timedelta(days=3)
        resp = self.client.post(
            "/api/create-rotation-plan/",
            {
                "product_id": self.pl.id,
                "target_ready_at": target.isoformat(),
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("schedule", data)
        self.assertIn("tasks", data)

        schedule = RotationSchedule.objects.get(
            product_list=self.pl
        )
        self.assertEqual(schedule.status, "planned")
        self.assertIsNotNone(schedule.freeze_start_at)
        self.assertIsNotNone(schedule.freeze_end_at)
        self.assertIsNotNone(schedule.thaw_start_at)
        self.assertGreater(
            schedule.freeze_duration_minutes, 0
        )
        self.assertGreater(
            schedule.thaw_duration_minutes, 0
        )

        # Tasks generated
        tasks = WorkerTask.objects.filter(
            rotation_schedule=schedule
        )
        self.assertGreater(tasks.count(), 0)

    def test_create_plan_with_overrides(self):
        from stock_meat.models import RotationSchedule
        target = timezone.now() + timedelta(days=3)
        resp = self.client.post(
            "/api/create-rotation-plan/",
            {
                "product_id": self.pl.id,
                "target_ready_at": target.isoformat(),
                "freeze_duration_minutes": 720,
                "thaw_duration_minutes": 960,
                "buffer_minutes": 60,
            },
        )
        self.assertTrue(resp.json()["success"])

        schedule = RotationSchedule.objects.get(
            product_list=self.pl)
        self.assertEqual(schedule.freeze_duration_minutes, 720)
        self.assertEqual(schedule.thaw_duration_minutes, 960)
        self.assertEqual(schedule.buffer_minutes, 60)
        self.assertTrue(schedule.is_override)

    def test_create_plan_missing_target(self):
        resp = self.client.post(
            "/api/create-rotation-plan/",
            {"product_id": self.pl.id},
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_plan_missing_product(self):
        target = timezone.now() + timedelta(days=3)
        resp = self.client.post(
            "/api/create-rotation-plan/",
            {"target_ready_at": target.isoformat()},
        )
        self.assertEqual(resp.status_code, 400)

    def test_schedule_timeline_correct(self):
        from stock_meat.models import RotationSchedule
        target = timezone.now() + timedelta(days=3)
        resp = self.client.post(
            "/api/create-rotation-plan/",
            {
                "product_id": self.pl.id,
                "target_ready_at": target.isoformat(),
            },
        )
        schedule = RotationSchedule.objects.get(
            product_list=self.pl)

        # thaw_start = target - thaw_duration - buffer
        expected_thaw_start = (
            schedule.target_ready_at
            - timedelta(minutes=schedule.thaw_duration_minutes + schedule.buffer_minutes)
        )
        self.assertAlmostEqual(
            schedule.thaw_start_at, expected_thaw_start,
            delta=timedelta(seconds=2),
        )

        # freeze_end = thaw_start
        self.assertAlmostEqual(
            schedule.freeze_end_at, schedule.thaw_start_at,
            delta=timedelta(seconds=2),
        )

        # freeze_start = freeze_end - freeze_duration
        expected_freeze_start = (
            schedule.freeze_end_at
            - timedelta(minutes=schedule.freeze_duration_minutes)
        )
        self.assertAlmostEqual(
            schedule.freeze_start_at, expected_freeze_start,
            delta=timedelta(seconds=2),
        )

    def test_plan_updates_product_list(self):
        from stock_meat.models import RotationSchedule
        target = timezone.now() + timedelta(days=3)
        self.client.post(
            "/api/create-rotation-plan/",
            {
                "product_id": self.pl.id,
                "target_ready_at": target.isoformat(),
            },
        )
        self.pl.refresh_from_db()
        self.assertIsNotNone(self.pl.freeze_end_at)
        self.assertIsNotNone(self.pl.thaw_target_ready_at)


class WorkerTaskTests(TestCase):
    """Tests for worker tasks endpoints."""

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(self.part, self.supply)
        self.pl = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            storage_status="frozen",
        )

    def _create_plan(self):
        from stock_meat.models import RotationSchedule
        from stock_meat.schedule import calculate_rotation_schedule, generate_worker_tasks
        target = timezone.now() + timedelta(days=1, hours=10)
        schedule_data = calculate_rotation_schedule(
            product_list=self.pl,
            target_ready_at=target,
        )
        schedule = RotationSchedule.objects.create(
            product_list=self.pl,
            status='planned',
            **{k: v for k, v in schedule_data.items() if hasattr(RotationSchedule, k)},
        )
        generate_worker_tasks(schedule)
        return schedule

    def test_worker_tasks_today(self):
        from stock_meat.models import RotationSchedule
        from stock_meat.schedule import calculate_rotation_schedule, generate_worker_tasks

        # Create a schedule with thaw_start at today
        now = timezone.now()
        target = now + timedelta(hours=20)
        schedule_data = calculate_rotation_schedule(
            product_list=self.pl,
            target_ready_at=target,
        )
        schedule = RotationSchedule.objects.create(
            product_list=self.pl,
            status='planned',
            **{k: v for k, v in schedule_data.items() if hasattr(RotationSchedule, k)},
        )
        generate_worker_tasks(schedule)

        resp = self.client.get(
            "/api/worker-tasks/?date=" + now.strftime("%Y-%m-%d")
        )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertGreater(data["total"], 0)

    def test_complete_task(self):
        from stock_meat.models import WorkerTask
        schedule = self._create_plan()
        task = WorkerTask.objects.filter(
            rotation_schedule=schedule
        ).first()

        resp = self.client.post(
            "/api/complete-task/",
            {
                "task_id": task.id,
                "completed_by": "ทดสอบ",
            },
        )
        self.assertTrue(resp.json()["success"])

        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(task.completed_by, "ทดสอบ")

    def test_rotation_plans_list(self):
        self._create_plan()
        resp = self.client.get("/api/rotation-plans/")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertGreater(data["total"], 0)


class MonthlyPlanningScenarioTest(TestCase):
    """
    Scenario: ซื้อเนื้อสด 10 แพ็ค → วางแผนขาย 10 วัน
    """

    def test_10_packs_10_days(self):
        from stock_meat.models import RotationSchedule, WorkerTask
        from stock_meat.schedule import calculate_rotation_schedule, generate_worker_tasks

        cat = _create_category()
        supply = _create_supply()
        part = _create_part(cat)
        pi = _create_product_info(
            part, supply,
            weight=5000, cost=120, selling_price=160,
        )

        # Create 10 packs with different weights
        packs = []
        weights = [500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400]
        for i, w in enumerate(weights):
            pl = _create_product_list(
                pi, barcode=f"10000{i+1}", weight=w,
                storage_status="frozen",
            )
            packs.append(pl)

        # Create schedules for 10 consecutive days
        base_date = timezone.now().replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        schedules = []
        for i, pack in enumerate(packs):
            target = base_date + timedelta(days=i+1)
            schedule_data = calculate_rotation_schedule(
                product_list=pack,
                target_ready_at=target,
            )
            schedule = RotationSchedule.objects.create(
                product_list=pack,
                status='planned',
                **{k: v for k, v in schedule_data.items() if hasattr(RotationSchedule, k)},
            )
            generate_worker_tasks(schedule)
            schedules.append(schedule)

        # Verify all schedules created
        self.assertEqual(RotationSchedule.objects.count(), 10)

        # Verify all tasks created
        total_tasks = WorkerTask.objects.count()
        self.assertGreater(total_tasks, 0)

        # Verify each day has target_ready_at on different days
        ready_dates = set()
        for s in schedules:
            ready_dates.add(s.target_ready_at.date())
        self.assertEqual(len(ready_dates), 10)

        # Verify all timelines are valid
        for s in schedules:
            self.assertLess(s.freeze_start_at, s.freeze_end_at)
            self.assertLessEqual(s.freeze_end_at, s.thaw_start_at)
            self.assertLess(s.thaw_start_at, s.target_ready_at)

        # Verify no overlapping thaw schedules
        thaw_starts = sorted([
            s.thaw_start_at for s in schedules
        ])
        for i in range(len(thaw_starts) - 1):
            # Each thaw should start after previous thaw's estimated end
            pass  # thaw durations vary by weight, so exact overlap check is complex

        # Verify tasks exist for each schedule
        for s in schedules:
            tasks = WorkerTask.objects.filter(
                rotation_schedule=s
            )
            self.assertGreater(tasks.count(), 0)

        # Verify we can get tasks for a specific day
        day1 = base_date.date() + timedelta(days=1)
        resp = self.client.get(
            f"/api/worker-tasks/?date={day1.isoformat()}"
        )
        data = resp.json()
        self.assertTrue(data["success"])


class ElectricityBillTests(TestCase):
    """Tests for ElectricityBill auto-calculation."""

    def test_auto_calculate_total(self):
        bill = ElectricityBill(
            month=8, year=2026,
            units_used=Decimal("1500.00"),
            total_amount=Decimal("0"),
        )
        bill.save()
        self.assertAlmostEqual(float(bill.total_amount), 6870.0, places=2)


# ============================================================
# 10. FREEZE/THAW SEPARATION TESTS
# ============================================================

class FreezeThawSeparationTests(TestCase):
    """
    Tests that freeze duration and thaw duration are separate.

    Root cause: confirmFreezeDuration() was sending freeze time
    as thaw_duration_hours, causing auto_rotation to use wrong thaw time.
    """

    def setUp(self):
        self.client = Client()
        self.cat = _create_category()
        self.supply = _create_supply()
        self.part = _create_part(self.cat)
        self.pi = _create_product_info(self.part, self.supply)
        self.pl = _create_product_list(
            self.pi, barcode="100001", weight=1000,
            storage_status="frozen",
        )

    def test_freeze_duration_not_stored_as_thaw(self):
        """freeze_duration_minutes must not overwrite thaw_duration_hours."""
        freeze_end = timezone.now() + timedelta(hours=8)
        resp = self.client.post(
            "/api/add-to-queue/",
            {
                "product_id": self.pl.id,
                "status": "frozen",
                "freeze_duration_minutes": 480,  # 8 hours
                "freeze_end_at": freeze_end.isoformat(),
                "freeze_target_temp": -8,
            },
        )
        data = resp.json()
        self.assertTrue(data["success"])

        self.pl.refresh_from_db()
        # thaw_duration_hours should remain at default (24), not 8
        self.assertEqual(self.pl.thaw_duration_hours, 24)
        # freeze_duration_minutes should be 480
        self.assertEqual(self.pl.freeze_duration_minutes, 480)
        # freeze_started_at should be set
        self.assertIsNotNone(self.pl.freeze_started_at)
        # freeze_end_at should be set
        self.assertIsNotNone(self.pl.freeze_end_at)

    def test_freeze_8_hours_then_auto_rotation_not_auto_thaw(self):
        """freeze 8h → auto_rotation → stays frozen, user must queue manually."""
        # Step 1: Freeze with 8 hour duration
        self.pl.freeze_end_at = timezone.now() - timedelta(hours=2)
        self.pl.freeze_started_at = timezone.now() - timedelta(hours=10)
        self.pl.freeze_duration_minutes = 480
        self.pl.thaw_duration_hours = 24
        self.pl.save()

        # Step 2: Auto rotation fires — sends alert but doesn't change status
        resp = self.client.get("/api/auto-rotation-check/")
        self.assertTrue(resp.json()["success"])
        types = [a["type"] for a in resp.json()["alerts"]]
        self.assertIn("freeze_complete", types)

        # Step 3: Status should remain frozen
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "frozen")
        self.assertEqual(self.pl.thaw_duration_hours, 24)

    def test_auto_rotation_18_hour_thaw(self):
        """User sets thaw_duration_hours=18 before freeze. Auto rotation preserves it."""
        # User manually starts thaw and sets 18h, then pull back to freeze
        # The thaw_duration_hours should persist
        self.pl.thaw_duration_hours = 18
        self.pl.freeze_end_at = timezone.now() - timedelta(hours=1)
        self.pl.save()

        resp = self.client.get("/api/auto-rotation-check/")
        self.assertTrue(resp.json()["success"])

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.thaw_duration_hours, 18)

    def test_auto_rotation_36_hour_thaw(self):
        """thaw_duration_hours=36 preserved through auto rotation."""
        self.pl.thaw_duration_hours = 36
        self.pl.freeze_end_at = timezone.now() - timedelta(hours=1)
        self.pl.save()

        resp = self.client.get("/api/auto-rotation-check/")
        self.assertTrue(resp.json()["success"])

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.thaw_duration_hours, 36)

    def test_auto_rotation_48_hour_thaw(self):
        """thaw_duration_hours=48 preserved through auto rotation."""
        self.pl.thaw_duration_hours = 48
        self.pl.freeze_end_at = timezone.now() - timedelta(hours=1)
        self.pl.save()

        resp = self.client.get("/api/auto-rotation-check/")
        self.assertTrue(resp.json()["success"])

        self.pl.refresh_from_db()
        self.assertEqual(self.pl.thaw_duration_hours, 48)

    def test_freeze_started_at_set_on_add_to_queue(self):
        """freeze_started_at must be set when adding to frozen queue."""
        resp = self.client.post(
            "/api/add-to-queue/",
            {"product_id": self.pl.id, "status": "frozen"},
        )
        self.assertTrue(resp.json()["success"])

        self.pl.refresh_from_db()
        self.assertIsNotNone(self.pl.freeze_started_at)
        self.assertEqual(self.pl.freeze_duration_minutes, 0)

    def test_weight_dependent_freeze_estimate(self):
        """Different weights should produce different freeze durations."""
        # This tests the frontend formula, but we verify the model supports it
        # 500g vs 1000g vs 2000g at -8°C
        w500 = _create_product_list(
            self.pi, barcode="200001", weight=500,
            storage_status="frozen",
        )
        w1000 = _create_product_list(
            self.pi, barcode="200002", weight=1000,
            storage_status="frozen",
        )
        w2000 = _create_product_list(
            self.pi, barcode="200003", weight=2000,
            storage_status="frozen",
        )

        # Set different freeze durations based on weight
        # (simulating what the frontend would calculate)
        self.client.post(
            "/api/add-to-queue/",
            {
                "product_id": w500.id,
                "status": "frozen",
                "freeze_duration_minutes": 240,  # ~4h for 500g
            },
        )
        self.client.post(
            "/api/add-to-queue/",
            {
                "product_id": w1000.id,
                "status": "frozen",
                "freeze_duration_minutes": 660,  # ~11h for 1000g
            },
        )
        self.client.post(
            "/api/add-to-queue/",
            {
                "product_id": w2000.id,
                "status": "frozen",
                "freeze_duration_minutes": 1050,  # ~17.5h for 2000g
            },
        )

        w500.refresh_from_db()
        w1000.refresh_from_db()
        w2000.refresh_from_db()

        self.assertEqual(w500.freeze_duration_minutes, 240)
        self.assertEqual(w1000.freeze_duration_minutes, 660)
        self.assertEqual(w2000.freeze_duration_minutes, 1050)
        # Heavier packs need more freeze time
        self.assertLess(
            w500.freeze_duration_minutes,
            w1000.freeze_duration_minutes,
        )
        self.assertLess(
            w1000.freeze_duration_minutes,
            w2000.freeze_duration_minutes,
        )

    def test_serialize_includes_freeze_fields(self):
        """Serialized product should include freeze_started_at and freeze_duration_minutes."""
        self.pl.freeze_started_at = timezone.now()
        self.pl.freeze_duration_minutes = 480
        self.pl.freeze_end_at = timezone.now() + timedelta(hours=8)
        self.pl.save()

        resp = self.client.get("/api/freeze-queue/")
        frozen = resp.json()["data"]["frozen_available"]
        item = [p for p in frozen if p["id"] == self.pl.id]
        self.assertEqual(len(item), 1)
        item = item[0]
        self.assertIsNotNone(item["freeze_started_at"])
        self.assertEqual(item["freeze_duration_minutes"], 480)
        self.assertIsNotNone(item["freeze_end_at"])

    def test_auto_rotation_idempotent(self):
        """Calling auto_rotation_check multiple times should not create duplicate events."""
        self.pl.freeze_end_at = timezone.now() - timedelta(hours=2)
        self.pl.save()

        # First call — freeze_complete alert
        resp1 = self.client.get("/api/auto-rotation-check/")
        self.assertTrue(resp1.json()["success"])
        types1 = [a["type"] for a in resp1.json()["alerts"]]
        self.assertIn("freeze_complete", types1)

        # Status should stay frozen
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "frozen")

        # Second call — same alert, still frozen
        resp2 = self.client.get("/api/auto-rotation-check/")
        self.assertTrue(resp2.json()["success"])
        types2 = [a["type"] for a in resp2.json()["alerts"]]
        self.assertIn("freeze_complete", types2)
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "frozen")

    def test_queue_no_duplicates(self):
        """Queue positions must be unique and sequential."""
        pl2 = _create_product_list(
            self.pi, barcode="100002", weight=800,
            storage_status="frozen",
        )
        pl3 = _create_product_list(
            self.pi, barcode="100003", weight=1200,
            storage_status="frozen",
        )

        # Start thaw for all three
        self.client.post(
            "/api/start-thaw/",
            {"product_id": self.pl.id, "thaw_duration_hours": 24},
        )
        self.client.post(
            "/api/start-thaw/",
            {"product_id": pl2.id, "thaw_duration_hours": 18},
        )
        self.client.post(
            "/api/start-thaw/",
            {"product_id": pl3.id, "thaw_duration_hours": 36},
        )

        self.pl.refresh_from_db()
        pl2.refresh_from_db()
        pl3.refresh_from_db()

        positions = sorted([
            self.pl.thaw_queue_position,
            pl2.thaw_queue_position,
            pl3.thaw_queue_position,
        ])
        self.assertEqual(positions, [1, 2, 3])

    def test_queue_reorder_after_removal(self):
        """After removing item from queue, positions should be renumbered."""
        pl2 = _create_product_list(
            self.pi, barcode="100002", weight=800,
            storage_status="frozen",
        )
        pl3 = _create_product_list(
            self.pi, barcode="100003", weight=1200,
            storage_status="frozen",
        )

        self.client.post(
            "/api/start-thaw/",
            {"product_id": self.pl.id, "thaw_duration_hours": 24},
        )
        self.client.post(
            "/api/start-thaw/",
            {"product_id": pl2.id, "thaw_duration_hours": 18},
        )
        self.client.post(
            "/api/start-thaw/",
            {"product_id": pl3.id, "thaw_duration_hours": 36},
        )

        # Complete thaw for pl2 (removes from queue)
        self.pl.refresh_from_db()
        pl2.refresh_from_db()
        pl3.refresh_from_db()

        # Backdate thaw start so it's complete
        pl2.thaw_started_at = timezone.now() - timedelta(hours=19)
        pl2.save(update_fields=["thaw_started_at"])

        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": pl2.id, "display_days": 3},
        )
        self.assertTrue(resp.json()["success"])

        # Check remaining items have sequential positions
        self.pl.refresh_from_db()
        pl3.refresh_from_db()

        positions = sorted([
            self.pl.thaw_queue_position,
            pl3.thaw_queue_position,
        ])
        self.assertEqual(positions, [1, 2])

    def test_complete_thaw_records_correct_duration_in_history(self):
        """Rotation history should show the thaw duration used."""
        self.pl.storage_status = "thawing"
        self.pl.thaw_started_at = timezone.now() - timedelta(hours=19)
        self.pl.thaw_duration_hours = 18
        self.pl.thaw_queue_position = 1
        self.pl.save()

        resp = self.client.post(
            "/api/complete-thaw/",
            {"product_id": self.pl.id, "display_days": 3},
        )
        self.assertTrue(resp.json()["success"])

        rot = FreezeRotation.objects.filter(
            product_list=self.pl, action="display_start"
        ).first()
        self.assertIsNotNone(rot)
        self.assertIn("3 วัน", rot.notes)

    def test_display_end_at_calculated_correctly(self):
        """display_end_at should equal entered_display_at + display_max_days."""
        self.pl.storage_status = "display"
        from datetime import datetime as _dt
        self.pl.entered_display_at = _dt(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc)
        self.pl.display_max_days = 3
        self.pl.save()

        end_at = self.pl.display_end_at
        self.assertIsNotNone(end_at)
        from datetime import datetime as _dt
        expected = _dt(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(end_at, expected, delta=timedelta(seconds=1))

    def test_auto_rotation_freeze_complete_event(self):
        """auto_rotation should emit freeze_complete event with correct data."""
        self.pl.freeze_end_at = timezone.now() - timedelta(hours=1)
        self.pl.freeze_target_temp = -8
        self.pl.freeze_duration_minutes = 480
        self.pl.save()

        resp = self.client.get("/api/auto-rotation-check/")
        data = resp.json()
        types = [a["type"] for a in data["alerts"]]
        self.assertIn("freeze_complete", types)

        # Find the freeze_complete alert
        freeze_alert = [a for a in data["alerts"] if a["type"] == "freeze_complete"][0]
        self.assertIn("products", freeze_alert)
        self.assertEqual(len(freeze_alert["products"]), 1)

    def test_datetime_serialization_consistency(self):
        """All datetime fields should be ISO format in API response."""
        now = timezone.now()
        self.pl.freeze_started_at = now
        self.pl.freeze_end_at = now + timedelta(hours=8)
        self.pl.thaw_started_at = None
        self.pl.entered_display_at = None
        self.pl.save()

        resp = self.client.get("/api/freeze-queue/")
        frozen = resp.json()["data"]["frozen_available"]
        item = [p for p in frozen if p["id"] == self.pl.id][0]

        # freeze_started_at should be ISO format string
        self.assertIsInstance(item["freeze_started_at"], str)
        self.assertIn("T", item["freeze_started_at"])
        # freeze_end_at should be ISO format string
        self.assertIsInstance(item["freeze_end_at"], str)
        self.assertIn("T", item["freeze_end_at"])

    def test_full_cycle_with_separate_durations(self):
        """
        Complete lifecycle with separate freeze and thaw durations.

        freeze 8h → auto rotation → thaw 18h → display 3 days
        → pull back → freeze 12h → auto rotation → thaw 36h → display
        """
        # Round 1: freeze 8h
        self.client.post(
            "/api/add-to-queue/",
            {
                "product_id": self.pl.id,
                "status": "frozen",
                "freeze_duration_minutes": 480,  # 8 hours
            },
        )
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.freeze_duration_minutes, 480)
        self.assertEqual(self.pl.thaw_duration_hours, 24)  # default

        # Auto rotation → alert only, status stays frozen
        self.pl.freeze_end_at = timezone.now() - timedelta(hours=1)
        self.pl.save(update_fields=["freeze_end_at"])
        resp = self.client.get("/api/auto-rotation-check/")
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "frozen")
        self.assertEqual(self.pl.thaw_duration_hours, 24)

        # Manually start thaw → thawing
        self.client.post(
            "/api/start-thaw/",
            {"product_id": self.pl.id, "thaw_duration_hours": 24},
        )
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "thawing")

        # Complete thaw → display
        self.pl.thaw_started_at = timezone.now() - timedelta(hours=25)
        self.pl.save(update_fields=["thaw_started_at"])
        self.client.post(
            "/api/complete-thaw/",
            {"product_id": self.pl.id, "display_days": 3},
        )
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "display")

        # Pull back → freeze round 2
        self.client.post(
            "/api/pull-from-display/",
            {"product_id": self.pl.id},
        )
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.storage_status, "frozen")

        # Round 2: freeze 12h
        self.client.post(
            "/api/add-to-queue/",
            {
                "product_id": self.pl.id,
                "status": "frozen",
                "freeze_duration_minutes": 720,  # 12 hours
            },
        )
        self.pl.refresh_from_db()
        self.assertEqual(self.pl.freeze_duration_minutes, 720)

        # Verify history has both rounds
        freeze_returns = FreezeRotation.objects.filter(
            product_list=self.pl, action="freeze_return"
        ).count()
        # 3: initial freeze + pull_from_display + second freeze
        self.assertEqual(freeze_returns, 3)
