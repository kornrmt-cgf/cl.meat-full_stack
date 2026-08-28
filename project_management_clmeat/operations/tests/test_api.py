"""
Tests for Operations API endpoints.
"""
import json
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from operations.models import WorkerTask, TaskType, TaskStatus
from inventory.models import Product, Batch, Package, PackageState
from planning.models import FreezeProfile, ThawProfile, RotationPlan, PlanStatus


class TasksTodayApiTest(TestCase):
    """Test tasks today API."""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')
        
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
    
    def test_get_todays_tasks_empty(self):
        """Test getting today's tasks when none exist."""
        response = self.client.get('/api/tasks/today/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['tasks'], [])
    
    def test_get_todays_tasks_with_data(self):
        """Test getting today's tasks with data."""
        WorkerTask.objects.create(
            package=self.package,
            rotation_plan=self.plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.PENDING
        )
        
        response = self.client.get('/api/tasks/today/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['tasks']), 1)


class FreezeStartApiTest(TestCase):
    """Test freeze start API."""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')
        
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
    
    def test_freeze_start_success(self):
        """Test successful freeze start."""
        response = self.client.post(
            '/api/tasks/freeze/start/',
            data=json.dumps({'package_id': self.package.pk}),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Verify package state changed
        self.package.refresh_from_db()
        self.assertEqual(self.package.current_state, PackageState.FREEZING)
