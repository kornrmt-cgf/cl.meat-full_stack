"""
Tests for Stock Coverage and Demand Planning.

Tests:
- ProductPlanningProfile model
- Stock coverage calculation
- Projected stock-out date
- Safety stock
- Required quantity calculation
- Barcode package eligibility
- Plan conflict detection
- Preparation schedule calculation
- API endpoints
"""
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.utils import timezone
from django.contrib.auth.models import User, Group

from inventory.models import (
    Product, Batch, Package, PackageState, StorageLocation,
    ProductPlanningProfile
)
from planning.models import (
    RotationPlan, FreezeProfile, ThawProfile, PlanStatus
)
from planning.stock_service import (
    get_product_stock_summary,
    calculate_required_quantity,
    calculate_preparation_schedule,
    check_plan_conflicts,
    get_barcode_package_eligibility,
)



class ProductPlanningProfileModelTest(TestCase):
    """Test ProductPlanningProfile model."""

    def setUp(self):
        self.product = Product.objects.create(
            sku='TEST-001', name='สันคอหมู', category='PORK', unit='KG'
        )

    def test_create_profile(self):
        profile = ProductPlanningProfile.objects.create(
            product=self.product,
            avg_daily_usage_kg=Decimal('12.00'),
            safety_stock_days=Decimal('1.0'),
            target_coverage_days=Decimal('7.0'),
            min_order_qty_kg=Decimal('10.00'),
        )
        self.assertEqual(profile.product, self.product)
        self.assertEqual(profile.avg_daily_usage_kg, Decimal('12.00'))
        self.assertTrue(profile.active)

    def test_profile_str(self):
        profile = ProductPlanningProfile.objects.create(
            product=self.product,
            avg_daily_usage_kg=Decimal('12.00'),
        )
        self.assertIn('12.00', str(profile))

    def test_one_to_one_constraint(self):
        ProductPlanningProfile.objects.create(
            product=self.product, avg_daily_usage_kg=Decimal('5.00')
        )
        with self.assertRaises(Exception):
            ProductPlanningProfile.objects.create(
                product=self.product, avg_daily_usage_kg=Decimal('3.00')
            )

    def test_daily_usage_display(self):
        profile = ProductPlanningProfile.objects.create(
            product=self.product, avg_daily_usage_kg=Decimal('12.00')
        )
        self.assertEqual(profile.daily_usage_display, '12.00 กก./วัน')


