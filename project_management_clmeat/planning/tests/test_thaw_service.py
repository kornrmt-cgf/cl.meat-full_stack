"""
Tests for Thaw Calculation Service — duration, temperature, capacity, scheduling.
"""
from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from datetime import timedelta
import unittest

from inventory.models import (
    Product, Batch, Package, PackageState, StorageLocation
)
from planning.models import (
    ThawProfile, FreezeProfile, RotationPlan, PlanStatus
)
from planning.thaw_service import (
    calculate_thaw_duration, get_effective_thaw_duration,
    validate_temperature, record_temperature_reading,
    check_thaw_capacity, calculate_thaw_schedule,
    get_best_thaw_profile, get_available_profiles,
)
from planning.services import calculate_rotation_plan


class ThawDurationCalculationTest(TestCase):
    """Test configurable thaw duration calculation."""
    
    def setUp(self):
        self.location = StorageLocation.objects.create(
            name='Freezer A', location_type='FREEZER', capacity=50
        )
        self.batch = Batch.objects.create(
            batch_number='TH-001', supplier='Test', received_at=timezone.now()
        )
        self.product = Product.objects.create(
            sku='TH-P01', name='Test Product', category='PORK'
        )
        
        # Standard thaw profile: min=12h, default=16h, buffer=1h
        # threshold=0.5kg, scale=1.20
        self.profile = ThawProfile.objects.create(
            name='Standard Thaw',
            default_duration=timedelta(hours=16),
            minimum_duration=timedelta(hours=12),
            buffer_duration=timedelta(hours=1),
            weight_threshold_kg=Decimal('0.500'),
            weight_scale_factor=Decimal('1.20'),
            target_temperature=Decimal('3.00'),
            min_temperature=Decimal('1.00'),
            max_temperature=Decimal('5.00'),
            thaw_capacity=20,
        )
    
    def _make_package(self, weight_kg):
        return Package.objects.create(
            product=self.product, batch=self.batch,
            barcode=f'TH-{weight_kg}', weight=Decimal(str(weight_kg)),
            packed_at=timezone.now(), current_state=PackageState.FROZEN,
            storage_location=self.location,
        )
    
    def test_small_package_uses_minimum(self):
        """Package ≤ threshold uses minimum_duration + buffer."""
        pkg = self._make_package(0.400)
        duration = calculate_thaw_duration(pkg, self.profile)
        # minimum(12h) + buffer(1h) = 13h
        self.assertEqual(duration, timedelta(hours=13))
    
    def test_threshold_package_uses_minimum(self):
        """Package exactly at threshold uses minimum_duration + buffer."""
        pkg = self._make_package(0.500)
        duration = calculate_thaw_duration(pkg, self.profile)
        self.assertEqual(duration, timedelta(hours=13))
    
    def test_medium_package_interpolates(self):
        """Package between threshold and 2× threshold interpolates."""
        # At 0.75kg (midway between 0.5 and 1.0):
        # fraction = (0.75 - 0.5) / 0.5 = 0.5
        # interp = 12h + 0.5 * (16h - 12h) = 14h
        # + buffer 1h = 15h
        pkg = self._make_package(0.750)
        duration = calculate_thaw_duration(pkg, self.profile)
        self.assertEqual(duration, timedelta(hours=15))
    
    def test_large_package_uses_scaled_default(self):
        """Package > 2× threshold uses default × scale_factor + buffer."""
        # At 1.5kg (> 2 × 0.5 = 1.0):
        # 16h × 1.20 = 19.2h = 19h 12m
        # + buffer 1h = 20h 12m
        pkg = self._make_package(1.500)
        duration = calculate_thaw_duration(pkg, self.profile)
        expected = timedelta(hours=20, minutes=12)
        self.assertEqual(duration, expected)
    
    def test_very_large_package(self):
        """Very large package (3kg) still uses scaled default."""
        pkg = self._make_package(3.000)
        duration = calculate_thaw_duration(pkg, self.profile)
        # 16h × 1.20 = 19.2h + 1h buffer = 20.2h
        expected = timedelta(hours=20, minutes=12)
        self.assertEqual(duration, expected)
    
    def test_buffer_always_added(self):
        """Buffer is always added regardless of weight."""
        for weight in [0.100, 0.500, 1.000, 2.000]:
            pkg = self._make_package(weight)
            duration = calculate_thaw_duration(pkg, self.profile)
            # Must be at least buffer_duration
            self.assertGreaterEqual(duration, self.profile.buffer_duration)
    
    def test_different_profile_different_duration(self):
        """Different profiles produce different durations."""
        fast_profile = ThawProfile.objects.create(
            name='Fast Thaw',
            default_duration=timedelta(hours=8),
            minimum_duration=timedelta(hours=6),
            buffer_duration=timedelta(minutes=30),
            weight_threshold_kg=Decimal('0.500'),
            weight_scale_factor=Decimal('1.10'),
        )
        
        pkg = self._make_package(0.600)
        std_dur = calculate_thaw_duration(pkg, self.profile)
        fast_dur = calculate_thaw_duration(pkg, fast_profile)
        
        self.assertGreater(std_dur, fast_dur)
    
    def test_no_buffer_profile(self):
        """Profile with zero buffer still works."""
        no_buffer = ThawProfile.objects.create(
            name='No Buffer',
            default_duration=timedelta(hours=10),
            minimum_duration=timedelta(hours=8),
            buffer_duration=timedelta(hours=0),
            weight_threshold_kg=Decimal('0.500'),
            weight_scale_factor=Decimal('1.20'),
        )
        
        pkg = self._make_package(0.300)
        duration = calculate_thaw_duration(pkg, no_buffer)
        self.assertEqual(duration, timedelta(hours=8))


