"""
Comprehensive tests for CL.MEAT Inventory & Fresh Meat Stock System.

Tests cover:
- Product, Batch, Package creation
- StorageLocation
- Stock movement
- Lifecycle transitions (valid + invalid)
- Package traceability
- Configuration (AUTO/CUSTOM modes)
- State machine
- Worker task generation
- Planning services
- Barcode generation
- Price calculation
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from inventory.models import (
    Category, Supplier, Product, Batch, Package, StorageLocation,
    StockMovement, TemperatureLog, PackageState, PriceChangeHistory,
    BarcodeSequence,
)
from inventory.services import (
    create_product, create_batch, create_storage_location,
    create_package, calculate_package_price, generate_barcode,
    move_package, adjust_package_price, get_packages_by_state,
    get_available_for_planning, get_package_by_barcode,
)
from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, ThawQueueEntry,
    PlanStatus, QueueStatus,
)
from planning.services import (
    calculate_freeze_duration, calculate_thaw_duration,
    create_rotation_plan, generate_worker_tasks,
    add_to_thaw_queue, get_best_thaw_profile, get_best_freeze_profile,
)
from operations.models import WorkerTask, TaskEvent, RotationEvent, TaskType, TaskStatus
from operations.services import complete_task, get_todays_tasks, update_task_status
from common.state_machine import (
    transition_package, can_transition, get_allowed_transitions,
    is_terminal, InvalidTransitionError, TransitionValidationError,
    TRANSITIONS, ALL_STATES,
)

User = get_user_model()


# ============================================================
# BASE TEST FIXTURE
# ============================================================

class InventoryBaseTestCase(TestCase):
    """Base test case with common fixtures."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(code='PORK', name='Pork', name_thai='หมู')
        cls.supplier = Supplier.objects.create(name='BETAGRO', locations='Bangkok')
        cls.product = Product.objects.create(
            sku='PORK-NECK-001', name='Pork Neck', name_thai='สันคอหมู',
            category=cls.category, supplier=cls.supplier,
            cost_per_kg=Decimal('95.00'), selling_price_per_kg=Decimal('149.00'),
            barcode_prefix='0051',
        )
        cls.batch = Batch.objects.create(
            batch_number='B-20260829-001', supplier=cls.supplier,
            received_at=timezone.now(),
        )
        cls.freezer = StorageLocation.objects.create(
            name='FREEZER-A1', location_type='FREEZER', capacity=50
        )
        cls.thaw_area = StorageLocation.objects.create(
            name='THAW-01', location_type='THAW_AREA', capacity=20, thaw_capacity=10
        )
        cls.display = StorageLocation.objects.create(
            name='DISPLAY-01', location_type='DISPLAY', capacity=30
        )
        cls.freeze_profile = FreezeProfile.objects.create(
            name='Standard Freeze', target_temperature=Decimal('-8.00'),
            minimum_duration=timedelta(hours=4), default_duration=timedelta(hours=8),
            buffer_duration=timedelta(hours=1),
        )
        cls.thaw_profile = ThawProfile.objects.create(
            name='Standard Thaw', default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12), buffer_duration=timedelta(hours=2),
            weight_threshold_kg=Decimal('0.500'), weight_scale_factor=Decimal('1.20'),
            target_temperature=Decimal('3.00'),
            min_temperature=Decimal('1.00'), max_temperature=Decimal('5.00'),
            thaw_capacity=10,
        )
        cls.user = User.objects.create_user(
            userid='w1', password='test1234', email='w1@test.com',
            first_name='Worker', last_name='One',
        )


# ============================================================
# PRODUCT TESTS
# ============================================================

class ProductModelTest(InventoryBaseTestCase):

    def test_product_creation(self):
        self.assertEqual(self.product.sku, 'PORK-NECK-001')
        self.assertEqual(self.product.name, 'Pork Neck')
        self.assertEqual(self.product.category.code, 'PORK')
        self.assertTrue(self.product.active)

    def test_product_str(self):
        self.assertIn('Pork Neck', str(self.product))

    def test_product_display_name(self):
        self.assertEqual(self.product.display_name, 'สันคอหมู')

    def test_category_emoji(self):
        self.assertEqual(self.category.emoji, '🐷')


# ============================================================
# BATCH TESTS
# ============================================================