class StockSummaryTest(TestCase):
    """Test get_product_stock_summary."""

    def setUp(self):
        self.product = Product.objects.create(
            sku='STK-001', name='หมูสามชั้น', category='PORK', unit='KG'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001', supplier='Test', received_at=timezone.now()
        )
        self.profile = ProductPlanningProfile.objects.create(
            product=self.product,
            avg_daily_usage_kg=Decimal('14.00'),
            safety_stock_days=Decimal('1.0'),
            target_coverage_days=Decimal('7.0'),
        )
        self.frozen1 = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('1.200'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN,
            barcode='STK-001-001'
        )
        self.frozen2 = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.800'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN,
            barcode='STK-001-002'
        )

    def test_summary_frozen_stock(self):
        summary = get_product_stock_summary(self.product)
        self.assertEqual(summary['total_stock_kg'], Decimal('2.000'))
        self.assertEqual(summary['frozen_kg'], Decimal('2.000'))
        self.assertEqual(summary['package_count'], 2)

    def test_summary_coverage_days(self):
        summary = get_product_stock_summary(self.product)
        # 2.0 kg / 14 kg/day ≈ 0.14 days
        self.assertGreater(summary['coverage_days'], Decimal('0'))
        self.assertLess(summary['coverage_days'], Decimal('1'))

    def test_summary_projected_stockout(self):
        summary = get_product_stock_summary(self.product)
        self.assertIsNotNone(summary['projected_stockout_date'])
        # Should be today in Bangkok timezone (coverage < 1 day)
        bkk_today = timezone.localtime(timezone.now()).date()
        self.assertEqual(summary['projected_stockout_date'], bkk_today)

    def test_summary_recommended_ready_date(self):
        summary = get_product_stock_summary(self.product)
        self.assertIsNotNone(summary['recommended_ready_date'])
        # Should be today or tomorrow in Bangkok timezone (stock running out)
        bkk_today = timezone.localtime(timezone.now()).date()
        self.assertLessEqual(
            summary['recommended_ready_date'],
            bkk_today + timedelta(days=1)
        )

    def test_summary_eligible_packages(self):
        summary = get_product_stock_summary(self.product)
        self.assertEqual(summary['eligible_count'], 2)
        self.assertEqual(summary['eligible_weight_kg'], Decimal('2.000'))

    def test_summary_excludes_packages_with_plans(self):
        """Packages with existing plans should not be eligible."""
        freeze_p = FreezeProfile.objects.create(name='F1', target_temperature=Decimal('-18.00'), default_duration=timedelta(hours=12), minimum_duration=timedelta(hours=8))
        thaw_p = ThawProfile.objects.create(name='T1', default_duration=timedelta(hours=24), minimum_duration=timedelta(hours=18))
        RotationPlan.objects.create(
            package=self.frozen1,
            target_ready_at=timezone.now() + timedelta(days=1),
            freeze_profile=freeze_p,
            thaw_profile=thaw_p,
            planned_freeze_start_at=timezone.now(),
            planned_freeze_end_at=timezone.now() + timedelta(hours=12),
            planned_thaw_queue_at=timezone.now() + timedelta(hours=13),
            planned_thaw_start_at=timezone.now() + timedelta(hours=13, minutes=30),
            freeze_duration=timedelta(hours=12),
            thaw_duration=timedelta(hours=24),
        )
        summary = get_product_stock_summary(self.product)
        self.assertEqual(summary['eligible_count'], 1)
        self.assertEqual(summary['eligible_weight_kg'], Decimal('0.800'))

    def test_summary_packed_packages_are_eligible(self):
        """PACKED packages should be eligible for planning (not just FROZEN)."""
        packed = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.500'), packed_at=timezone.now(),
            current_state=PackageState.PACKED,
            barcode='STK-001-PACK'
        )
        summary = get_product_stock_summary(self.product)
        # Should count both FROZEN (2) + PACKED (1) = 3 eligible
        self.assertEqual(summary['eligible_count'], 3)
        self.assertEqual(summary['eligible_weight_kg'], Decimal('2.500'))

    def test_summary_no_profile(self):
        """Product without planning profile uses defaults."""
        p2 = Product.objects.create(sku='STK-002', name='TestNo', category='OTHER')
        summary = get_product_stock_summary(p2)
        self.assertEqual(summary['avg_daily_usage_kg'], Decimal('0'))
        self.assertEqual(summary['safety_stock_days'], Decimal('1'))
        self.assertEqual(summary['coverage_days'], Decimal('0'))

    def test_summary_includes_thaw_states(self):
        """THAW_QUEUED and THAWING packages count toward stock."""
        self.frozen1.current_state = PackageState.THAW_QUEUED
        self.frozen1.save()
        summary = get_product_stock_summary(self.product)
        self.assertEqual(summary['frozen_kg'], Decimal('2.000'))

    def test_summary_usable_stock(self):
        """READY_FOR_SALE packages count as usable."""
        self.frozen1.current_state = PackageState.READY_FOR_SALE
        self.frozen1.save()
        summary = get_product_stock_summary(self.product)
        self.assertEqual(summary['usable_kg'], Decimal('1.200'))
        self.assertEqual(summary['frozen_kg'], Decimal('0.800'))
        self.assertEqual(summary['total_stock_kg'], Decimal('2.000'))


class RequiredQuantityTest(TestCase):
    """Test calculate_required_quantity."""

    def setUp(self):
        self.product = Product.objects.create(
            sku='REQ-001', name='อกไก่', category='CHICKEN', unit='KG'
        )
        self.batch = Batch.objects.create(
            batch_number='REQ-B01', supplier='Supplier', received_at=timezone.now()
        )
        self.profile = ProductPlanningProfile.objects.create(
            product=self.product,
            avg_daily_usage_kg=Decimal('10.00'),
            safety_stock_days=Decimal('1.0'),
            target_coverage_days=Decimal('5.0'),
        )

    def test_required_quantity_basic(self):
        result = calculate_required_quantity(self.product)
        # 10 kg/day * 5 days = 50 kg total
        self.assertEqual(result['required_kg'], Decimal('50.0'))

    def test_required_quantity_with_stock(self):
        Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('20.000'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN, barcode='REQ-001'
        )
        result = calculate_required_quantity(self.product)
        # 50 - 20 = 30 net required
        self.assertEqual(result['net_required_kg'], Decimal('30.0'))

    def test_net_required_never_negative(self):
        """Net required should be 0, not negative, when stock exceeds demand."""
        Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('60.000'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN, barcode='REQ-002'
        )
        result = calculate_required_quantity(self.product)
        self.assertEqual(result['net_required_kg'], Decimal('0'))


