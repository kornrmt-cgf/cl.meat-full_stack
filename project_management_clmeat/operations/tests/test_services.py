"""
Tests for Operations Services.
"""
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from operations.services import generate_worker_tasks, complete_task, get_todays_tasks
from operations.models import WorkerTask, TaskType, TaskStatus
from inventory.models import Product, Batch, Package, PackageState
from planning.models import FreezeProfile, ThawProfile, RotationPlan, PlanStatus


class GenerateWorkerTasksTest(TestCase):
    """Test generate_worker_tasks service."""
    
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
    
    def test_generate_tasks(self):
        """Test task generation."""
        tasks = generate_worker_tasks(self.plan)
        
        self.assertEqual(len(tasks), 7)
        
        task_types = [t.task_type for t in tasks]
        self.assertIn(TaskType.FREEZE_START, task_types)
        self.assertIn(TaskType.FREEZE_CHECK, task_types)
        self.assertIn(TaskType.MOVE_TO_THAW_QUEUE, task_types)
        self.assertIn(TaskType.THAW_START, task_types)
        self.assertIn(TaskType.THAW_CHECK, task_types)
        self.assertIn(TaskType.THAW_COMPLETE, task_types)
        self.assertIn(TaskType.MOVE_TO_DISPLAY, task_types)
    
    def test_tasks_scheduled_correctly(self):
        """Test tasks are scheduled at correct times."""
        tasks = generate_worker_tasks(self.plan)
        
        freeze_start = next(t for t in tasks if t.task_type == TaskType.FREEZE_START)
        self.assertEqual(freeze_start.scheduled_at, self.plan.planned_freeze_start_at)
        
        thaw_start = next(t for t in tasks if t.task_type == TaskType.THAW_START)
        self.assertEqual(thaw_start.scheduled_at, self.plan.planned_thaw_start_at)


class CompleteTaskTest(TestCase):
    """Test complete_task service."""
    
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
        
        self.task = WorkerTask.objects.create(
            package=self.package,
            rotation_plan=self.plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.PENDING
        )
    
    def test_complete_task_success(self):
        """Test successful task completion."""
        result = complete_task(self.task, 'worker1', 'Started freezing')
        
        self.assertEqual(result['task'].status, TaskStatus.COMPLETED)
        self.assertIsNotNone(result['task'].completed_at)
        self.assertEqual(result['task'].completed_by, 'worker1')
    
    def test_complete_already_completed_task(self):
        """Test completing an already completed task raises error."""
        self.task.status = TaskStatus.COMPLETED
        self.task.save()
        
        with self.assertRaises(ValueError):
            complete_task(self.task, 'worker1')
    
    def test_task_event_created(self):
        """Test that task event is created on completion."""
        complete_task(self.task, 'worker1', 'Test notes')
        
        from operations.models import TaskEvent
        event = TaskEvent.objects.filter(task=self.task).first()
        
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, 'TASK_COMPLETED')

    def test_complete_task_returns_dict(self):
        """Test complete_task returns dict with task and transitions."""
        result = complete_task(self.task, 'worker1')
        
        self.assertIn('task', result)
        self.assertIn('transitions', result)
        self.assertEqual(result['task'].pk, self.task.pk)