class BatchModelTest(InventoryBaseTestCase):

    def test_batch_creation(self):
        self.assertEqual(self.batch.batch_number, 'B-20260829-001')
        self.assertTrue(self.batch.active)

    def test_batch_str(self):
        self.assertIn('B-20260829-001', str(self.batch))


# ============================================================
# STORAGE LOCATION TESTS
# ============================================================

class StorageLocationTest(InventoryBaseTestCase):

    def test_creation(self):
        self.assertEqual(self.freezer.location_type, 'FREEZER')

    def test_available_capacity_empty(self):
        self.assertEqual(self.freezer.available_capacity, 50)

    def test_available_capacity_with_packages(self):
        create_package(self.product, self.batch, 1.0, storage_location=self.freezer)
        self.assertEqual(self.freezer.current_count, 1)
        self.assertEqual(self.freezer.available_capacity, 49)


# ============================================================
# BARCODE GENERATION TESTS
# ============================================================

class BarcodeGenerationTest(InventoryBaseTestCase):

    def test_first_barcode(self):
        barcode = generate_barcode(self.product, self.batch)
        self.assertIn('0051', barcode)

    def test_sequential_barcodes(self):
        b1 = generate_barcode(self.product, self.batch)
        b2 = generate_barcode(self.product, self.batch)
        self.assertNotEqual(b1, b2)

    def test_barcode_uniqueness_across_packages(self):
        pkg1 = create_package(self.product, self.batch, 0.5)
        barcode2 = generate_barcode(self.product, self.batch)
        self.assertNotEqual(pkg1.barcode, barcode2)

    def test_requires_product(self):
        with self.assertRaises(ValueError):
            generate_barcode(None, self.batch)

    def test_requires_batch(self):
        with self.assertRaises(ValueError):
            generate_barcode(self.product, None)


# ============================================================
# PACKAGE CREATION TESTS
# ============================================================

class PackageCreationTest(InventoryBaseTestCase):

    def test_create_package_basic(self):
        pkg = create_package(self.product, self.batch, 1.234)
        self.assertEqual(pkg.product, self.product)
        self.assertEqual(pkg.weight, Decimal('1.234'))
        self.assertEqual(pkg.current_state, PackageState.PACKED)
        self.assertIsNotNone(pkg.barcode)

    def test_create_package_records_movement(self):
        pkg = create_package(self.product, self.batch, 1.0)
        self.assertEqual(pkg.movements.count(), 1)
        self.assertEqual(pkg.movements.first().movement_type, 'RECEIVED')

    def test_weight_must_be_positive(self):
        with self.assertRaises(ValueError):
            create_package(self.product, self.batch, 0)

    def test_auto_price_calculation(self):
        pkg = create_package(self.product, self.batch, 1.0)
        self.assertEqual(int(pkg.selling_price), 149)

    def test_fractional_weight_price(self):
        pkg = create_package(self.product, self.batch, 0.270)
        self.assertEqual(int(pkg.selling_price), 41)

    def test_is_active(self):
        pkg = create_package(self.product, self.batch, 1.0)
        self.assertTrue(pkg.is_active)

    def test_not_active_when_completed(self):
        pkg = create_package(self.product, self.batch, 1.0)
        pkg.current_state = PackageState.COMPLETED
        pkg.save()
        self.assertFalse(pkg.is_active)


# ============================================================
# PRICE CALCULATION TESTS
# ============================================================

class PriceCalculationTest(InventoryBaseTestCase):

    def test_auto_mode(self):
        price = calculate_package_price(self.product, 1.0, mode='auto')
        self.assertEqual(price, 149)

    def test_price_per_kg_mode(self):
        price = calculate_package_price(self.product, 2.0, mode='price_per_kg', value=200)
        self.assertEqual(price, 400)

    def test_cost_margin_mode(self):
        price = calculate_package_price(self.product, 1.0, mode='cost_margin', value=30)
        self.assertEqual(price, 124)

    def test_discount_mode(self):
        price = calculate_package_price(self.product, 1.0, mode='discount', value=10)
        self.assertEqual(price, 135)

    def test_zero_weight(self):
        self.assertEqual(calculate_package_price(self.product, 0), 0)

    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            calculate_package_price(self.product, 1.0, mode='invalid')


# ============================================================
# STATE MACHINE TESTS
# ============================================================

