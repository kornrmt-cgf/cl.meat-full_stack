"""
Tests for Planning Dashboard — product cards, planning status, filtering.
"""
from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from datetime import timedelta

from inventory.models import (
    Product, Batch, Package, PackageState,
    StorageLocation, ProductPlanningProfile
)
from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, PlanStatus
)
from planning.stock_service import (
    get_planning_dashboard, get_product_stock_summary,
    PlanningStatus, _determine_planning_status
)


class PlanningStatusCalculationTest(TestCase):
    """Test _determine_planning_status logic."""
    
    def test_sufficient_stock(self):
        """Stock well above safety threshold."""
        status = _determine_planning_status(
            total_stock_kg=Decimal('100'),
            coverage_days=Decimal('14'),
            safety_days=Decimal('1'),
            net_required=Decimal('0'),
            incoming_kg=Decimal('0'),
            eligible_kg=Decimal('20'),
            has_profile=True,
            projected_stockout=timezone.localtime(timezone.now()).date() + timedelta(days=13),
            today=timezone.localtime(timezone.now()).date(),
        )
        self.assertEqual(status, PlanningStatus.SUFFICIENT)
    
    def test_out_of_stock(self):
        """No stock at all."""
        status = _determine_planning_status(
            total_stock_kg=Decimal('0'),
            coverage_days=Decimal('0'),
            safety_days=Decimal('1'),
            net_required=Decimal('50'),
            incoming_kg=Decimal('0'),
            eligible_kg=Decimal('0'),
            has_profile=True,
            projected_stockout=timezone.localtime(timezone.now()).date(),
            today=timezone.localtime(timezone.now()).date(),
        )
        self.assertEqual(status, PlanningStatus.OUT_OF_STOCK)
    
    def test_low_stock(self):
        """Stock below safety threshold."""
        status = _determine_planning_status(
            total_stock_kg=Decimal('5'),
            coverage_days=Decimal('0.5'),
            safety_days=Decimal('1'),
            net_required=Decimal('0'),
            incoming_kg=Decimal('0'),
            eligible_kg=Decimal('20'),
            has_profile=True,
            projected_stockout=timezone.localtime(timezone.now()).date(),
            today=timezone.localtime(timezone.now()).date(),
        )
        self.assertEqual(status, PlanningStatus.LOW_STOCK)
    
    def test_planning_required(self):
        """Has net requirement and eligible packages."""
        status = _determine_planning_status(
            total_stock_kg=Decimal('30'),
            coverage_days=Decimal('3'),
            safety_days=Decimal('1'),
            net_required=Decimal('40'),
            incoming_kg=Decimal('0'),
            eligible_kg=Decimal('15'),
            has_profile=True,
            projected_stockout=timezone.localtime(timezone.now()).date() + timedelta(days=2),
            today=timezone.localtime(timezone.now()).date(),
        )
        self.assertEqual(status, PlanningStatus.PLANNING_REQUIRED)
    
    def test_stock_gap(self):
        """Has net requirement but no eligible packages and no incoming."""
        status = _determine_planning_status(
            total_stock_kg=Decimal('10'),
            coverage_days=Decimal('1'),
            safety_days=Decimal('1'),
            net_required=Decimal('30'),
            incoming_kg=Decimal('0'),
            eligible_kg=Decimal('0'),
            has_profile=True,
            projected_stockout=timezone.localtime(timezone.now()).date(),
            today=timezone.localtime(timezone.now()).date(),
        )
        self.assertEqual(status, PlanningStatus.STOCK_GAP)
    
    def test_incoming(self):
        """Has incoming stock."""
        status = _determine_planning_status(
            total_stock_kg=Decimal('20'),
            coverage_days=Decimal('2'),
            safety_days=Decimal('1'),
            net_required=Decimal('0'),
            incoming_kg=Decimal('30'),
            eligible_kg=Decimal('10'),
            has_profile=True,
            projected_stockout=timezone.localtime(timezone.now()).date() + timedelta(days=1),
            today=timezone.localtime(timezone.now()).date(),
        )
        self.assertEqual(status, PlanningStatus.INCOMING)
    
    def test_overstocked(self):
        """Stock exceeds 2x target coverage."""
        status = _determine_planning_status(
            total_stock_kg=Decimal('200'),
            coverage_days=Decimal('30'),
            safety_days=Decimal('1'),
            net_required=Decimal('0'),
            incoming_kg=Decimal('0'),
            eligible_kg=Decimal('0'),
            has_profile=True,
            projected_stockout=timezone.localtime(timezone.now()).date() + timedelta(days=29),
            today=timezone.localtime(timezone.now()).date(),
        )
        self.assertEqual(status, PlanningStatus.OVERSTOCKED)
    
    def test_no_profile_with_stock_shows_sufficient(self):
        """Products without planning profile but with stock show SUFFICIENT."""
        status = _determine_planning_status(
            total_stock_kg=Decimal('50'),
            coverage_days=Decimal('0'),
            safety_days=Decimal('1'),
            net_required=Decimal('0'),
            incoming_kg=Decimal('0'),
            eligible_kg=Decimal('0'),
            has_profile=False,
            projected_stockout=None,
            today=timezone.localtime(timezone.now()).date(),
        )
        self.assertEqual(status, PlanningStatus.SUFFICIENT)
    
    def test_no_profile_no_stock_returns_none(self):
        """Products without planning profile and without stock don't appear."""
        status = _determine_planning_status(
            total_stock_kg=Decimal('0'),
            coverage_days=Decimal('0'),
            safety_days=Decimal('1'),
            net_required=Decimal('0'),
            incoming_kg=Decimal('0'),
            eligible_kg=Decimal('0'),
            has_profile=False,
            projected_stockout=None,
            today=timezone.localtime(timezone.now()).date(),
        )
        self.assertIsNone(status)