class TemperatureValidationTest(TestCase):
    """Test temperature range validation."""
    
    def setUp(self):
        self.location = StorageLocation.objects.create(
            name='Thaw Area', location_type='THAW_AREA', capacity=20,
            min_temperature=Decimal('1.00'),
            max_temperature=Decimal('5.00'),
        )
        self.profile = ThawProfile.objects.create(
            name='Standard',
            default_duration=timedelta(hours=16),
            minimum_duration=timedelta(hours=12),
            target_temperature=Decimal('3.00'),
            min_temperature=Decimal('1.00'),
            max_temperature=Decimal('5.00'),
        )
    
    def test_temperature_in_range(self):
        """Temperature within range is OK."""
        result = validate_temperature(Decimal('3.00'), self.profile)
        self.assertTrue(result['valid'])
        self.assertEqual(result['status'], 'OK')
    
    def test_temperature_at_min_boundary(self):
        """Temperature exactly at min is OK."""
        result = validate_temperature(Decimal('1.00'), self.profile)
        self.assertTrue(result['valid'])
        self.assertEqual(result['status'], 'OK')
    
    def test_temperature_at_max_boundary(self):
        """Temperature exactly at max is OK."""
        result = validate_temperature(Decimal('5.00'), self.profile)
        self.assertTrue(result['valid'])
        self.assertEqual(result['status'], 'OK')
    
    def test_temperature_below_min(self):
        """Temperature below min is CRITICAL."""
        result = validate_temperature(Decimal('0.50'), self.profile)
        self.assertFalse(result['valid'])
        self.assertEqual(result['status'], 'CRITICAL')
        self.assertIn('ต่ำเกินไป', result['message'])
    
    def test_temperature_above_max(self):
        """Temperature above max is CRITICAL."""
        result = validate_temperature(Decimal('7.00'), self.profile)
        self.assertFalse(result['valid'])
        self.assertEqual(result['status'], 'CRITICAL')
        self.assertIn('สูงเกินไป', result['message'])
    
    def test_temperature_warning_low(self):
        """Temperature near min is WARNING."""
        result = validate_temperature(Decimal('1.50'), self.profile)
        self.assertTrue(result['valid'])
        self.assertEqual(result['status'], 'WARNING')
        self.assertIn('ใกล้ขีดจำกัดล่าง', result['message'])
    
    def test_temperature_warning_high(self):
        """Temperature near max is WARNING."""
        result = validate_temperature(Decimal('4.50'), self.profile)
        self.assertTrue(result['valid'])
        self.assertEqual(result['status'], 'WARNING')
        self.assertIn('ใกล้ขีดจำกัดบน', result['message'])
    
    def test_fallback_to_location_range(self):
        """Falls back to location range when no profile."""
        result = validate_temperature(Decimal('3.00'), location=self.location)
        self.assertTrue(result['valid'])
        self.assertEqual(result['min_allowed'], Decimal('1.00'))
        self.assertEqual(result['max_allowed'], Decimal('5.00'))
    
    def test_no_range_configured(self):
        """No range = can't validate, returns OK with warning."""
        loc = StorageLocation.objects.create(
            name='Unknown', location_type='STORAGE', capacity=10
        )
        result = validate_temperature(Decimal('25.00'), location=loc)
        self.assertTrue(result['valid'])
        self.assertIn('ไม่มีการกำหนด', result['message'])
    
    def test_3c_passes(self):
        """3°C should PASS with 1-5°C range."""
        result = validate_temperature(Decimal('3.00'), self.profile)
        self.assertTrue(result['valid'])
        self.assertEqual(result['status'], 'OK')
    
    def test_7c_fails(self):
        """7°C should FAIL with 1-5°C range."""
        result = validate_temperature(Decimal('7.00'), self.profile)
        self.assertFalse(result['valid'])
        self.assertEqual(result['status'], 'CRITICAL')
    
    def test_record_temperature_ok(self):
        """Recording a valid temperature creates correct log."""
        result = record_temperature_reading(
            self.location, Decimal('3.00'),
            thaw_profile=self.profile,
            source='MANUAL', recorded_by='test_worker',
        )
        
        log = result['log']
        self.assertEqual(log.actual_temperature, Decimal('3.00'))
        self.assertEqual(log.status, 'OK')
        self.assertEqual(log.recorded_by, 'test_worker')
        self.assertTrue(result['validation']['valid'])
    
    def test_record_temperature_critical(self):
        """Recording out-of-range temperature creates CRITICAL log."""
        result = record_temperature_reading(
            self.location, Decimal('7.00'),
            thaw_profile=self.profile,
        )
        
        log = result['log']
        self.assertEqual(log.status, 'CRITICAL')
        self.assertFalse(result['validation']['valid'])