class StateMachineTest(InventoryBaseTestCase):

    def test_all_states_defined(self):
        self.assertEqual(len(ALL_STATES), 12)

    def test_can_transition_valid(self):
        self.assertTrue(can_transition('PACKED', 'FREEZING'))
        self.assertTrue(can_transition('FREEZING', 'FROZEN'))
        self.assertTrue(can_transition('FROZEN', 'READY_FOR_THAW'))
        self.assertTrue(can_transition('ON_DISPLAY', 'PROCESSING'))
        self.assertTrue(can_transition('PROCESSING', 'COMPLETED'))

    def test_can_transition_invalid(self):
        self.assertFalse(can_transition('PACKED', 'FROZEN'))
        self.assertFalse(can_transition('COMPLETED', 'PACKED'))

    def test_terminal_state(self):
        self.assertTrue(is_terminal('COMPLETED'))
        self.assertFalse(is_terminal('PACKED'))

    def test_transition_package_valid(self):
        pkg = create_package(self.product, self.batch, 1.0)
        transition_package(pkg, 'FREEZING', actor='test_user')
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, 'FREEZING')

    def test_transition_creates_audit_event(self):
        pkg = create_package(self.product, self.batch, 1.0)
        transition_package(pkg, 'FREEZING', actor='test_user', reason='Testing')
        event = RotationEvent.objects.filter(package=pkg).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.from_state, 'PACKED')
        self.assertEqual(event.to_state, 'FREEZING')

    def test_transition_invalid_raises_error(self):
        pkg = create_package(self.product, self.batch, 1.0)
        with self.assertRaises(InvalidTransitionError):
            transition_package(pkg, 'FROZEN')

    def test_transition_to_thaw_queue_requires_plan(self):
        pkg = create_package(self.product, self.batch, 1.0)
        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')
        transition_package(pkg, 'READY_FOR_THAW', actor='test')
        with self.assertRaises(TransitionValidationError):
            transition_package(pkg, 'THAW_QUEUED', actor='test')

    def test_transition_to_thawing_requires_queue_entry(self):
        """Full happy path: PACKED → FROZEN → READY_FOR_THAW → THAW_QUEUED → THAWING"""
        pkg = create_package(self.product, self.batch, 1.0)
        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')

        # Create plan at FROZEN state (required)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile, actor='test')

        # Add to queue (transitions FROZEN → READY_FOR_THAW → THAW_QUEUED)
        add_to_thaw_queue(pkg, plan, actor='test')

        # Now THAWING should work
        transition_package(pkg, 'THAWING', actor='test')
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, 'THAWING')

    def test_full_lifecycle_happy_path(self):
        """Complete lifecycle from PACKED to COMPLETED."""
        pkg = create_package(self.product, self.batch, 1.0)

        # Pack → Freeze
        transition_package(pkg, 'FREEZING', actor='w1')
        transition_package(pkg, 'FROZEN', actor='w1')

        # Create plan at FROZEN
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile, actor='test')

        # Add to queue (FROZEN → READY_FOR_THAW → THAW_QUEUED)
        add_to_thaw_queue(pkg, plan, actor='w1')
        self.assertEqual(pkg.current_state, 'THAW_QUEUED')

        transition_package(pkg, 'THAWING', actor='w1')

        # Complete thaw
        entry = ThawQueueEntry.objects.filter(package=pkg).first()
        entry.status = QueueStatus.COMPLETED
        entry.save()
        transition_package(pkg, 'READY_FOR_SALE', actor='w1')

        # Display
        transition_package(pkg, 'ON_DISPLAY', actor='w1')

        # Process
        transition_package(pkg, 'PROCESSING', actor='w1')
        transition_package(pkg, 'COMPLETED', actor='w1')
        self.assertTrue(is_terminal(pkg.current_state))

    def test_refreeze_cycle(self):
        """ON_DISPLAY → REFREEZE_PENDING → FREEZING → FROZEN."""
        pkg = create_package(self.product, self.batch, 1.0)
        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')

        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile, actor='test')
        add_to_thaw_queue(pkg, plan, actor='test')
        transition_package(pkg, 'THAWING', actor='test')
        entry = ThawQueueEntry.objects.filter(package=pkg).first()
        entry.status = QueueStatus.COMPLETED
        entry.save()
        transition_package(pkg, 'READY_FOR_SALE', actor='test')
        transition_package(pkg, 'ON_DISPLAY', actor='test')

        # Refreeze cycle
        transition_package(pkg, 'REFREEZE_PENDING', actor='test')
        self.assertEqual(pkg.current_state, 'REFREEZE_PENDING')
        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')
        self.assertEqual(pkg.current_state, 'FROZEN')