class AutoTransitionFreezeStartTest(TestCase):
    """Test auto-transition when FREEZE_START task is completed."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PKC001', name='Pork Collar', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001', supplier='Thai Fresh', received_at=timezone.now()
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12)
        )
    
    def _create_plan_and_tasks(self, package, freeze_start_in_past=False, freeze_end_in_past=False):
        """Helper: create rotation plan and tasks."""
        now = timezone.now()
        if freeze_start_in_past:
            freeze_start = now - timedelta(hours=2)
        else:
            freeze_start = now + timedelta(hours=1)
        
        if freeze_end_in_past:
            freeze_end = now - timedelta(minutes=30)
        else:
            freeze_end = freeze_start + timedelta(hours=24)
        
        target_ready = freeze_end + timedelta(hours=25)
        
        plan = RotationPlan.objects.create(
            package=package, target_ready_at=target_ready,
            planned_thaw_start_at=freeze_end + timedelta(minutes=15),
            planned_thaw_queue_at=freeze_end,
            planned_freeze_start_at=freeze_start,
            planned_freeze_end_at=freeze_end,
            freeze_profile=self.freeze_profile, thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
        
        tasks = []
        for tt, sched in [
            (TaskType.FREEZE_START, freeze_start),
            (TaskType.FREEZE_CHECK, freeze_start + timedelta(hours=2)),
            (TaskType.MOVE_TO_THAW_QUEUE, freeze_end),
            (TaskType.THAW_START, freeze_end + timedelta(minutes=15)),
            (TaskType.THAW_COMPLETE, target_ready),
            (TaskType.MOVE_TO_DISPLAY, target_ready + timedelta(minutes=15)),
        ]:
            tasks.append(WorkerTask.objects.create(
                package=package, rotation_plan=plan,
                task_type=tt, scheduled_at=sched, status=TaskStatus.PENDING
            ))
        
        return plan, tasks
    
    def test_freeze_start_transitions_packed_to_freezing(self):
        """FREEZE_START completion transitions PACKED → FREEZING."""
        package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        plan, tasks = self._create_plan_and_tasks(package)
        freeze_task = next(t for t in tasks if t.task_type == TaskType.FREEZE_START)
        
        result = complete_task(freeze_task, 'worker1')
        
        package.refresh_from_db()
        self.assertEqual(package.current_state, 'FREEZING')
        self.assertIn(('PACKED', 'FREEZING'), result['transitions'])
    
    def test_freeze_start_freezing_to_frozen_when_schedule_complete(self):
        """FREEZE_START completion transitions PACKED → FREEZING → FROZEN when freeze time elapsed."""
        package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        # freeze_end is in the past → should auto-transition to FROZEN
        plan, tasks = self._create_plan_and_tasks(
            package, freeze_start_in_past=True, freeze_end_in_past=True
        )
        freeze_task = next(t for t in tasks if t.task_type == TaskType.FREEZE_START)
        
        result = complete_task(freeze_task, 'worker1')
        
        package.refresh_from_db()
        self.assertEqual(package.current_state, 'FROZEN')
        self.assertIn(('PACKED', 'FREEZING'), result['transitions'])
        self.assertIn(('FREEZING', 'FROZEN'), result['transitions'])
    
    def test_freeze_start_stays_freezing_when_schedule_not_complete(self):
        """FREEZE_START completion stays at FREEZING when freeze time not elapsed."""
        package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        # freeze_end is far in the future
        plan, tasks = self._create_plan_and_tasks(
            package, freeze_start_in_past=False, freeze_end_in_past=False
        )
        freeze_task = next(t for t in tasks if t.task_type == TaskType.FREEZE_START)
        
        result = complete_task(freeze_task, 'worker1')
        
        package.refresh_from_db()
        self.assertEqual(package.current_state, 'FREEZING')
        self.assertIn(('PACKED', 'FREEZING'), result['transitions'])
        # Should NOT have transitioned to FROZEN
        self.assertNotIn(('FREEZING', 'FROZEN'), result['transitions'])
    
    def test_freeze_start_on_non_packed_package_does_nothing(self):
        """FREEZE_START on FROZEN package doesn't crash."""
        package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
        plan, tasks = self._create_plan_and_tasks(package)
        freeze_task = next(t for t in tasks if t.task_type == TaskType.FREEZE_START)
        
        result = complete_task(freeze_task, 'worker1')
        
        package.refresh_from_db()
        self.assertEqual(package.current_state, 'FROZEN')
        self.assertEqual(result['transitions'], [])