class CapacityCheckTest(TestCase):
    """Test thaw capacity checking."""
    
    def setUp(self):
        self.location = StorageLocation.objects.create(
            name='Thaw Area', location_type='THAW_AREA', capacity=20, thaw_capacity=5
        )
        self.profile = ThawProfile.objects.create(
            name='Cap Test', default_duration=timedelta(hours=16),
            minimum_duration=timedelta(hours=12), thaw_capacity=3,
        )
        self.product = Product.objects.create(sku='CAP-01', name='CapTest', category='PORK')
        self.batch = Batch.objects.create(batch_number='C-001', supplier='X', received_at=timezone.now())
        self.freeze_profile = FreezeProfile.objects.create(
            name='Std', target_temperature=Decimal('-18'),
            minimum_duration=timedelta(hours=8), default_duration=timedelta(hours=24),
        )
    
    def _make_plan(self, target_hours_from_now=24):
        pkg = Package.objects.create(
            product=self.product, batch=self.batch, barcode=f'CAP-{Package.objects.count()}',
            weight=Decimal('0.500'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN, storage_location=self.location,
        )
        target = timezone.now() + timedelta(hours=target_hours_from_now)
        plan = RotationPlan.objects.create(
            package=pkg, target_ready_at=target,
            planned_thaw_start_at=target - timedelta(hours=16),
            planned_thaw_queue_at=target - timedelta(hours=16, minutes=30),
            planned_freeze_start_at=target - timedelta(hours=42),
            planned_freeze_end_at=target - timedelta(hours=16, minutes=15),
            freeze_profile=self.freeze_profile, thaw_profile=self.profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=16),
        )
        # Add to queue
        from planning.models import ThawQueueEntry, QueueStatus
        ThawQueueEntry.objects.create(
            package=pkg, rotation_plan=plan,
            queue_position=ThawQueueEntry.objects.count() + 1,
            planned_start_at=plan.planned_thaw_start_at,
            target_ready_at=plan.target_ready_at,
            status=QueueStatus.QUEUED,
        )
        return pkg, plan
    
    def test_capacity_available_initially(self):
        """Capacity is available when no active entries."""
        result = check_thaw_capacity(self.profile)
        self.assertTrue(result['available'])
        self.assertEqual(result['current_count'], 0)
    
    def test_capacity_reduced_by_active_entries(self):
        """Active entries reduce available capacity."""
        pkg1, plan1 = self._make_plan(24)
        pkg2, plan2 = self._make_plan(25)
        
        # Check at a time when both entries are active (thaw in progress)
        check_time = timezone.now() + timedelta(hours=16)  # During thaw
        result = check_thaw_capacity(self.profile, target_time=check_time)
        self.assertEqual(result['current_count'], 2)
        self.assertTrue(result['available'])  # 2 < 3 (profile capacity)
    
    def test_capacity_exceeded(self):
        """Capacity exceeded when count >= max."""
        for i in range(3):
            self._make_plan(24 + i)
        
        # Check during thaw time when all 3 are active
        check_time = timezone.now() + timedelta(hours=16)
        result = check_thaw_capacity(self.profile, target_time=check_time)
        self.assertFalse(result['available'])
        self.assertEqual(result['current_count'], 3)
    
    def test_location_capacity_used(self):
        """Location capacity is considered."""
        for i in range(5):
            self._make_plan(24 + i)
        
        # Check during thaw time
        check_time = timezone.now() + timedelta(hours=16)
        # Profile allows 3, but we created 5 entries
        result = check_thaw_capacity(self.profile, self.location, target_time=check_time)
        self.assertFalse(result['available'])
    
    def test_uses_smaller_capacity(self):
        """Uses the smaller of profile and location capacity."""
        # Profile=3, Location=5 → effective=3
        for i in range(3):
            self._make_plan(24 + i)
        
        check_time = timezone.now() + timedelta(hours=16)
        result = check_thaw_capacity(self.profile, self.location, target_time=check_time)
        self.assertEqual(result['max_capacity'], 3)  # profile is smaller
    
    def test_capacity_at_specific_time(self):
        """Check capacity at a specific future time during thaw."""
        self._make_plan(24)
        
        # thaw_start = now+24h - 16h = now+8h
        # thaw_end (target_ready) = now+24h
        # Check at now+16h (during thaw)
        check_time = timezone.now() + timedelta(hours=16)
        result = check_thaw_capacity(self.profile, target_time=check_time)
        self.assertEqual(result['current_count'], 1)