# ============================================================
# STOCK MOVEMENT TESTS
# ============================================================

class StockMovementTest(InventoryBaseTestCase):

    def test_move_package(self):
        pkg = create_package(self.product, self.batch, 1.0, storage_location=self.freezer)
        move_package(pkg, self.thaw_area, actor='w1', reason='Moving to thaw')
        pkg.refresh_from_db()
        self.assertEqual(pkg.storage_location, self.thaw_area)
        self.assertEqual(pkg.movements.count(), 2)

    def test_adjust_price(self):
        pkg = create_package(self.product, self.batch, 1.0)
        old_price = pkg.selling_price
        adjust_package_price(pkg, Decimal('200'), mode='manual', actor='admin')
        pkg.refresh_from_db()
        self.assertEqual(pkg.selling_price, Decimal('200'))
        history = PriceChangeHistory.objects.filter(package=pkg).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_price, old_price)


# ============================================================
# FREEZE/THAW DURATION TESTS
# ============================================================

class FreezeDurationTest(InventoryBaseTestCase):

    def test_small_package(self):
        pkg = create_package(self.product, self.batch, 0.3)
        duration = calculate_freeze_duration(pkg, self.freeze_profile)
        self.assertEqual(duration, timedelta(hours=5))  # min(4h) + buffer(1h)

    def test_medium_package(self):
        pkg = create_package(self.product, self.batch, 0.8)
        duration = calculate_freeze_duration(pkg, self.freeze_profile)
        self.assertEqual(duration, timedelta(hours=9))  # default(8h) + buffer(1h)

    def test_large_package(self):
        pkg = create_package(self.product, self.batch, 2.0)
        duration = calculate_freeze_duration(pkg, self.freeze_profile)
        self.assertGreater(duration, timedelta(hours=10))


class ThawDurationTest(InventoryBaseTestCase):

    def test_small_package(self):
        pkg = create_package(self.product, self.batch, 0.3)
        duration = calculate_thaw_duration(pkg, self.thaw_profile)
        self.assertEqual(duration, timedelta(hours=14))  # min(12h) + buffer(2h)

    def test_medium_package(self):
        pkg = create_package(self.product, self.batch, 0.75)
        duration = calculate_thaw_duration(pkg, self.thaw_profile)
        self.assertGreater(duration, timedelta(hours=14))
        self.assertLess(duration, timedelta(hours=28))

    def test_large_package(self):
        pkg = create_package(self.product, self.batch, 1.5)
        duration = calculate_thaw_duration(pkg, self.thaw_profile)
        self.assertGreater(duration, timedelta(hours=30))


# ============================================================
# ROTATION PLAN TESTS
# ============================================================

class RotationPlanTest(InventoryBaseTestCase):

    def test_create_plan_auto_mode(self):
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        self.assertFalse(plan.is_override)
        self.assertEqual(plan.status, PlanStatus.PLANNED)
        self.assertGreater(plan.worker_tasks.count(), 0)

    def test_create_plan_custom_mode(self):
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        custom_freeze = timedelta(hours=12)
        plan = create_rotation_plan(
            pkg, future, self.freeze_profile, self.thaw_profile,
            freeze_override=custom_freeze, override_reason='Test', actor='admin'
        )
        self.assertTrue(plan.is_override)
        self.assertEqual(plan.freeze_override, custom_freeze)

    def test_plan_requires_frozen_or_packed(self):
        pkg = create_package(self.product, self.batch, 1.0)
        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        self.assertIsNotNone(plan)

    def test_plan_rejects_wrong_state(self):
        pkg = create_package(self.product, self.batch, 1.0)
        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')
        transition_package(pkg, 'READY_FOR_THAW', actor='test')
        future = timezone.now() + timedelta(days=3)
        with self.assertRaises(ValueError):
            create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)

    def test_cannot_duplicate_plan(self):
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        with self.assertRaises(ValueError):
            create_rotation_plan(pkg, future + timedelta(days=1), self.freeze_profile, self.thaw_profile)

    def test_worker_tasks_generated(self):
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        tasks = plan.worker_tasks.all()
        self.assertEqual(tasks.count(), 7)
        task_types = set(t.task_type for t in tasks)
        self.assertIn('FREEZE_START', task_types)
        self.assertIn('THAW_START', task_types)
        self.assertIn('MOVE_TO_DISPLAY', task_types)