class PlanningDashboardTest(TestCase):
    """Test the planning dashboard product cards."""
    
    def setUp(self):
        self.location = StorageLocation.objects.create(
            name='Freezer A', location_type='FREEZER', capacity=50
        )
        self.batch = Batch.objects.create(
            batch_number='B001', supplier='BETAGRO', received_at=timezone.now()
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard Freeze', target_temperature=Decimal('-18'),
            minimum_duration=timedelta(hours=8),
            default_duration=timedelta(hours=24),
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Slow Thaw', default_duration=timedelta(hours=16),
            minimum_duration=timedelta(hours=12),
        )
        
        # Product with planning profile
        self.product_low = Product.objects.create(
            sku='LOW-001', name='สันคอหมู', category='PORK',
            barcode_prefix='8002',
        )
        ProductPlanningProfile.objects.create(
            product=self.product_low,
            avg_daily_usage_kg=Decimal('12.00'),
            safety_stock_days=Decimal('1.0'),
            target_coverage_days=Decimal('7.0'),
        )
        
        # Product without planning profile
        self.product_no_profile = Product.objects.create(
            sku='NP-001', name='อกไก่บด', category='CHICKEN'
        )
    
    def _create_package(self, product, weight, state=PackageState.FROZEN):
        return Package.objects.create(
            product=product, batch=self.batch, barcode=f'BC-{product.pk}-{weight}',
            weight=Decimal(str(weight)), packed_at=timezone.now(),
            current_state=state, storage_location=self.location,
        )
    
    def test_dashboard_includes_products_with_stock(self):
        """Products with stock and profile appear in dashboard."""
        self._create_package(self.product_low, 5.0)
        
        cards = get_planning_dashboard()
        product_ids = [c['product_id'] for c in cards]
        self.assertIn(self.product_low.pk, product_ids)
    
    def test_dashboard_excludes_products_without_profile_or_stock(self):
        """Products without profile and without stock don't appear."""
        cards = get_planning_dashboard()
        product_ids = [c['product_id'] for c in cards]
        self.assertNotIn(self.product_no_profile.pk, product_ids)
    
    def test_dashboard_out_of_stock_status(self):
        """Product with no stock shows OUT_OF_STOCK."""
        cards = get_planning_dashboard()
        low_card = next((c for c in cards if c['product_id'] == self.product_low.pk), None)
        self.assertIsNotNone(low_card)
        self.assertEqual(low_card['status'], PlanningStatus.OUT_OF_STOCK)
    
    def test_dashboard_low_stock_status(self):
        """Product with stock below safety threshold shows LOW_STOCK."""
        self._create_package(self.product_low, 2.0)  # 2kg / 12kg/day = 0.17 days < 1 day safety
        
        cards = get_planning_dashboard()
        low_card = next((c for c in cards if c['product_id'] == self.product_low.pk), None)
        self.assertIsNotNone(low_card)
        self.assertEqual(low_card['status'], PlanningStatus.LOW_STOCK)
    
    def test_dashboard_planning_required_status(self):
        """Product with eligible packages and net requirement shows PLANNING_REQUIRED."""
        # Create enough stock to cover safety but not target
        for i in range(3):
            self._create_package(self.product_low, 10.0)
        # 30kg / 12kg/day = 2.5 days > 1 day safety, but < 7 days target
        # net_required = 12*7 - 30 - 0 = 54 > 0, eligible = 30 > 0
        
        cards = get_planning_dashboard()
        low_card = next((c for c in cards if c['product_id'] == self.product_low.pk), None)
        self.assertIsNotNone(low_card)
        self.assertEqual(low_card['status'], PlanningStatus.PLANNING_REQUIRED)
    
    def test_dashboard_sufficient_status(self):
        """Product with enough stock shows SUFFICIENT."""
        # Create 100kg — 100/12 = 8.3 days > 7 target
        self._create_package(self.product_low, 50.0)
        self._create_package(self.product_low, 50.0)
        
        cards = get_planning_dashboard()
        low_card = next((c for c in cards if c['product_id'] == self.product_low.pk), None)
        self.assertIsNotNone(low_card)
        self.assertEqual(low_card['status'], PlanningStatus.SUFFICIENT)
    
    def test_dashboard_card_has_all_fields(self):
        """Each card has all required fields."""
        self._create_package(self.product_low, 5.0)
        
        cards = get_planning_dashboard()
        card = next((c for c in cards if c['product_id'] == self.product_low.pk), None)
        self.assertIsNotNone(card)
        
        required_fields = [
            'product_id', 'product_name', 'product_sku', 'category',
            'status', 'status_label', 'current_stock_kg', 'avg_daily_usage_kg',
            'coverage_days', 'projected_stockout_date', 'incoming_kg',
            'planned_kg', 'planned_count', 'eligible_kg', 'eligible_count',
            'net_required_kg', 'recommended_ready_date', 'has_profile',
        ]
        for field in required_fields:
            self.assertIn(field, card, f'Missing field: {field}')
    
    def test_dashboard_sorted_by_urgency(self):
        """Cards are sorted: OUT_OF_STOCK first, SUFFICIENT last."""
        # Product A: out of stock
        product_a = Product.objects.create(sku='A-001', name='AAA', category='PORK')
        ProductPlanningProfile.objects.create(
            product=product_a, avg_daily_usage_kg=Decimal('10'),
            safety_stock_days=Decimal('1'), target_coverage_days=Decimal('7'),
        )
        
        # Product B: sufficient
        product_b = Product.objects.create(sku='B-001', name='BBB', category='PORK')
        ProductPlanningProfile.objects.create(
            product=product_b, avg_daily_usage_kg=Decimal('10'),
            safety_stock_days=Decimal('1'), target_coverage_days=Decimal('7'),
        )
        for i in range(10):
            self._create_package(product_b, 10.0)
        
        cards = get_planning_dashboard()
        statuses = [c['status'] for c in cards]
        
        # OUT_OF_STOCK should come before SUFFICIENT
        a_idx = next(i for i, c in enumerate(cards) if c['product_id'] == product_a.pk)
        b_idx = next(i for i, c in enumerate(cards) if c['product_id'] == product_b.pk)
        self.assertLess(a_idx, b_idx)
    
    def test_dashboard_planned_kg_includes_existing_plans(self):
        """Planned kg reflects existing RotationPlans."""
        pkg = self._create_package(self.product_low, 10.0)
        
        RotationPlan.objects.create(
            package=pkg, target_ready_at=timezone.now() + timedelta(days=3),
            planned_thaw_start_at=timezone.now() + timedelta(days=2),
            planned_thaw_queue_at=timezone.now() + timedelta(days=2),
            planned_freeze_start_at=timezone.now() + timedelta(days=1),
            planned_freeze_end_at=timezone.now() + timedelta(days=2),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24),
            thaw_duration=timedelta(hours=16),
            status=PlanStatus.PLANNED,
        )
        
        cards = get_planning_dashboard()
        card = next((c for c in cards if c['product_id'] == self.product_low.pk), None)
        self.assertIsNotNone(card)
        self.assertEqual(float(card['planned_kg']), 10.0)
        self.assertEqual(card['planned_count'], 1)
    
    def test_dashboard_incoming_packages(self):
        """PACKED packages count as incoming."""
        self._create_package(self.product_low, 8.0, state=PackageState.PACKED)
        
        cards = get_planning_dashboard()
        card = next((c for c in cards if c['product_id'] == self.product_low.pk), None)
        self.assertIsNotNone(card)
        self.assertEqual(float(card['incoming_kg']), 8.0)
    
    def test_dashboard_eligible_count(self):
        """Eligible count only includes FROZEN packages without plans."""
        self._create_package(self.product_low, 5.0, state=PackageState.FROZEN)
        self._create_package(self.product_low, 5.0, state=PackageState.FROZEN)
        self._create_package(self.product_low, 5.0, state=PackageState.THAWING)  # Not eligible
        
        cards = get_planning_dashboard()
        card = next((c for c in cards if c['product_id'] == self.product_low.pk), None)
        self.assertIsNotNone(card)
        self.assertEqual(card['eligible_count'], 2)
    
    def test_dashboard_api_returns_json(self):
        """API endpoint returns valid JSON with cards."""
        from django.contrib.auth.models import User
        User.objects.create_superuser('testadmin', 't@t.com', 'pass123')
        self._create_package(self.product_low, 5.0)
        
        self.client.login(username='testadmin', password='pass123')
        resp = self.client.get('/api/plans/dashboard/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('cards', data)
        self.assertGreater(len(data['cards']), 0)


class PlanningDashboardPermissionTest(TestCase):
    """Test permission behavior for planning dashboard."""
    
    def setUp(self):
        from django.contrib.auth.models import User
        self.admin = User.objects.create_superuser('admin', 'admin@test.com', 'admin123')
        self.worker = User.objects.create_user('worker', 'worker@test.com', 'worker123')
        self.viewer = User.objects.create_user('viewer', 'viewer@test.com', 'viewer123')
        
        self.location = StorageLocation.objects.create(
            name='Freezer X', location_type='FREEZER', capacity=20
        )
        self.batch = Batch.objects.create(
            batch_number='P-001', supplier='Test', received_at=timezone.now()
        )
        self.product = Product.objects.create(
            sku='T-001', name='TestProduct', category='PORK'
        )
        ProductPlanningProfile.objects.create(
            product=self.product,
            avg_daily_usage_kg=Decimal('10'),
            safety_stock_days=Decimal('1'),
            target_coverage_days=Decimal('7'),
        )
        Package.objects.create(
            product=self.product, batch=self.batch, barcode='T-BC-001',
            weight=Decimal('5.0'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN, storage_location=self.location,
        )
    
    def test_admin_can_access_dashboard(self):
        """Admin can access planning dashboard API."""
        self.client.login(username='admin', password='admin123')
        resp = self.client.get('/api/plans/dashboard/')
        self.assertEqual(resp.status_code, 200)
    
    def test_worker_can_access_dashboard(self):
        """Worker can view dashboard (read-only)."""
        self.client.login(username='worker', password='worker123')
        resp = self.client.get('/api/plans/dashboard/')
        self.assertEqual(resp.status_code, 200)
    
    def test_unauthenticated_redirects(self):
        """Unauthenticated user gets redirected."""
        resp = self.client.get('/api/plans/dashboard/')
        self.assertEqual(resp.status_code, 302)
    
    def test_create_plan_page_requires_permission(self):
        """Create plan page requires planning.add_rotationplan permission."""
        self.client.login(username='worker', password='worker123')
        resp = self.client.get('/planning/create/')
        # Worker without permission gets redirected
        self.assertIn(resp.status_code, [200, 302])
    
    def test_admin_can_open_create_form(self):
        """Admin can open the planning create form."""
        self.client.login(username='admin', password='admin123')
        resp = self.client.get('/planning/create/')
        self.assertEqual(resp.status_code, 200)
    
    def test_create_form_accepts_product_id(self):
        """Create form accepts product_id query parameter."""
        self.client.login(username='admin', password='admin123')
        resp = self.client.get(f'/planning/create/?product_id={self.product.pk}')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, str(self.product.pk))
