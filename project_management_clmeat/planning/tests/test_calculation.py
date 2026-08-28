"""
Tests for Planning Calculation Logic.
"""
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from planning.models import FreezeProfile, ThawProfile
from planning.services import calculate_rotation_plan
from inventory.models import Product, Batch, Package, PackageState


class WeightBasedCalculationTest(TestCase):
    """Test weight-based duration calculations."""
    
    def setUp(self):
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard',
            target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24),
            buffer_duration=timedelta(hours=2)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard',
            default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12),
            buffer_duration=timedelta(hours=2)
        )
        
        self.product = Product.objects.create(
            sku='TEST', name='Test', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='B001', supplier='Test', received_at=timezone.now()
        )
    
    def create_package(self, weight):
        """Helper to create package with specific weight."""
        return Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal(str(weight)), packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
    
    def test_very_small_package(self):
        """Test calculation for very small package (0.3kg)."""
        package = self.create_package(0.300)
        target_ready = timezone.now() + timedelta(days=3)
        
        plan = calculate_rotation_plan(
            package, target_ready,
            self.freeze_profile, self.thaw_profile
        )
        
        # Small package should use minimum thaw duration
        self.assertGreaterEqual(plan['thaw_duration'], self.thaw_profile.minimum_duration)
    
    def test_standard_package(self):
        """Test calculation for standard package (0.56kg).
        
        0.560kg is slightly above threshold (0.500kg), so it gets
        interpolated between minimum (12h) and default (24h).
        fraction = (0.56 - 0.5) / 0.5 = 0.12
        interp = 12h + 0.12 * 12h = 13.44h
        total = 13.44h + 2h buffer = 15.44h
        """
        package = self.create_package(0.560)
        target_ready = timezone.now() + timedelta(days=3)
        
        plan = calculate_rotation_plan(
            package, target_ready,
            self.freeze_profile, self.thaw_profile
        )
        
        # Freeze duration uses default (weight-based)
        self.assertEqual(plan['freeze_duration'], self.freeze_profile.default_duration + self.freeze_profile.buffer_duration)
        # Thaw duration is interpolated for 0.560kg (above threshold)
        # 12h + 0.12*(24h-12h) + 2h buffer = ~15.44h
        self.assertGreaterEqual(plan['thaw_duration'], self.thaw_profile.minimum_duration + self.thaw_profile.buffer_duration)
        self.assertLessEqual(plan['thaw_duration'], self.thaw_profile.default_duration + self.thaw_profile.buffer_duration)
    
    def test_large_package(self):
        """Test calculation for large package (1.5kg)."""
        package = self.create_package(1.500)
        target_ready = timezone.now() + timedelta(days=3)
        
        plan = calculate_rotation_plan(
            package, target_ready,
            self.freeze_profile, self.thaw_profile
        )
        
        # Large package should use extended durations
        expected_thaw = (self.thaw_profile.default_duration * 1.2) + self.thaw_profile.buffer_duration
        self.assertEqual(plan['thaw_duration'], expected_thaw)
    
    def test_time_chain_correctness(self):
        """Test that calculated times form correct chain."""
        package = self.create_package(0.560)
        target_ready = timezone.now() + timedelta(days=3)
        
        plan = calculate_rotation_plan(
            package, target_ready,
            self.freeze_profile, self.thaw_profile
        )
        
        # Verify time chain: freeze_start < freeze_end, freeze_start < queue < thaw_start < target_ready
        self.assertLess(plan['planned_freeze_start_at'], plan['planned_freeze_end_at'])
        self.assertLess(plan['planned_freeze_start_at'], plan['planned_thaw_queue_at'])
        self.assertLess(plan['planned_thaw_queue_at'], plan['planned_thaw_start_at'])
        self.assertLess(plan['planned_thaw_start_at'], plan['target_ready_at'])
    
    def test_buffer_time_included(self):
        """Test that buffer time is included in calculations."""
        package = self.create_package(0.560)
        target_ready = timezone.now() + timedelta(days=3)
        
        plan = calculate_rotation_plan(
            package, target_ready,
            self.freeze_profile, self.thaw_profile
        )
        
        # Freeze duration should include buffer
        expected_freeze = self.freeze_profile.default_duration + self.freeze_profile.buffer_duration
        self.assertEqual(plan['freeze_duration'], expected_freeze)
        
        # Thaw duration should be >= minimum + buffer (interpolated for 0.560kg)
        min_with_buffer = self.thaw_profile.minimum_duration + self.thaw_profile.buffer_duration
        self.assertGreaterEqual(plan['thaw_duration'], min_with_buffer)