# ============================================================
# THAW QUEUE TESTS
# ============================================================

class ThawQueueTest(InventoryBaseTestCase):

    def _prepare_package(self):
        """Create package at FROZEN state with a rotation plan."""
        pkg = create_package(self.product, self.batch, 1.0)
        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        return pkg, plan

    def test_add_to_queue(self):
        pkg, plan = self._prepare_package()
        entry = add_to_thaw_queue(pkg, plan, actor='w1')
        self.assertEqual(entry.queue_position, 1)
        self.assertEqual(entry.status, QueueStatus.QUEUED)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, 'THAW_QUEUED')

    def test_queue_position_increments(self):
        pkg1, plan1 = self._prepare_package()
        # Create second package
        pkg2 = create_package(self.product, self.batch, 0.5)
        transition_package(pkg2, 'FREEZING', actor='test')
        transition_package(pkg2, 'FROZEN', actor='test')
        future = timezone.now() + timedelta(days=3)
        plan2 = create_rotation_plan(pkg2, future, self.freeze_profile, self.thaw_profile)

        add_to_thaw_queue(pkg1, plan1, actor='w1')
        entry2 = add_to_thaw_queue(pkg2, plan2, actor='w1')
        self.assertEqual(entry2.queue_position, 2)

    def test_cannot_queue_twice(self):
        pkg, plan = self._prepare_package()
        add_to_thaw_queue(pkg, plan, actor='w1')
        with self.assertRaises(ValueError):
            add_to_thaw_queue(pkg, plan, actor='w1')

    def test_cannot_queue_non_frozen(self):
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        with self.assertRaises(ValueError):
            add_to_thaw_queue(pkg, plan, actor='w1')


# ============================================================
# PROFILE MATCHING TESTS
# ============================================================

class ProfileMatchingTest(InventoryBaseTestCase):

    def test_get_best_thaw_profile_category_specific(self):
        ThawProfile.objects.create(
            name='Pork Thaw', default_duration=timedelta(hours=20),
            minimum_duration=timedelta(hours=10), buffer_duration=timedelta(hours=1),
            category='PORK', active=True,
        )
        profile = get_best_thaw_profile(self.product)
        self.assertEqual(profile.name, 'Pork Thaw')

    def test_get_best_thaw_profile_fallback(self):
        profile = get_best_thaw_profile(self.product)
        self.assertEqual(profile, self.thaw_profile)

    def test_get_best_freeze_profile(self):
        profile = get_best_freeze_profile()
        self.assertEqual(profile, self.freeze_profile)


# ============================================================
# WORKER TASK COMPLETION TESTS
# ============================================================

class WorkerTaskCompletionTest(InventoryBaseTestCase):

    def test_complete_freeze_start(self):
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        freeze_task = plan.worker_tasks.filter(task_type='FREEZE_START').first()
        result = complete_task(freeze_task, actor=self.user)
        self.assertEqual(freeze_task.status, TaskStatus.COMPLETED)
        self.assertEqual(len(result['transitions']), 1)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, 'FREEZING')

    def test_complete_already_completed_is_idempotent(self):
        """Completing an already-completed task is idempotent (no error, no re-execution)."""
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        freeze_task = plan.worker_tasks.filter(task_type='FREEZE_START').first()
        result1 = complete_task(freeze_task, actor=self.user)
        # Second complete should be idempotent — no error, no re-execution
        result2 = complete_task(freeze_task, actor=self.user)
        self.assertEqual(result2['task'].status, TaskStatus.COMPLETED)
        self.assertEqual(result2['transitions'], [])

    def test_complete_with_string_actor(self):
        """complete_task should handle string actor (for backward compat)."""
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        freeze_task = plan.worker_tasks.filter(task_type='FREEZE_START').first()
        result = complete_task(freeze_task, actor='system')
        self.assertEqual(freeze_task.status, TaskStatus.COMPLETED)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, 'FREEZING')