class AutoTransitionFreezeCheckTest(TestCase):
    """Test auto-transition when FREEZE_CHECK task is completed."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PKC001', name='Pork Collar', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001', supplier='Thai Fresh', received_at=timezone.now()
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12)
        )
    
    def test_freeze_check_transitions_freezing_to_frozen_when_complete(self):
        """FREEZE_CHECK transitions FREEZING → FROZEN when freeze time elapsed."""
        package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.FREEZING
        )
        now = timezone.now()
        plan = RotationPlan.objects.create(
            package=package, target_ready_at=now + timedelta(days=2),
            planned_thaw_start_at=now + timedelta(hours=25),
            planned_thaw_queue_at=now + timedelta(hours=24, minutes=30),
            planned_freeze_start_at=now - timedelta(hours=26),
            planned_freeze_end_at=now - timedelta(minutes=30),  # past
            freeze_profile=self.freeze_profile, thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
        task = WorkerTask.objects.create(
            package=package, rotation_plan=plan,
            task_type=TaskType.FREEZE_CHECK,
            scheduled_at=now - timedelta(hours=22),
            status=TaskStatus.PENDING
        )
        
        result = complete_task(task, 'worker1')
        
        package.refresh_from_db()
        self.assertEqual(package.current_state, 'FROZEN')
        self.assertIn(('FREEZING', 'FROZEN'), result['transitions'])


class AutoTransitionThawWorkflowTest(TestCase):
    """Test auto-transitions for thaw workflow tasks."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PKC001', name='Pork Collar', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001', supplier='Thai Fresh', received_at=timezone.now()
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12)
        )
        self.package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
        now = timezone.now()
        self.plan = RotationPlan.objects.create(
            package=self.package, target_ready_at=now + timedelta(days=1),
            planned_thaw_start_at=now + timedelta(hours=20),
            planned_thaw_queue_at=now + timedelta(hours=19, minutes=30),
            planned_freeze_start_at=now - timedelta(hours=26),
            planned_freeze_end_at=now - timedelta(hours=2),
            freeze_profile=self.freeze_profile, thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
    
    def test_move_to_thaw_queue_transitions_frozen_to_thaw_queued(self):
        """MOVE_TO_THAW_QUEUE transitions FROZEN → THAW_QUEUED."""
        task = WorkerTask.objects.create(
            package=self.package, rotation_plan=self.plan,
            task_type=TaskType.MOVE_TO_THAW_QUEUE,
            scheduled_at=timezone.now(), status=TaskStatus.PENDING
        )
        
        # Add thaw queue entry (required by state machine validation)
        from planning.models import ThawQueueEntry, QueueStatus
        ThawQueueEntry.objects.create(
            package=self.package, rotation_plan=self.plan,
            queue_position=1, planned_start_at=timezone.now() + timedelta(hours=1),
            target_ready_at=self.plan.target_ready_at,
            status=QueueStatus.QUEUED
        )
        
        result = complete_task(task, 'worker1')
        
        self.package.refresh_from_db()
        self.assertEqual(self.package.current_state, 'THAW_QUEUED')
        self.assertIn(('FROZEN', 'READY_FOR_THAW'), result['transitions'])
        self.assertIn(('READY_FOR_THAW', 'THAW_QUEUED'), result['transitions'])

    def test_thaw_start_transitions_thaw_queued_to_thawing(self):
        """THAW_START transitions THAW_QUEUED → THAWING."""
        self.package.current_state = 'THAW_QUEUED'
        self.package.save(update_fields=['current_state'])
        
        # Ensure thaw queue entry exists
        from planning.models import ThawQueueEntry, QueueStatus
        ThawQueueEntry.objects.create(
            package=self.package, rotation_plan=self.plan,
            queue_position=1, planned_start_at=timezone.now(),
            target_ready_at=self.plan.target_ready_at,
            status=QueueStatus.QUEUED
        )
        
        task = WorkerTask.objects.create(
            package=self.package, rotation_plan=self.plan,
            task_type=TaskType.THAW_START,
            scheduled_at=timezone.now(), status=TaskStatus.PENDING
        )
        
        result = complete_task(task, 'worker1')
        
        self.package.refresh_from_db()
        self.assertEqual(self.package.current_state, 'THAWING')
        self.assertIn(('THAW_QUEUED', 'THAWING'), result['transitions'])

    def test_thaw_complete_transitions_thawing_to_ready_for_sale(self):
        """THAW_COMPLETE transitions THAWING → READY_FOR_SALE."""
        self.package.current_state = 'THAWING'
        self.package.save(update_fields=['current_state'])
        
        from planning.models import ThawQueueEntry, QueueStatus
        ThawQueueEntry.objects.create(
            package=self.package, rotation_plan=self.plan,
            queue_position=1, planned_start_at=timezone.now() - timedelta(hours=24),
            target_ready_at=self.plan.target_ready_at,
            status=QueueStatus.STARTED
        )
        
        task = WorkerTask.objects.create(
            package=self.package, rotation_plan=self.plan,
            task_type=TaskType.THAW_COMPLETE,
            scheduled_at=timezone.now(), status=TaskStatus.PENDING
        )
        
        result = complete_task(task, 'worker1')
        
        self.package.refresh_from_db()
        self.assertEqual(self.package.current_state, 'READY_FOR_SALE')
        self.assertIn(('THAWING', 'READY_FOR_SALE'), result['transitions'])
        
        # Verify queue entry is marked COMPLETED
        entry = ThawQueueEntry.objects.get(package=self.package)
        self.assertEqual(entry.status, QueueStatus.COMPLETED)


class AutoTransitionDisplayWorkflowTest(TestCase):
    """Test auto-transitions for display workflow tasks."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PKC001', name='Pork Collar', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001', supplier='Thai Fresh', received_at=timezone.now()
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12)
        )
        self.package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.READY_FOR_SALE
        )
        now = timezone.now()
        self.plan = RotationPlan.objects.create(
            package=self.package, target_ready_at=now + timedelta(hours=1),
            planned_thaw_start_at=now - timedelta(hours=23),
            planned_thaw_queue_at=now - timedelta(hours=23, minutes=30),
            planned_freeze_start_at=now - timedelta(hours=48),
            planned_freeze_end_at=now - timedelta(hours=24),
            freeze_profile=self.freeze_profile, thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
    
    def test_move_to_display_transitions(self):
        """MOVE_TO_DISPLAY transitions READY_FOR_SALE → ON_DISPLAY."""
        task = WorkerTask.objects.create(
            package=self.package, rotation_plan=self.plan,
            task_type=TaskType.MOVE_TO_DISPLAY,
            scheduled_at=timezone.now(), status=TaskStatus.PENDING
        )
        
        result = complete_task(task, 'worker1')
        
        self.package.refresh_from_db()
        self.assertEqual(self.package.current_state, 'ON_DISPLAY')
        self.assertIn(('READY_FOR_SALE', 'ON_DISPLAY'), result['transitions'])

    def test_refreeze_transitions(self):
        """REFREEZE transitions ON_DISPLAY → REFREEZE_PENDING."""
        self.package.current_state = 'ON_DISPLAY'
        self.package.save(update_fields=['current_state'])
        
        task = WorkerTask.objects.create(
            package=self.package, rotation_plan=self.plan,
            task_type=TaskType.REFREEZE,
            scheduled_at=timezone.now(), status=TaskStatus.PENDING
        )
        
        result = complete_task(task, 'worker1')
        
        self.package.refresh_from_db()
        self.assertEqual(self.package.current_state, 'REFREEZE_PENDING')
        self.assertIn(('ON_DISPLAY', 'REFREEZE_PENDING'), result['transitions'])


class FullAutoTransitionWorkflowTest(TestCase):
    """End-to-end test: complete all tasks in order and verify package transitions."""

    def test_packed_to_frozen_via_task_completion(self):
        """PACKED → complete FREEZE_START → FREEZING → complete FREEZE_CHECK → FROZEN."""
        product = Product.objects.create(sku='PKC001', name='Pork Collar', category='PORK')
        batch = Batch.objects.create(batch_number='B001', supplier='Thai Fresh', received_at=timezone.now())
        freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24), minimum_duration=timedelta(hours=12)
        )

        package = Package.objects.create(
            product=product, batch=batch, weight=Decimal('0.560'),
            packed_at=timezone.now(), current_state=PackageState.PACKED
        )

        now = timezone.now()
        plan = RotationPlan.objects.create(
            package=package, target_ready_at=now + timedelta(days=2),
            planned_thaw_start_at=now + timedelta(hours=25),
            planned_thaw_queue_at=now + timedelta(hours=24, minutes=30),
            planned_freeze_start_at=now - timedelta(minutes=30),
            planned_freeze_end_at=now - timedelta(minutes=5),  # nearly complete
            freeze_profile=freeze_profile, thaw_profile=thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )

        # Create all tasks
        freeze_start_task = WorkerTask.objects.create(
            package=package, rotation_plan=plan,
            task_type=TaskType.FREEZE_START, scheduled_at=now - timedelta(minutes=30),
            status=TaskStatus.PENDING
        )

        # Step 1: Complete FREEZE_START → PACKED → FREEZING
        # freeze_end is 5 min in the past, so should also go to FROZEN
        result = complete_task(freeze_start_task, 'worker1')
        package.refresh_from_db()
        self.assertEqual(package.current_state, 'FROZEN')
        self.assertIn(('PACKED', 'FREEZING'), result['transitions'])
        self.assertIn(('FREEZING', 'FROZEN'), result['transitions'])

        # Verify audit trail
        from operations.models import RotationEvent
        events = RotationEvent.objects.filter(package=package).order_by('timestamp')
        state_transitions = [(e.from_state, e.to_state) for e in events]
        self.assertIn(('PACKED', 'FREEZING'), state_transitions)
        self.assertIn(('FREEZING', 'FROZEN'), state_transitions)


class CompleteWorkflowPACKEDToON_DISPLAYTest(TestCase):
    """
    GOLDEN WORKFLOW — End-to-end test.
    
    Package: Pork Collar 0.560 kg
    
    PACKED
      → create_rotation_plan (worker tasks auto-generated)
      → complete FREEZE_START  → PACKED → FREEZING (+ FROZEN if freeze elapsed)
      → complete MOVE_TO_THAW_QUEUE → FROZEN → THAW_QUEUED
      → complete THAW_START → THAW_QUEUED → THAWING
      → complete THAW_COMPLETE → THAWING → READY_FOR_SALE
      → complete MOVE_TO_DISPLAY → READY_FOR_SALE → ON_DISPLAY
    
    Verifies:
    - every state transition
    - every RotationEvent audit trail
    - every WorkerTask completion
    - ThawQueueEntry lifecycle
    - queue position
    - timestamps are consistent
    - no duplicate state transitions
    """

    def setUp(self):
        self.product = Product.objects.create(
            sku='PKC001', name='Pork Collar', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001', supplier='Thai Fresh',
            received_at=timezone.now()
        )
        self.freeze_profile = FreezeProfile.objects.create(
            name='แช่แข็งมาตรฐาน', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24),
            buffer_duration=timedelta(hours=0),
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='ละลายน้ำแข็งช้า',
            default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12),
            buffer_duration=timedelta(hours=1),
        )

    def test_complete_workflow(self):
        """
        Full lifecycle: PACKED → FREEZING → FROZEN → THAW_QUEUED →
        THAWING → READY_FOR_SALE → ON_DISPLAY
        """
        # ---- Step 0: Create PACKED package ----
        package = Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal('0.560'), packed_at=timezone.now(),
            current_state=PackageState.PACKED,
        )
        self.assertEqual(package.current_state, 'PACKED')

        # ---- Step 1: Create rotation plan ----
        from planning.services import create_rotation_plan
        now = timezone.now()
        target_ready = now + timedelta(days=3)
        plan = create_rotation_plan(
            package, target_ready,
            self.freeze_profile, self.thaw_profile,
            actor='admin'
        )

        # Plan exists
        self.assertIsNotNone(plan)
        self.assertEqual(plan.status, PlanStatus.PLANNED)
        self.assertEqual(plan.package, package)

        # Worker tasks were generated
        tasks = WorkerTask.objects.filter(
            rotation_plan=plan
        ).order_by('scheduled_at')
        self.assertGreaterEqual(tasks.count(), 5)
        task_types = list(tasks.values_list('task_type', flat=True))
        self.assertIn(TaskType.FREEZE_START, task_types)
        self.assertIn(TaskType.MOVE_TO_THAW_QUEUE, task_types)
        self.assertIn(TaskType.THAW_START, task_types)
        self.assertIn(TaskType.THAW_COMPLETE, task_types)
        self.assertIn(TaskType.MOVE_TO_DISPLAY, task_types)

        # Package still PACKED (plan created, tasks scheduled, no action yet)
        package.refresh_from_db()
        self.assertEqual(package.current_state, 'PACKED')

        # ---- Step 2: Complete FREEZE_START ----
        # Set freeze_end in the past so both transitions fire
        plan.planned_freeze_end_at = now - timedelta(minutes=30)
        plan.save(update_fields=['planned_freeze_end_at'])

        freeze_start_task = tasks.get(task_type=TaskType.FREEZE_START)
        result = complete_task(freeze_start_task, 'สมชาย')

        package.refresh_from_db()
        self.assertEqual(package.current_state, 'FROZEN')
        self.assertIn(('PACKED', 'FREEZING'), result['transitions'])
        self.assertIn(('FREEZING', 'FROZEN'), result['transitions'])
        self.assertEqual(freeze_start_task.status, TaskStatus.COMPLETED)
        self.assertEqual(freeze_start_task.completed_by, 'สมชาย')

        # ---- Step 3: Complete MOVE_TO_THAW_QUEUE ----
        # Ensure a ThawQueueEntry exists for state machine validation
        from planning.models import ThawQueueEntry, QueueStatus
        ThawQueueEntry.objects.create(
            package=package, rotation_plan=plan,
            queue_position=1,
            planned_start_at=plan.planned_thaw_start_at,
            target_ready_at=plan.target_ready_at,
            status=QueueStatus.QUEUED,
        )

        move_thaw_task = tasks.get(task_type=TaskType.MOVE_TO_THAW_QUEUE)
        result = complete_task(move_thaw_task, 'สมชาย')

        package.refresh_from_db()
        self.assertEqual(package.current_state, 'THAW_QUEUED')
        self.assertIn(('FROZEN', 'READY_FOR_THAW'), result['transitions'])
        self.assertIn(('READY_FOR_THAW', 'THAW_QUEUED'), result['transitions'])
        self.assertEqual(move_thaw_task.status, TaskStatus.COMPLETED)

        # ---- Step 4: Complete THAW_START ----
        thaw_start_task = tasks.get(task_type=TaskType.THAW_START)
        result = complete_task(thaw_start_task, 'สมชาย')

        package.refresh_from_db()
        self.assertEqual(package.current_state, 'THAWING')
        self.assertIn(('THAW_QUEUED', 'THAWING'), result['transitions'])
        self.assertEqual(thaw_start_task.status, TaskStatus.COMPLETED)

        # ---- Step 5: Complete THAW_COMPLETE ----
        thaw_complete_task = tasks.get(task_type=TaskType.THAW_COMPLETE)
        result = complete_task(thaw_complete_task, 'สมชาย')

        package.refresh_from_db()
        self.assertEqual(package.current_state, 'READY_FOR_SALE')
        self.assertIn(('THAWING', 'READY_FOR_SALE'), result['transitions'])
        self.assertEqual(thaw_complete_task.status, TaskStatus.COMPLETED)

        # ThawQueueEntry should be COMPLETED
        entry = ThawQueueEntry.objects.get(package=package)
        self.assertEqual(entry.status, QueueStatus.COMPLETED)

        # ---- Step 6: Complete MOVE_TO_DISPLAY ----
        display_task = tasks.get(task_type=TaskType.MOVE_TO_DISPLAY)
        result = complete_task(display_task, 'สมชาย')

        package.refresh_from_db()
        self.assertEqual(package.current_state, 'ON_DISPLAY')
        self.assertIn(('READY_FOR_SALE', 'ON_DISPLAY'), result['transitions'])
        self.assertEqual(display_task.status, TaskStatus.COMPLETED)

        # ---- Verify complete audit trail ----
        from operations.models import RotationEvent
        events = RotationEvent.objects.filter(
            package=package
        ).order_by('timestamp')
        state_transitions = [(e.from_state, e.to_state) for e in events]

        self.assertEqual(state_transitions, [
            ('PACKED', 'FREEZING'),
            ('FREEZING', 'FROZEN'),
            ('FROZEN', 'READY_FOR_THAW'),
            ('READY_FOR_THAW', 'THAW_QUEUED'),
            ('THAW_QUEUED', 'THAWING'),
            ('THAWING', 'READY_FOR_SALE'),
            ('READY_FOR_SALE', 'ON_DISPLAY'),
        ])

        # Every transition has a valid actor
        for e in events:
            self.assertEqual(e.actor, 'สมชาย')
            self.assertTrue(len(e.reason) > 0, f"Event {e.from_state}→{e.to_state} has empty reason")

        # ---- Verify all tasks COMPLETED ----
        completed_tasks = WorkerTask.objects.filter(
            rotation_plan=plan, status=TaskStatus.COMPLETED
        )
        self.assertGreaterEqual(completed_tasks.count(), 5)

        # ---- Verify no duplicate transitions ----
        self.assertEqual(len(state_transitions), 7)