class BarcodeEligibilityTest(TestCase):
    """Test get_barcode_package_eligibility."""

    def setUp(self):
        self.product = Product.objects.create(
            sku='BC-001', name='สันนอกหมู', category='PORK', unit='KG'
        )
        self.batch = Batch.objects.create(
            batch_number='BC-B01', supplier='Test', received_at=timezone.now()
        )
        self.package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('1.500'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN,
            barcode='BC-TEST-001'
        )

    def test_eligible_barcode(self):
        result = get_barcode_package_eligibility('BC-TEST-001')
        self.assertTrue(result['found'])
        self.assertTrue(result['eligible'])
        self.assertEqual(result['package'], self.package)

    def test_unknown_barcode(self):
        result = get_barcode_package_eligibility('UNKNOWN-001')
        self.assertFalse(result['found'])
        self.assertFalse(result['eligible'])
        self.assertIn('ไม่พบ', result['reason'])

    def test_wrong_state_barcode(self):
        self.package.current_state = PackageState.THAWING
        self.package.save()
        result = get_barcode_package_eligibility('BC-TEST-001')
        self.assertTrue(result['found'])
        self.assertFalse(result['eligible'])
        self.assertIn('Thawing', result['reason'])

    def test_wrong_product_barcode(self):
        other = Product.objects.create(sku='BC-002', name='อกไก่', category='CHICKEN')
        result = get_barcode_package_eligibility('BC-TEST-001', product=other)
        self.assertTrue(result['found'])
        self.assertFalse(result['eligible'])
        self.assertIn('อกไก่', result['reason'])

    def test_package_with_existing_plan(self):
        freeze_p = FreezeProfile.objects.create(name='F1', target_temperature=Decimal('-18.00'), default_duration=timedelta(hours=12), minimum_duration=timedelta(hours=8))
        thaw_p = ThawProfile.objects.create(name='T1', default_duration=timedelta(hours=24), minimum_duration=timedelta(hours=18))
        RotationPlan.objects.create(
            package=self.package,
            target_ready_at=timezone.now() + timedelta(days=1),
            freeze_profile=freeze_p,
            thaw_profile=thaw_p,
            planned_freeze_start_at=timezone.now(),
            planned_freeze_end_at=timezone.now() + timedelta(hours=12),
            planned_thaw_queue_at=timezone.now() + timedelta(hours=13),
            planned_thaw_start_at=timezone.now() + timedelta(hours=13, minutes=30),
            freeze_duration=timedelta(hours=12),
            thaw_duration=timedelta(hours=24),
        )
        result = get_barcode_package_eligibility('BC-TEST-001')
        self.assertTrue(result['found'])
        self.assertFalse(result['eligible'])
        self.assertIn('แผนงาน', result['reason'])


class PlanConflictTest(TestCase):
    """Test check_plan_conflicts."""

    def setUp(self):
        self.product = Product.objects.create(
            sku='CF-001', name='น่องไก่', category='CHICKEN', unit='KG'
        )
        self.batch = Batch.objects.create(
            batch_number='CF-B01', supplier='Test', received_at=timezone.now()
        )
        self.freeze_p = FreezeProfile.objects.create(name='F1', target_temperature=Decimal('-18.00'), default_duration=timedelta(hours=12), minimum_duration=timedelta(hours=8))
        self.thaw_p = ThawProfile.objects.create(name='T1', default_duration=timedelta(hours=24), minimum_duration=timedelta(hours=18))

    def test_no_conflict_when_empty(self):
        target = timezone.now() + timedelta(days=7)
        warnings = check_plan_conflicts(self.product, target)
        # No conflict if no packages → check for package warning instead
        self.assertTrue(any('ไม่มีแพ็กเกจ' in w for w in warnings))

    def test_conflict_when_same_date_plan_exists(self):
        pkg = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.800'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN, barcode='CF-001'
        )
        target = timezone.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=7)
        RotationPlan.objects.create(
            package=pkg,
            target_ready_at=target,
            freeze_profile=self.freeze_p,
            thaw_profile=self.thaw_p,
            status=PlanStatus.PLANNED,
            planned_freeze_start_at=timezone.now(),
            planned_freeze_end_at=timezone.now() + timedelta(hours=12),
            planned_thaw_queue_at=timezone.now() + timedelta(hours=13),
            planned_thaw_start_at=timezone.now() + timedelta(hours=13, minutes=30),
            freeze_duration=timedelta(hours=12),
            thaw_duration=timedelta(hours=24),
        )
        warnings = check_plan_conflicts(self.product, target)
        self.assertTrue(any('มีแผนงานแล้ว' in w for w in warnings))