class BackwardSchedulingTest(unittest.TestCase):
    """Test backward scheduling from target ready time."""
    
    def setUp(self):
        from planning.models import ThawProfile as TP
        self.profile = TP(
            name='Sched Test',
            default_duration=timedelta(hours=16),
            minimum_duration=timedelta(hours=12),
            buffer_duration=timedelta(hours=1),
            weight_threshold_kg=Decimal('0.500'),
        )
    
    def test_basic_schedule(self):
        """Basic backward schedule calculation."""
        target = timezone.make_aware(
            timezone.datetime(2026, 9, 2, 8, 0)
        )
        
        result = calculate_thaw_schedule(target, self.profile)
        
        # thaw_duration = 16h (default, no package) + 1h buffer = 17h
        # thaw_start = target - 17h = Sep 1, 15:00
        # thaw_queue = thaw_start - 30min = Sep 1, 14:30
        self.assertEqual(result['thaw_start_at'], 
                        timezone.make_aware(timezone.datetime(2026, 9, 1, 15, 0)))
        self.assertEqual(result['thaw_queue_at'],
                        timezone.make_aware(timezone.datetime(2026, 9, 1, 14, 30)))
        self.assertEqual(result['target_ready_at'], target)
    
    def test_with_package_uses_weight(self):
        """With package, uses weight-based duration."""
        from inventory.models import Product as P, Batch as B, Package as PK, PackageState as PS
        
        product = P.objects.create(sku='S-01', name='S', category='PORK')
        batch = B.objects.create(batch_number='S-001', supplier='X', received_at=timezone.now())
        loc = StorageLocation.objects.create(name='L', location_type='FREEZER', capacity=10)
        pkg = PK.objects.create(
            product=product, batch=batch, barcode='S-BC',
            weight=Decimal('0.400'), packed_at=timezone.now(),
            current_state=PS.FROZEN, storage_location=loc,
        )
        
        target = timezone.make_aware(
            timezone.datetime(2026, 9, 2, 8, 0)
        )
        
        result = calculate_thaw_schedule(target, self.profile, package=pkg)
        
        # 0.4kg ≤ 0.5kg threshold → minimum_duration(12h) + buffer(1h) = 13h
        self.assertEqual(result['thaw_duration'], timedelta(hours=13))
        self.assertEqual(result['thaw_start_at'],
                        timezone.make_aware(timezone.datetime(2026, 9, 1, 19, 0)))
    
    def test_schedule_is_timezone_aware(self):
        """Schedule produces timezone-aware datetimes."""
        target = timezone.make_aware(
            timezone.datetime(2026, 9, 2, 8, 0)
        )
        
        result = calculate_thaw_schedule(target, self.profile)
        
        self.assertTrue(timezone.is_aware(result['thaw_start_at']))
        self.assertTrue(timezone.is_aware(result['thaw_queue_at']))
        self.assertTrue(timezone.is_aware(result['target_ready_at']))
    
    def test_custom_queue_buffer(self):
        """Custom queue buffer changes queue time."""
        target = timezone.make_aware(
            timezone.datetime(2026, 9, 2, 8, 0)
        )
        
        result = calculate_thaw_schedule(
            target, self.profile,
            queue_buffer_minutes=60,  # 1 hour instead of 30 min
        )
        
        # thaw_start = target - 17h = Sep 1, 15:00
        # thaw_queue = thaw_start - 60min = Sep 1, 14:00
        self.assertEqual(result['thaw_queue_at'],
                        timezone.make_aware(timezone.datetime(2026, 9, 1, 14, 0)))


