"""
Tests for Planning Services.
"""
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, PlanStatus,
    ThawQueueEntry, QueueStatus
)
from planning.services import (
    calculate_freeze_duration, calculate_thaw_duration,
    calculate_rotation_plan, create_rotation_plan,
    add_to_thaw_queue, remove_from_thaw_queue
)
from inventory.models import Product, Batch, Package, PackageState


class CalculateFreezeDurationTest(TestCase):
    """Test calculate_freeze_duration service."""
    
    def setUp(self):
        self.profile = FreezeProfile.objects.create(
            name='Standard',
            target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24),
            buffer_duration=timedelta(hours=2)
        )
        
        self.product = Product.objects.create(
            sku='TEST', name='Test', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='B001', supplier='Test', received_at=timezone.now()
        )
    
    def test_small_package(self):
        """Test duration for small package (<=0.5kg)."""
        package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.500'), packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        
        duration = calculate_freeze_duration(package, self.profile)
        # Small package uses minimum_duration + buffer
        expected = self.profile.minimum_duration + self.profile.buffer_duration
        self.assertEqual(duration, expected)
    
    def test_medium_package(self):
        """Test duration for medium package (0.5-1.0kg)."""
        package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.750'), packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        
        duration = calculate_freeze_duration(package, self.profile)
        # Medium package uses default_duration + buffer
        expected = self.profile.default_duration + self.profile.buffer_duration
        self.assertEqual(duration, expected)
    
    def test_large_package(self):
        """Test duration for large package (>1.0kg)."""
        package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('1.500'), packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        
        duration = calculate_freeze_duration(package, self.profile)
        # Large package uses default_duration * 1.2 + buffer
        expected = (self.profile.default_duration * 1.2) + self.profile.buffer_duration
        self.assertEqual(duration, expected)


class CalculateRotationPlanTest(TestCase):
    """Test calculate_rotation_plan service."""
    
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
        self.package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
    
    def test_calculate_plan(self):
        """Test plan calculation."""
        target_ready = timezone.now() + timedelta(days=3)
        
        plan_data = calculate_rotation_plan(
            self.package, target_ready,
            self.freeze_profile, self.thaw_profile
        )
        
        # Verify times are calculated correctly
        self.assertIn('planned_thaw_start_at', plan_data)
        self.assertIn('planned_freeze_start_at', plan_data)
        self.assertIn('freeze_duration', plan_data)
        self.assertIn('thaw_duration', plan_data)
        
        # Thaw should start before target ready
        self.assertLess(plan_data['planned_thaw_start_at'], target_ready)
        
        # Freeze should start before thaw
        self.assertLess(plan_data['planned_freeze_start_at'], plan_data['planned_thaw_start_at'])


class CreateRotationPlanTest(TestCase):
    """Test create_rotation_plan service."""
    
    def setUp(self):
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard',
            target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard',
            default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12)
        )
        
        self.product = Product.objects.create(
            sku='TEST', name='Test', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='B001', supplier='Test', received_at=timezone.now()
        )
        self.package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
    
    def test_create_plan_success(self):
        """Test successful plan creation."""
        target_ready = timezone.now() + timedelta(days=3)
        
        plan = create_rotation_plan(
            self.package, target_ready,
            self.freeze_profile, self.thaw_profile
        )
        
        self.assertEqual(plan.package, self.package)
        self.assertEqual(plan.status, PlanStatus.PLANNED)
        self.assertEqual(plan.freeze_profile, self.freeze_profile)
        self.assertEqual(plan.thaw_profile, self.thaw_profile)
    
    def test_create_plan_wrong_state(self):
        """Test plan creation with wrong package state (e.g. THAWING)."""
        self.package.current_state = PackageState.THAWING
        self.package.save()
        
        target_ready = timezone.now() + timedelta(days=3)
        
        with self.assertRaises(ValueError) as context:
            create_rotation_plan(
                self.package, target_ready,
                self.freeze_profile, self.thaw_profile
            )
        self.assertIn('PACKED or FROZEN', str(context.exception))
    
    def test_create_plan_packed_state_allowed(self):
        """Test plan creation with PACKED state is now allowed."""
        self.package.current_state = PackageState.PACKED
        self.package.save()
        
        target_ready = timezone.now() + timedelta(days=3)
        
        plan = create_rotation_plan(
            self.package, target_ready,
            self.freeze_profile, self.thaw_profile
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.package, self.package)
        self.assertEqual(plan.status, PlanStatus.PLANNED)
    
    def test_create_plan_duplicate(self):
        """Test creating duplicate plan."""
        target_ready = timezone.now() + timedelta(days=3)
        
        create_rotation_plan(
            self.package, target_ready,
            self.freeze_profile, self.thaw_profile
        )
        
        with self.assertRaises(ValueError) as context:
            create_rotation_plan(
                self.package, target_ready,
                self.freeze_profile, self.thaw_profile
            )
        self.assertIn('already has', str(context.exception))