class PreparationScheduleTest(TestCase):
    """Test calculate_preparation_schedule (backward calculation)."""

    def setUp(self):
        self.freeze_p = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'), default_duration=timedelta(hours=12), minimum_duration=timedelta(hours=8)
        )
        self.thaw_p = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24), minimum_duration=timedelta(hours=18)
        )

    def test_backward_calculation(self):
        target = timezone.now() + timedelta(days=3)
        result = calculate_preparation_schedule(target, self.freeze_p, self.thaw_p, 0.560)

        # 0.560kg is interpolated between threshold (0.5kg) and default
        # thaw_duration is between minimum and default
        thaw_dur = result['thaw_duration']
        self.assertGreaterEqual(thaw_dur, self.thaw_p.minimum_duration)
        self.assertLessEqual(thaw_dur, self.thaw_p.default_duration + self.thaw_p.buffer_duration)
        self.assertEqual(
            result['planned_thaw_start_at'],
            target - thaw_dur
        )
        # thaw_queue = thaw_start - 30min
        self.assertEqual(
            result['planned_thaw_queue_at'],
            result['planned_thaw_start_at'] - timedelta(minutes=30)
        )
        # freeze_end = thaw_start - 15min
        self.assertEqual(
            result['planned_freeze_end_at'],
            result['planned_thaw_start_at'] - timedelta(minutes=15)
        )
        # freeze_start = freeze_end - freeze_duration (8h for 0.56kg medium)
        freeze_dur = self.freeze_p.default_duration + self.freeze_p.buffer_duration
        self.assertEqual(
            result['planned_freeze_start_at'],
            result['planned_freeze_end_at'] - freeze_dur
        )

    def test_total_preparation_time(self):
        target = timezone.now() + timedelta(days=3)
        result = calculate_preparation_schedule(target, self.freeze_p, self.thaw_p)
        # Total includes freeze + thaw + buffer, should be substantial
        total = result['total_preparation_time']
        self.assertGreater(total, timedelta(hours=24))
        self.assertLess(total, timedelta(hours=48))


