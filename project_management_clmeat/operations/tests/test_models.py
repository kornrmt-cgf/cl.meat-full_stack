"""
Tests for Operations Models.
"""
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from operations.models import WorkerTask, TaskEvent, RotationEvent, TaskType, TaskStatus
from inventory.models import Product, Batch, Package, PackageState
from planning.models import FreezeProfile, ThawProfile, RotationPlan, PlanStatus


class WorkerTaskTest(TestCase):
    """Test WorkerTask model."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PKC001', name='Pork Collar', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001', supplier='Thai Fresh', received_at=timezone.now()
        )
        self.package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12)
        )
        target_ready = timezone.now() + timedelta(days=3)
        self.plan = RotationPlan.objects.create(
            package=self.package, target_ready_at=target_ready,
            planned_thaw_start_at=target_ready - timedelta(hours=24),
            planned_thaw_queue_at=target_ready - timedelta(hours=24, minutes=30),
            planned_freeze_start_at=target_ready - timedelta(hours=24, minutes=45),
            planned_freeze_end_at=target_ready - timedelta(hours=24, minutes=15),
            freeze_profile=self.freeze_profile, thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
    
    def test_create_worker_task(self):
        """Test creating a worker task."""
        task = WorkerTask.objects.create(
            package=self.package,
            rotation_plan=self.plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.PENDING
        )
        
        self.assertEqual(task.task_type, TaskType.FREEZE_START)
        self.assertEqual(task.status, TaskStatus.PENDING)
    
    def test_task_is_overdue(self):
        """Test is_overdue property."""
        task = WorkerTask.objects.create(
            package=self.package,
            rotation_plan=self.plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now() - timedelta(hours=1),
            status=TaskStatus.PENDING
        )
        
        self.assertTrue(task.is_overdue)
    
    def test_task_str(self):
        """Test task string representation."""
        task = WorkerTask.objects.create(
            package=self.package,
            rotation_plan=self.plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.PENDING
        )
        
        self.assertIn('Freeze Start', str(task))


class RotationEventTest(TestCase):
    """Test RotationEvent model."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PKC001', name='Pork Collar', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001', supplier='Thai Fresh', received_at=timezone.now()
        )
        self.package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
    
    def test_create_rotation_event(self):
        """Test creating a rotation event."""
        event = RotationEvent.objects.create(
            package=self.package,
            event_type='STATE_TRANSITION',
            from_state='PACKED',
            to_state='FREEZING',
            timestamp=timezone.now(),
            actor='test_user'
        )
        
        self.assertEqual(event.event_type, 'STATE_TRANSITION')
        self.assertEqual(event.from_state, 'PACKED')
        self.assertEqual(event.to_state, 'FREEZING')
    
    def test_rotation_event_str(self):
        """Test rotation event string representation."""
        event = RotationEvent.objects.create(
            package=self.package,
            event_type='STATE_TRANSITION',
            from_state='PACKED',
            to_state='FREEZING',
            timestamp=timezone.now()
        )
        
        self.assertIn('PACKED', str(event))
        self.assertIn('FREEZING', str(event))
