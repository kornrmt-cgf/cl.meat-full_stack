"""
Tests for Planning Models.
"""
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, PlanStatus,
    ThawQueueEntry, QueueStatus
)
from inventory.models import Product, Batch, Package, PackageState


class FreezeProfileTest(TestCase):
    """Test FreezeProfile model."""
    
    def test_create_freeze_profile(self):
        """Test creating a freeze profile."""
        profile = FreezeProfile.objects.create(
            name='Standard Freeze',
            target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24),
            buffer_duration=timedelta(hours=2)
        )
        self.assertEqual(profile.name, 'Standard Freeze')
        self.assertEqual(profile.target_temperature, Decimal('-18.00'))
        self.assertTrue(profile.active)
    
    def test_freeze_profile_str(self):
        """Test freeze profile string representation."""
        profile = FreezeProfile.objects.create(
            name='Standard Freeze',
            target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24)
        )
        self.assertIn('Standard Freeze', str(profile))


class ThawProfileTest(TestCase):
    """Test ThawProfile model."""
    
    def test_create_thaw_profile(self):
        """Test creating a thaw profile."""
        profile = ThawProfile.objects.create(
            name='Standard Thaw',
            default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12),
            buffer_duration=timedelta(hours=2)
        )
        self.assertEqual(profile.name, 'Standard Thaw')
        self.assertTrue(profile.active)


class RotationPlanTest(TestCase):
    """Test RotationPlan model."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PKC001',
            name='Pork Collar',
            category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001',
            supplier='Thai Fresh',
            received_at=timezone.now()
        )
        self.package = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard Freeze',
            target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard Thaw',
            default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12)
        )
    
    def test_create_rotation_plan(self):
        """Test creating a rotation plan."""
        target_ready = timezone.now() + timedelta(days=3)
        
        plan = RotationPlan.objects.create(
            package=self.package,
            target_ready_at=target_ready,
            planned_thaw_start_at=target_ready - timedelta(hours=24),
            planned_thaw_queue_at=target_ready - timedelta(hours=24, minutes=30),
            planned_freeze_start_at=target_ready - timedelta(hours=24, minutes=45),
            planned_freeze_end_at=target_ready - timedelta(hours=24, minutes=15),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24),
            thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
        
        self.assertEqual(plan.package, self.package)
        self.assertEqual(plan.status, PlanStatus.PLANNED)
    
    def test_rotation_plan_str(self):
        """Test rotation plan string representation."""
        target_ready = timezone.now() + timedelta(days=3)
        
        plan = RotationPlan.objects.create(
            package=self.package,
            target_ready_at=target_ready,
            planned_thaw_start_at=target_ready - timedelta(hours=24),
            planned_thaw_queue_at=target_ready - timedelta(hours=24, minutes=30),
            planned_freeze_start_at=target_ready - timedelta(hours=24, minutes=45),
            planned_freeze_end_at=target_ready - timedelta(hours=24, minutes=15),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24),
            thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
        
        self.assertIn('Pork Collar', str(plan))


class ThawQueueEntryTest(TestCase):
    """Test ThawQueueEntry model."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PKC001',
            name='Pork Collar',
            category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001',
            supplier='Thai Fresh',
            received_at=timezone.now()
        )
        self.package = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard Freeze',
            target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard Thaw',
            default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12)
        )
        
        target_ready = timezone.now() + timedelta(days=3)
        self.plan = RotationPlan.objects.create(
            package=self.package,
            target_ready_at=target_ready,
            planned_thaw_start_at=target_ready - timedelta(hours=24),
            planned_thaw_queue_at=target_ready - timedelta(hours=24, minutes=30),
            planned_freeze_start_at=target_ready - timedelta(hours=24, minutes=45),
            planned_freeze_end_at=target_ready - timedelta(hours=24, minutes=15),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24),
            thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
    
    def test_create_queue_entry(self):
        """Test creating a queue entry."""
        entry = ThawQueueEntry.objects.create(
            package=self.package,
            rotation_plan=self.plan,
            queue_position=1,
            planned_start_at=self.plan.planned_thaw_start_at,
            target_ready_at=self.plan.target_ready_at,
            status=QueueStatus.QUEUED
        )
        
        self.assertEqual(entry.queue_position, 1)
        self.assertEqual(entry.status, QueueStatus.QUEUED)
    
    def test_queue_entry_str(self):
        """Test queue entry string representation."""
        entry = ThawQueueEntry.objects.create(
            package=self.package,
            rotation_plan=self.plan,
            queue_position=1,
            planned_start_at=self.plan.planned_thaw_start_at,
            target_ready_at=self.plan.target_ready_at,
            status=QueueStatus.QUEUED
        )
        
        self.assertIn('Queue #1', str(entry))