class StockAnalysisAPITest(TestCase):
    """Test stock analysis API endpoint."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('analyst', password='test123')
        self.group = Group.objects.create(name='MANAGER')
        self.user.groups.add(self.group)
        from django.contrib.auth.models import Permission
        self.user.user_permissions.add(
            Permission.objects.get(codename='view_rotationplan'),
            Permission.objects.get(codename='add_rotationplan'),
        )
        self.client.login(username='analyst', password='test123')

        self.product = Product.objects.create(
            sku='API-001', name='หมูบด', category='PORK', unit='KG'
        )
        self.batch = Batch.objects.create(
            batch_number='API-B01', supplier='Test', received_at=timezone.now()
        )
        ProductPlanningProfile.objects.create(
            product=self.product,
            avg_daily_usage_kg=Decimal('8.00'),
            safety_stock_days=Decimal('1.0'),
            target_coverage_days=Decimal('5.0'),
        )

    def test_stock_analysis_requires_product_id(self):
        resp = self.client.get('/api/plans/stock-analysis/')
        self.assertEqual(resp.status_code, 400)

    def test_stock_analysis_returns_data(self):
        resp = self.client.get(f'/api/plans/stock-analysis/?product_id={self.product.pk}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['product']['name'], 'หมูบด')
        self.assertIn('total_stock_kg', data)
        self.assertIn('coverage_days', data)
        self.assertIn('eligible_packages', data)
        self.assertIn('net_required_kg', data)

    def test_barcode_check_requires_barcode(self):
        resp = self.client.get('/api/plans/barcode-check/')
        self.assertEqual(resp.status_code, 400)

    def test_barcode_check_eligible(self):
        pkg = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.600'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN, barcode='API-BC-001'
        )
        resp = self.client.get('/api/plans/barcode-check/?barcode=API-BC-001')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['found'])
        self.assertTrue(data['eligible'])

    def test_eligible_packages_filters_correctly(self):
        """Only FROZEN packages without plans are returned."""
        pkg1 = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.500'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN, barcode='EP-001'
        )
        pkg2 = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.800'), packed_at=timezone.now(),
            current_state=PackageState.THAWING, barcode='EP-002'
        )
        resp = self.client.get(f'/api/plans/eligible-packages/?product_id={self.product.pk}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        ids = [p['id'] for p in data['packages']]
        self.assertIn(pkg1.pk, ids)
        self.assertNotIn(pkg2.pk, ids)


class EndToEndDemandPlanningTest(TestCase):
    """Test complete workflow: Product → Stock Analysis → Plan → Schedule."""

    def setUp(self):
        self.user = User.objects.create_user('e2eadmin', password='test123')
        from django.contrib.auth.models import Permission
        self.user.user_permissions.add(
            Permission.objects.get(codename='view_rotationplan'),
            Permission.objects.get(codename='add_rotationplan'),
            Permission.objects.get(codename='change_rotationplan'),
        )
        self.client = Client()
        self.client.login(username='e2eadmin', password='test123')

        self.product = Product.objects.create(
            sku='E2E-001', name='ปีกไก่', category='CHICKEN', unit='KG'
        )
        self.batch = Batch.objects.create(
            batch_number='E2E-B01', supplier='Farm', received_at=timezone.now()
        )
        self.profile = ProductPlanningProfile.objects.create(
            product=self.product,
            avg_daily_usage_kg=Decimal('5.00'),
            safety_stock_days=Decimal('1.5'),
            target_coverage_days=Decimal('5.0'),
        )
        self.freeze_p = FreezeProfile.objects.create(
            name='Quick Freezer', target_temperature=Decimal('-18.00'), default_duration=timedelta(hours=8), minimum_duration=timedelta(hours=6)
        )
        self.thaw_p = ThawProfile.objects.create(
            name='Overnight Thaw', default_duration=timedelta(hours=18), minimum_duration=timedelta(hours=12)
        )
        # 5 FROZEN packages
        self.packages = []
        for i in range(5):
            pkg = Package.objects.create(
                product=self.product, batch=self.batch,
                weight=Decimal(f'{0.4 + i * 0.2:.1f}00'),
                packed_at=timezone.now() - timedelta(days=i),
                current_state=PackageState.FROZEN,
                barcode=f'E2E-PKG-{i:03d}'
            )
            self.packages.append(pkg)

    def test_full_demand_planning_workflow(self):
        """Admin selects product → sees stock → selects date → creates plan."""
        # Step 1: Stock analysis
        resp = self.client.get(f'/api/plans/stock-analysis/?product_id={self.product.pk}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['product']['name'], 'ปีกไก่')
        self.assertEqual(data['avg_daily_usage_kg'], '5.0')
        self.assertEqual(data['eligible_count'], 5)

        # Step 2: Barcode check
        resp = self.client.get('/api/plans/barcode-check/?barcode=E2E-PKG-000&product_id=' + str(self.product.pk))
        self.assertEqual(resp.status_code, 200)
        bc = resp.json()
        self.assertTrue(bc['found'])
        self.assertTrue(bc['eligible'])

        # Step 3: Create plan via API
        target = timezone.now() + timedelta(days=5)
        target = target.replace(hour=8, minute=0, second=0, microsecond=0)
        resp = self.client.post('/api/plans/create/', {
            'package_id': self.packages[0].pk,
            'freeze_profile_id': self.freeze_p.pk,
            'thaw_profile_id': self.thaw_p.pk,
            'target_ready_at': target.isoformat(),
        }, content_type='application/json')
        self.assertEqual(resp.status_code, 201)

        # Step 4: Verify plan was created
        plan = RotationPlan.objects.get(package=self.packages[0])
        self.assertIsNotNone(plan)
        self.assertIsNotNone(plan.planned_thaw_start_at)
        self.assertIsNotNone(plan.planned_freeze_start_at)
        self.assertLess(plan.planned_freeze_start_at, plan.planned_freeze_end_at)

        # Step 5: Verify package no longer in eligible list
        resp = self.client.get(f'/api/plans/eligible-packages/?product_id={self.product.pk}')
        data = resp.json()
        ids = [p['id'] for p in data['packages']]
        self.assertNotIn(self.packages[0].pk, ids)
        self.assertEqual(len(ids), 4)