# ============================================================
# QUERY SERVICE TESTS
# ============================================================

class QueryServiceTest(InventoryBaseTestCase):

    def test_get_by_state(self):
        create_package(self.product, self.batch, 1.0)
        self.assertEqual(get_packages_by_state(PackageState.PACKED).count(), 1)

    def test_get_available_for_planning(self):
        pkg = create_package(self.product, self.batch, 1.0)
        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')
        self.assertEqual(get_available_for_planning().count(), 1)

    def test_get_by_barcode(self):
        pkg = create_package(self.product, self.batch, 1.0)
        self.assertEqual(get_package_by_barcode(pkg.barcode), pkg)

    def test_get_by_barcode_not_found(self):
        self.assertIsNone(get_package_by_barcode('NONEXISTENT'))


# ============================================================
# LABEL SERVICE TESTS
# ============================================================

class LabelServiceTest(InventoryBaseTestCase):

    def test_get_label_data(self):
        from inventory.label_service import get_label_data
        pkg = create_package(self.product, self.batch, 1.5)
        data = get_label_data(pkg)
        self.assertEqual(data['product_name'], 'Pork Neck')
        self.assertEqual(data['barcode'], pkg.barcode)
        self.assertEqual(data['weight_kg'], 1.5)
        self.assertEqual(data['category_emoji'], '🐷')

    def test_get_niimbot_label_data(self):
        from inventory.label_service import get_niimbot_label_data
        pkg = create_package(self.product, self.batch, 0.5)
        data = get_niimbot_label_data(pkg)
        self.assertEqual(data['product'], 'Pork Neck')
        self.assertEqual(data['weight'], '0.500')


# ============================================================
# AUTOMATION PRINCIPLE TESTS
# ============================================================

class AutomationTest(InventoryBaseTestCase):

    def test_auto_freeze_scales_with_weight(self):
        small = create_package(self.product, self.batch, 0.3)
        large = create_package(self.product, self.batch, 2.0)
        self.assertGreater(
            calculate_freeze_duration(large, self.freeze_profile),
            calculate_freeze_duration(small, self.freeze_profile),
        )

    def test_auto_thaw_scales_with_weight(self):
        small = create_package(self.product, self.batch, 0.3)
        large = create_package(self.product, self.batch, 2.0)
        self.assertGreater(
            calculate_thaw_duration(large, self.thaw_profile),
            calculate_thaw_duration(small, self.thaw_profile),
        )

    def test_plan_calculates_all_times_automatically(self):
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)

        self.assertIsNotNone(plan.planned_freeze_start_at)
        self.assertIsNotNone(plan.planned_freeze_end_at)
        self.assertIsNotNone(plan.planned_thaw_start_at)
        self.assertIsNotNone(plan.planned_thaw_queue_at)

        # Timeline order
        self.assertLess(plan.planned_freeze_start_at, plan.planned_freeze_end_at)
        self.assertLess(plan.planned_freeze_end_at, plan.planned_thaw_start_at)
        self.assertLess(plan.planned_thaw_queue_at, plan.planned_thaw_start_at)
        self.assertLess(plan.planned_thaw_start_at, plan.target_ready_at)


# ============================================================
# INTEGRATION TESTS
# ============================================================

class TaskManagerIntegrationTest(InventoryBaseTestCase):

    def test_rotation_plan_creates_worker_tasks(self):
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        tasks = WorkerTask.objects.filter(rotation_plan=plan)
        self.assertEqual(tasks.count(), 7)
        for task in tasks:
            self.assertIsNotNone(task.scheduled_at)
            self.assertEqual(task.status, TaskStatus.PENDING)

    def test_task_completion_triggers_state_transition(self):
        pkg = create_package(self.product, self.batch, 1.0)
        future = timezone.now() + timedelta(days=3)
        plan = create_rotation_plan(pkg, future, self.freeze_profile, self.thaw_profile)
        freeze_task = plan.worker_tasks.filter(task_type='FREEZE_START').first()
        complete_task(freeze_task, actor=self.user)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, 'FREEZING')
        event = RotationEvent.objects.filter(package=pkg).last()
        self.assertEqual(event.to_state, 'FREEZING')