class FullRotationPlanWithEnhancedProfileTest(TestCase):
    """Test creating rotation plan with the enhanced thaw profile."""
    
    def setUp(self):
        self.location = StorageLocation.objects.create(
            name='Freezer A', location_type='FREEZER', capacity=50
        )
        self.thaw_area = StorageLocation.objects.create(
            name='Thaw Area', location_type='THAW_AREA', capacity=20, thaw_capacity=10,
            min_temperature=Decimal('1.00'), max_temperature=Decimal('5.00'),
        )
        self.batch = Batch.objects.create(
            batch_number='FP-001', supplier='Test', received_at=timezone.now()
        )
        self.product = Product.objects.create(
            sku='FP-01', name='Full Plan Test', category='CHICKEN'
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='Std Freeze', target_temperature=Decimal('-18'),
            minimum_duration=timedelta(hours=8), default_duration=timedelta(hours=24),
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Chicken Thaw',
            default_duration=timedelta(hours=14),
            minimum_duration=timedelta(hours=10),
            buffer_duration=timedelta(minutes=45),
            weight_threshold_kg=Decimal('0.600'),
            weight_scale_factor=Decimal('1.15'),
            target_temperature=Decimal('3.00'),
            min_temperature=Decimal('1.00'),
            max_temperature=Decimal('5.00'),
            thaw_capacity=10,
            category='CHICKEN',
        )
    
    def test_full_plan_with_enhanced_profile(self):
        """Create a complete plan using the enhanced profile."""
        pkg = Package.objects.create(
            product=self.product, batch=self.batch, barcode='FP-BC-001',
            weight=Decimal('0.800'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN, storage_location=self.location,
        )
        
        target = timezone.now() + timedelta(days=3)
        
        plan = RotationPlan.objects.create(
            package=pkg, target_ready_at=target,
            planned_thaw_start_at=target - timedelta(hours=14),
            planned_thaw_queue_at=target - timedelta(hours=14, minutes=30),
            planned_freeze_start_at=target - timedelta(hours=40),
            planned_freeze_end_at=target - timedelta(hours=14, minutes=15),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24),
            thaw_duration=timedelta(hours=14),
            status=PlanStatus.PLANNED,
        )
        
        self.assertIsNotNone(plan)
        self.assertEqual(plan.thaw_profile, self.thaw_profile)
        self.assertEqual(plan.thaw_duration, timedelta(hours=14))
    
    def test_profile_category_matching(self):
        """Profile is matched by product category."""
        profile = get_best_thaw_profile(product=self.product)
        self.assertEqual(profile, self.thaw_profile)
    
    def test_all_categories_profile_fallback(self):
        """Falls back to 'all categories' profile."""
        general = ThawProfile.objects.create(
            name='General', default_duration=timedelta(hours=16),
            minimum_duration=timedelta(hours=12), category='',
        )
        
        beef = Product.objects.create(sku='B-01', name='Beef', category='BEEF')
        profile = get_best_thaw_profile(product=beef)
        self.assertEqual(profile, general)
    
    def test_available_profiles_for_product(self):
        """Get profiles applicable to a product."""
        profiles = get_available_profiles(product=self.product)
        # Should include chicken-specific and any '' (all) profiles
        self.assertIn(self.thaw_profile, profiles)



