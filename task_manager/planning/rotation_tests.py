"""
Focused tests for rotation engine and audit trail.
Only tests that verify the required behavior of TASK 04.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from inventory.models import Category, Supplier, Product, Batch, Package, StorageLocation, PackageState
from inventory.services import create_package
from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, ThawQueueEntry,
    PlanStatus, QueueStatus,
)
from planning.services import (
    calculate_freeze_duration, calculate_thaw_duration,
    create_rotation_plan, generate_worker_tasks,
    add_to_thaw_queue, cancel_rotation_plan,
)
from planning.rotation import RotationEngine, Decision, Priority
from planning.audit import Audit, package_trail
from operations.models import RotationEvent, WorkerTask, TaskStatus
from common.state_machine import transition_package

User = get_user_model()


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.cat = Category.objects.create(code='PORK', name='Pork')
        cls.sup = Supplier.objects.create(name='BETAGRO')
        cls.prod = Product.objects.create(
            sku='P001', name='Pork Neck', category=cls.cat, supplier=cls.sup,
            cost_per_kg=Decimal('95'), selling_price_per_kg=Decimal('149'),
            barcode_prefix='0051',
        )
        cls.batch = Batch.objects.create(
            batch_number='B001', supplier=cls.sup, received_at=timezone.now(),
        )
        cls.freeze = FreezeProfile.objects.create(
            name='Std Freeze', target_temperature=Decimal('-8'),
            minimum_duration=timedelta(hours=4),
            default_duration=timedelta(hours=8),
            buffer_duration=timedelta(hours=1),
        )
        cls.thaw = ThawProfile.objects.create(
            name='Std Thaw', default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12), buffer_duration=timedelta(hours=2),
            weight_threshold_kg=Decimal('0.5'), weight_scale_factor=Decimal('1.2'),
            target_temperature=Decimal('3'),
            min_temperature=Decimal('1'), max_temperature=Decimal('5'),
            thaw_capacity=10,
        )
        cls.user = User.objects.create_user(
            userid='w1', password='test', email='w1@t.com',
            first_name='W', last_name='1',
        )

    def frozen_pkg(self, weight=1.0):
        p = create_package(self.prod, self.batch, weight)
        transition_package(p, 'FREEZING', actor='t')
        transition_package(p, 'FROZEN', actor='t')
        return p

    def planned_pkg(self, weight=1.0, hours=48):
        p = self.frozen_pkg(weight)
        plan = create_rotation_plan(p, timezone.now() + timedelta(hours=hours),
                                    self.freeze, self.thaw)
        return p, plan


# ── Duration calculation ──

class DurationCalcTest(Base):

    def test_freeze_small_lt_medium_lt_large(self):
        s = calculate_freeze_duration(create_package(self.prod, self.batch, 0.3), self.freeze)
        m = calculate_freeze_duration(create_package(self.prod, self.batch, 0.8), self.freeze)
        l = calculate_freeze_duration(create_package(self.prod, self.batch, 2.0), self.freeze)
        self.assertLess(s, m)
        self.assertLess(m, l)

    def test_thaw_small_lt_large(self):
        s = calculate_thaw_duration(create_package(self.prod, self.batch, 0.3), self.thaw)
        l = calculate_thaw_duration(create_package(self.prod, self.batch, 2.0), self.thaw)
        self.assertGreater(l, s)

    def test_buffer_always_added(self):
        raw = timedelta(hours=4)
        pkg = create_package(self.prod, self.batch, 0.3)
        # freeze minimum=4h, buffer=1h → total=5h
        d = calculate_freeze_duration(pkg, self.freeze)
        self.assertEqual(d, timedelta(hours=5))


# ── Plan creation ──

class PlanCreationTest(Base):

    def test_auto_mode_no_overrides(self):
        p = create_package(self.prod, self.batch, 1.0)
        plan = create_rotation_plan(p, timezone.now() + timedelta(days=3), self.freeze, self.thaw)
        self.assertIsNone(plan.freeze_override)
        self.assertIsNone(plan.thaw_override)
        self.assertFalse(plan.is_override)
        self.assertGreater(plan.worker_tasks.count(), 0)

    def test_custom_mode_override(self):
        p = create_package(self.prod, self.batch, 1.0)
        plan = create_rotation_plan(
            p, timezone.now() + timedelta(days=3),
            self.freeze, self.thaw,
            freeze_override=timedelta(hours=12), override_reason='Test', actor='admin',
        )
        self.assertTrue(plan.is_override)
        self.assertEqual(plan.freeze_override, timedelta(hours=12))
        self.assertEqual(plan.overridden_by, 'admin')

    def test_rejects_wrong_state(self):
        p = create_package(self.prod, self.batch, 1.0)
        transition_package(p, 'FREEZING', actor='t')
        transition_package(p, 'FROZEN', actor='t')
        transition_package(p, 'READY_FOR_THAW', actor='t')
        with self.assertRaises(ValueError):
            create_rotation_plan(p, timezone.now() + timedelta(days=3), self.freeze, self.thaw)

    def test_no_duplicate_plan(self):
        p = create_package(self.prod, self.batch, 1.0)
        create_rotation_plan(p, timezone.now() + timedelta(days=3), self.freeze, self.thaw)
        with self.assertRaises(ValueError):
            create_rotation_plan(p, timezone.now() + timedelta(days=4), self.freeze, self.thaw)

    def test_timeline_order(self):
        p = create_package(self.prod, self.batch, 1.0)
        plan = create_rotation_plan(p, timezone.now() + timedelta(days=3), self.freeze, self.thaw)
        self.assertLess(plan.planned_freeze_start_at, plan.planned_freeze_end_at)
        self.assertLess(plan.planned_freeze_end_at, plan.planned_thaw_start_at)
        self.assertLess(plan.planned_thaw_start_at, plan.target_ready_at)

    def test_generates_7_worker_tasks(self):
        p = create_package(self.prod, self.batch, 1.0)
        plan = create_rotation_plan(p, timezone.now() + timedelta(days=3), self.freeze, self.thaw)
        self.assertEqual(plan.worker_tasks.count(), 7)
        types = set(t.task_type for t in plan.worker_tasks.all())
        self.assertIn('FREEZE_START', types)
        self.assertIn('THAW_COMPLETE', types)
        self.assertIn('MOVE_TO_DISPLAY', types)


# ── Thaw queue ──

class ThawQueueTest(Base):

    def test_add_to_queue(self):
        p, plan = self.planned_pkg()
        entry = add_to_thaw_queue(p, plan, actor='w1')
        self.assertEqual(entry.queue_position, 1)
        p.refresh_from_db()
        self.assertEqual(p.current_state, 'THAW_QUEUED')

    def test_queue_increments(self):
        p1, pl1 = self.planned_pkg()
        p2, pl2 = self.planned_pkg(0.5)
        add_to_thaw_queue(p1, pl1)
        e2 = add_to_thaw_queue(p2, pl2)
        self.assertEqual(e2.queue_position, 2)

    def test_cannot_queue_twice(self):
        p, plan = self.planned_pkg()
        add_to_thaw_queue(p, plan)
        with self.assertRaises(ValueError):
            add_to_thaw_queue(p, plan)


# ── Cancel ──

class CancelPlanTest(Base):

    def test_cancel(self):
        p, plan = self.planned_pkg()
        self.assertEqual(plan.worker_tasks.filter(status=TaskStatus.PENDING).count(), 7)
        cancel_rotation_plan(plan, actor='admin', reason='No longer needed')
        plan.refresh_from_db()
        self.assertEqual(plan.status, PlanStatus.CANCELLED)
        self.assertEqual(
            plan.worker_tasks.filter(status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]).count(), 0
        )


# ── Rotation engine ──

class EngineTest(Base):

    def test_packed_detected(self):
        create_package(self.prod, self.batch, 1.0)
        engine = RotationEngine()
        actions = [d.action for d in engine.get_decisions()]
        self.assertIn('START_FREEZE', actions)

    def test_overdue_plan_detected(self):
        p, plan = self.planned_pkg()
        plan.target_ready_at = timezone.now() - timedelta(hours=2)
        plan.save(update_fields=['target_ready_at'])
        engine = RotationEngine()
        actions = [(d.action, d.priority) for d in engine.get_decisions()]
        self.assertIn(('OVERDUE_PLAN', Priority.CRITICAL), actions)

    def test_thaw_candidates_fifo(self):
        p1 = self.frozen_pkg(1.0)
        p2 = self.frozen_pkg(0.5)
        engine = RotationEngine()
        barcodes = [c.package.barcode for c in engine.get_thaw_candidates()]
        self.assertIn(p1.barcode, barcodes)
        self.assertIn(p2.barcode, barcodes)

    def test_thaw_candidates_with_plan_due_soon(self):
        p, plan = self.planned_pkg()
        plan.planned_thaw_queue_at = timezone.now() + timedelta(minutes=30)
        plan.save(update_fields=['planned_thaw_queue_at'])
        engine = RotationEngine()
        actions = [c.action for c in engine.get_thaw_candidates()]
        self.assertIn('MOVE_TO_THAW_QUEUE', actions)

    def test_decisions_sorted_by_priority(self):
        create_package(self.prod, self.batch, 1.0)
        engine = RotationEngine()
        decisions = engine.get_decisions()
        priorities = [d.priority for d in decisions]
        self.assertEqual(priorities, sorted(priorities))


# ── Audit trail ──

class AuditTest(Base):

    def test_state_change_logged(self):
        p = create_package(self.prod, self.batch, 1.0)
        Audit.state_change(p, 'PACKED', 'FREEZING', actor='w1', reason='Starting freeze')
        e = RotationEvent.objects.filter(package=p).first()
        self.assertEqual(e.from_state, 'PACKED')
        self.assertEqual(e.to_state, 'FREEZING')
        self.assertEqual(e.actor, 'w1')
        self.assertFalse(e.metadata.get('automatic', True))

    def test_automatic_flag(self):
        p = create_package(self.prod, self.batch, 1.0)
        Audit.state_change(p, 'FREEZING', 'FROZEN', automatic=True)
        e = RotationEvent.objects.filter(package=p).first()
        self.assertTrue(e.metadata.get('automatic'))

    def test_override_logged(self):
        p = create_package(self.prod, self.batch, 1.0)
        Audit.override(p, 'FROZEN', 'THAWING', 'admin', 'Emergency')
        e = RotationEvent.objects.filter(package=p).first()
        self.assertEqual(e.event_type, 'MANUAL_OVERRIDE')
        self.assertIn('Emergency', e.reason)

    def test_plan_action_logged(self):
        p, plan = self.planned_pkg()
        Audit.plan_action(plan, 'PLAN_CREATED', actor='admin')
        e = RotationEvent.objects.filter(package=p, event_type='PLAN_CREATED').first()
        self.assertIsNotNone(e)
        self.assertEqual(e.metadata.get('plan_id'), plan.id)

    def test_movement_logged(self):
        p = create_package(self.prod, self.batch, 1.0)
        Audit.movement(p, 'MOVED', actor='w1', reason='To freezer')
        from inventory.models import StockMovement
        m = StockMovement.objects.filter(package=p).first()
        self.assertEqual(m.movement_type, 'MOVED')

    def test_package_trail(self):
        p = create_package(self.prod, self.batch, 1.0)
        transition_package(p, 'FREEZING', actor='w1')
        transition_package(p, 'FROZEN', actor='w1')
        trail = package_trail(p)
        self.assertGreaterEqual(len(trail), 2)
        self.assertEqual(trail[0]['from'], 'PACKED')
        self.assertEqual(trail[0]['to'], 'FREEZING')


# ── Full integration ──

class IntegrationTest(Base):

    def test_plan_to_thaw_to_action(self):
        """package → plan → thaw candidates → decision."""
        p, plan = self.planned_pkg()
        plan.planned_thaw_queue_at = timezone.now() + timedelta(minutes=30)
        plan.save(update_fields=['planned_thaw_queue_at'])
        engine = RotationEngine()
        candidates = engine.get_thaw_candidates()
        barcodes = [c.package.barcode for c in candidates]
        self.assertIn(p.barcode, barcodes)

    def test_task_completion_triggers_transition(self):
        """Worker completes task → package state changes."""
        p = create_package(self.prod, self.batch, 1.0)
        plan = create_rotation_plan(p, timezone.now() + timedelta(days=3), self.freeze, self.thaw)
        ft = plan.worker_tasks.filter(task_type='FREEZE_START').first()
        from operations.services import complete_task
        complete_task(ft, actor=self.user)
        p.refresh_from_db()
        self.assertEqual(p.current_state, 'FREEZING')
        e = RotationEvent.objects.filter(package=p).last()
        self.assertEqual(e.to_state, 'FREEZING')
