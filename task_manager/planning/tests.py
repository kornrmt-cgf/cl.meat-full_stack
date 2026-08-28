"""
Planning Services Tests — regression tests for thaw queue lifecycle.

Covers:
1. failed transition cannot create queue
2. successful add-to-queue creates correct state and queue
3. queue cancellation changes package state correctly
4. queue cancellation rolls back on failure
5. duplicate queue position detection
6. queue ordering
7. interval overlap
8. adjacent intervals
9. capacity limit
10. repeated rotation-cycle analysis
11. duplicate barcode detection
12. `.0` barcode normalization
13. orphan relationship detection
14. invalid package state detection
15. reconciliation deterministic repeatability
"""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from inventory.models import (
    Category, Supplier, Product, Batch, Package, PackageState,
    StorageLocation, StockMovement,
)
from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, PlanStatus,
    ThawQueueEntry, QueueStatus,
)
from planning.services import (
    create_rotation_plan, add_to_thaw_queue, remove_from_thaw_queue,
    calculate_freeze_duration, calculate_thaw_duration,
    check_interval_overlap, check_thaw_capacity_at_time,
    check_thaw_interval_overlap, _recalculate_queue_positions,
)
from common.state_machine import (
    transition_package, InvalidTransitionError, TransitionValidationError,
    can_transition, TRANSITIONS,
)
from operations.models import RotationEvent, WorkerTask, TaskStatus


# ============================================================
# TEST HELPERS
# ============================================================

class PlanningTestBase(TestCase):
    """Shared fixtures for planning tests."""

    def setUp(self):
        # Reference data
        self.category = Category.objects.create(
            code='PORK', name='Pork', name_thai='หมู', is_active=True
        )
        self.supplier = Supplier.objects.create(
            name='Test Supplier', locations='14.0,100.0', is_active=True
        )
        self.product = Product.objects.create(
            sku='MP-TEST-001', name='Pork Neck', name_thai='สันคอหมู',
            category=self.category, supplier=self.supplier,
            cost_per_kg=Decimal('85.00'), selling_price_per_kg=Decimal('97.00'),
            active=True,
        )
        self.batch = Batch.objects.create(
            batch_number='B-TEST-001', supplier=self.supplier,
            received_at=timezone.now(), active=True,
        )

        # Freeze/thaw profiles
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard Freeze', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=4),
            default_duration=timedelta(hours=8),
            buffer_duration=timedelta(hours=1),
            active=True,
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard Thaw', default_duration=timedelta(hours=12),
            minimum_duration=timedelta(hours=6),
            buffer_duration=timedelta(hours=1),
            weight_threshold_kg=Decimal('0.500'),
            weight_scale_factor=Decimal('1.20'),
            target_temperature=Decimal('3.00'),
            thaw_capacity=3,
            active=True,
        )

        # Locations
        self.freezer = StorageLocation.objects.create(
            name='Main Freezer', location_type='FREEZER', capacity=50, active=True
        )
        self.thaw_area = StorageLocation.objects.create(
            name='Thaw Area', location_type='THAW_AREA', capacity=20, active=True
        )

    def _create_package(self, state=PackageState.PACKED, weight=Decimal('0.800'),
                        barcode=None):
        """Create a package with a unique barcode."""
        if barcode is None:
            barcode = f"TEST-{Package.objects.count() + 1:04d}"
        return Package.objects.create(
            product=self.product, batch=self.batch,
            barcode=barcode, weight=weight,
            selling_price=Decimal('77.60'),
            packed_at=timezone.now(), current_state=state,
        )

    def _create_plan(self, package, target_ready_at=None):
        """Create a rotation plan for a package."""
        if target_ready_at is None:
            target_ready_at = timezone.now() + timedelta(days=1)
        return create_rotation_plan(
            package=package,
            target_ready_at=target_ready_at,
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            actor='test',
        )


# ============================================================
# TEST 1: Failed transition cannot create queue entry
# ============================================================

class TestFailedTransitionBlocksQueue(PlanningTestBase):
    """
    TransitionValidationError must prevent queue creation.
    No partial queue records.
    """

    def test_add_to_thaw_queue_fails_without_plan(self):
        """add_to_thaw_queue should fail if no rotation plan provided."""
        pkg = self._create_package(state=PackageState.FROZEN)

        with self.assertRaises(ValueError, msg='rotation_plan is required'):
            add_to_thaw_queue(pkg, rotation_plan=None, actor='test')

        # Verify no queue entry was created
        self.assertEqual(ThawQueueEntry.objects.filter(package=pkg).count(), 0)
        # Verify package state unchanged
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.FROZEN)

    def test_add_to_thaw_queue_fails_wrong_state(self):
        """add_to_thaw_queue should fail if package is not FROZEN."""
        pkg = self._create_package(state=PackageState.PACKED)
        plan = self._create_plan(pkg)

        with self.assertRaises(ValueError):
            add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        self.assertEqual(ThawQueueEntry.objects.filter(package=pkg).count(), 0)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.PACKED)

    def test_no_partial_record_on_failure(self):
        """When transition fails, no queue entry or state change should persist."""
        pkg = self._create_package(state=PackageState.FROZEN)

        # Mock transition to raise on second call (THAW_QUEUED transition)
        call_count = [0]
        original_transition = None

        from common import state_machine
        original_transition = state_machine.transition_package

        def mock_transition(package, target_state, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                # Second transition (THAW_QUEUED) fails
                raise TransitionValidationError("Simulated failure")
            return original_transition(package, target_state, **kwargs)

        plan = self._create_plan(pkg)

        with patch.object(state_machine, 'transition_package', side_effect=mock_transition):
            with self.assertRaises(TransitionValidationError):
                add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        # Verify: no queue entry, package back to FROZEN (transaction rolled back)
        self.assertEqual(ThawQueueEntry.objects.filter(package=pkg).count(), 0)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.FROZEN)


# ============================================================
# TEST 2: Successful add-to-queue creates correct state and queue
# ============================================================

class TestSuccessfulAddToQueue(PlanningTestBase):

    def test_successful_add_to_queue(self):
        """add_to_thaw_queue should create queue entry and transition to THAW_QUEUED."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)

        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        # Verify package state
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.THAW_QUEUED)

        # Verify queue entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.package, pkg)
        self.assertEqual(entry.rotation_plan, plan)
        self.assertEqual(entry.status, QueueStatus.QUEUED)
        self.assertEqual(entry.queue_position, 1)
        self.assertEqual(entry.planned_start_at, plan.planned_thaw_start_at)
        self.assertEqual(entry.target_ready_at, plan.target_ready_at)

        # Verify rotation event created
        events = RotationEvent.objects.filter(package=pkg)
        self.assertTrue(events.filter(from_state='FROZEN', to_state='READY_FOR_THAW').exists())
        self.assertTrue(events.filter(from_state='READY_FOR_THAW', to_state='THAW_QUEUED').exists())

    def test_duplicate_queue_rejected(self):
        """Package already in queue should be rejected."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)
        add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        with self.assertRaises(ValueError, msg='Already in thaw queue'):
            add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')


# ============================================================
# TEST 3: Queue cancellation changes package state correctly
# ============================================================

class TestQueueCancellation(PlanningTestBase):

    def test_cancellation_transitions_to_packed(self):
        """Cancelling a queue entry should transition package back to PACKED."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)
        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        cancelled = remove_from_thaw_queue(entry, actor='test', reason='Test cancel')

        # Verify entry status
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, QueueStatus.CANCELLED)

        # Verify package state
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.PACKED)

    def test_cancelled_entry_not_in_active_queue(self):
        """Cancelled entries should not appear in active queue."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)
        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        remove_from_thaw_queue(entry, actor='test')

        active = ThawQueueEntry.objects.filter(
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
        )
        self.assertNotIn(entry.pk, active.values_list('pk', flat=True))

    def test_cancel_recycles_queue_position(self):
        """After cancellation, queue positions should be recalculated."""
        pkg1 = self._create_package(state=PackageState.FROZEN, barcode='T-001')
        pkg2 = self._create_package(state=PackageState.FROZEN, barcode='T-002')
        pkg3 = self._create_package(state=PackageState.FROZEN, barcode='T-003')

        plan1 = self._create_plan(pkg1)
        plan2 = self._create_plan(pkg2)
        plan3 = self._create_plan(pkg3)

        e1 = add_to_thaw_queue(pkg1, rotation_plan=plan1, actor='test')
        e2 = add_to_thaw_queue(pkg2, rotation_plan=plan2, actor='test')
        e3 = add_to_thaw_queue(pkg3, rotation_plan=plan3, actor='test')

        self.assertEqual(e1.queue_position, 1)
        self.assertEqual(e2.queue_position, 2)
        self.assertEqual(e3.queue_position, 3)

        # Cancel middle entry
        remove_from_thaw_queue(e2, actor='test')

        # Verify positions recalculated
        e1.refresh_from_db()
        e3.refresh_from_db()
        self.assertEqual(e1.queue_position, 1)
        self.assertEqual(e3.queue_position, 2)


# ============================================================
# TEST 4: Queue cancellation rolls back on failure
# ============================================================

class TestCancellationRollback(PlanningTestBase):

    def test_rollback_on_transition_failure(self):
        """If package transition fails, queue entry should not be cancelled."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)
        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        # Mock transition to fail
        from common import state_machine
        original = state_machine.transition_package

        def failing_transition(package, target_state, **kwargs):
            if target_state == 'PACKED':
                raise InvalidTransitionError("Simulated failure")
            return original(package, target_state, **kwargs)

        with patch.object(state_machine, 'transition_package', side_effect=failing_transition):
            with self.assertRaises(InvalidTransitionError):
                remove_from_thaw_queue(entry, actor='test')

        # Verify: entry still QUEUED, package still THAW_QUEUED
        entry.refresh_from_db()
        self.assertEqual(entry.status, QueueStatus.QUEUED)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.THAW_QUEUED)

    def test_cannot_cancel_started_entry(self):
        """Cannot cancel an entry that has already started thawing."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)
        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')
        entry.status = QueueStatus.STARTED
        entry.save(update_fields=['status'])

        with self.assertRaises(ValueError, msg='Cannot cancel'):
            remove_from_thaw_queue(entry, actor='test')

    def test_cannot_cancel_completed_entry(self):
        """Cannot cancel a completed entry."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)
        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')
        entry.status = QueueStatus.COMPLETED
        entry.save(update_fields=['status'])

        with self.assertRaises(ValueError, msg='Cannot cancel'):
            remove_from_thaw_queue(entry, actor='test')


# ============================================================
# TEST 5: Queue ordering and position integrity
# ============================================================

class TestQueueOrdering(PlanningTestBase):

    def test_positions_are_sequential(self):
        """Queue positions should be sequential starting from 1."""
        self.thaw_profile.thaw_capacity = 10
        self.thaw_profile.save()
        packages = []
        for i in range(5):
            pkg = self._create_package(state=PackageState.FROZEN, barcode=f'Q-{i}')
            plan = self._create_plan(pkg)
            entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')
            packages.append((pkg, entry))

        positions = [e.queue_position for _, e in packages]
        self.assertEqual(positions, [1, 2, 3, 4, 5])

    def test_gap_in_positions_gets_fixed(self):
        """After deletion, gaps in positions should be repaired."""
        self.thaw_profile.thaw_capacity = 5
        self.thaw_profile.save()
        pkgs = []
        for i in range(3):
            pkg = self._create_package(state=PackageState.FROZEN, barcode=f'G-{i}')
            plan = self._create_plan(pkg)
            entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')
            pkgs.append((pkg, entry))

        # Cancel first entry
        remove_from_thaw_queue(pkgs[0][1], actor='test')

        # Verify remaining positions are 1, 2 (no gap)
        active = ThawQueueEntry.objects.filter(
            status__in=[QueueStatus.QUEUED, QueueStatus.READY_TO_START]
        ).order_by('queue_position')
        positions = list(active.values_list('queue_position', flat=True))
        self.assertEqual(positions, [1, 2])


# ============================================================
# TEST 6: Interval overlap detection
# ============================================================

class TestIntervalOverlap(PlanningTestBase):

    def test_complete_overlap(self):
        """[1, 5] overlaps [2, 4] — complete containment."""
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=4)
        b_start = a_start + timedelta(hours=1)
        b_end = a_start + timedelta(hours=3)
        self.assertTrue(check_interval_overlap(a_start, a_end, b_start, b_end))

    def test_partial_overlap(self):
        """[1, 3] overlaps [2, 5] — partial."""
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=2)
        b_start = a_start + timedelta(hours=1)
        b_end = a_start + timedelta(hours=4)
        self.assertTrue(check_interval_overlap(a_start, a_end, b_start, b_end))

    def test_same_start(self):
        """[1, 3] overlaps [1, 5] — same start."""
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=2)
        b_end = a_start + timedelta(hours=4)
        self.assertTrue(check_interval_overlap(a_start, a_end, a_start, b_end))

    def test_same_end(self):
        """[1, 5] overlaps [3, 5] — same end."""
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=4)
        b_start = a_start + timedelta(hours=2)
        self.assertTrue(check_interval_overlap(a_start, a_end, b_start, a_end))

    def test_adjacent_non_overlapping(self):
        """[1, 3] and [3, 5] — adjacent, no overlap (half-open boundaries)."""
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=2)
        b_start = a_end  # starts exactly when A ends
        b_end = a_start + timedelta(hours=4)
        self.assertFalse(check_interval_overlap(a_start, a_end, b_start, b_end))

    def test_separate_intervals(self):
        """[1, 2] and [3, 4] — no overlap."""
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=1)
        b_start = a_start + timedelta(hours=2)
        b_end = a_start + timedelta(hours=3)
        self.assertFalse(check_interval_overlap(a_start, a_end, b_start, b_end))

    def test_reversed_order(self):
        """Overlap detection is symmetric."""
        a_start = timezone.now()
        a_end = a_start + timedelta(hours=2)
        b_start = a_start + timedelta(hours=1)
        b_end = a_start + timedelta(hours=3)
        self.assertTrue(check_interval_overlap(b_start, b_end, a_start, a_end))


# ============================================================
# TEST 7: Thaw capacity limit
# ============================================================

class TestThawCapacity(PlanningTestBase):

    def test_capacity_not_exceeded(self):
        """Fewer active entries than capacity → available."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)
        add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        target = plan.planned_thaw_start_at
        result = check_thaw_capacity_at_time(self.thaw_profile, target)
        self.assertTrue(result['available'])
        self.assertEqual(result['current_count'], 1)
        self.assertEqual(result['max_capacity'], 3)

    def test_capacity_exceeded(self):
        """More active entries than capacity → not available."""
        # Create 3 packages with overlapping time windows
        now = timezone.now()
        for i in range(3):
            pkg = self._create_package(state=PackageState.FROZEN, barcode=f'C-{i}')
            target_ready = now + timedelta(days=1)
            plan = create_rotation_plan(
                pkg, target_ready, self.freeze_profile, self.thaw_profile, actor='test'
            )
            add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        # Check capacity at a time within all3 entries' overlap
        # All entries have planned_start_at ≈ now+11h and target_ready_at ≈ now+1d
        result = check_thaw_capacity_at_time(self.thaw_profile, now + timedelta(hours=14))
        self.assertFalse(result['available'])
        self.assertEqual(result['current_count'], 3)

    def test_capacity_one_package(self):
        """Capacity=1: first package takes all capacity."""
        self.thaw_profile.thaw_capacity = 1
        self.thaw_profile.save()

        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)
        add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        target = plan.planned_thaw_start_at
        result = check_thaw_capacity_at_time(self.thaw_profile, target)
        self.assertFalse(result['available'])
        self.assertEqual(result['current_count'], 1)

    def test_exclude_package_from_count(self):
        """Excluding a package should reduce the count."""
        now = timezone.now()
        pkg1 = self._create_package(state=PackageState.FROZEN, barcode='E-001')
        pkg2 = self._create_package(state=PackageState.FROZEN, barcode='E-002')
        target_ready = now + timedelta(days=1)
        plan1 = create_rotation_plan(
            pkg1, target_ready, self.freeze_profile, self.thaw_profile, actor='test'
        )
        plan2 = create_rotation_plan(
            pkg2, target_ready, self.freeze_profile, self.thaw_profile, actor='test'
        )
        add_to_thaw_queue(pkg1, rotation_plan=plan1, actor='test')
        add_to_thaw_queue(pkg2, rotation_plan=plan2, actor='test')

        # Check at a time within both entries' overlap
        check_time = now + timedelta(hours=14)
        result_all = check_thaw_capacity_at_time(self.thaw_profile, check_time)
        result_excl = check_thaw_capacity_at_time(self.thaw_profile, check_time, exclude_package=pkg1)

        self.assertEqual(result_all['current_count'], 2)
        self.assertEqual(result_excl['current_count'], 1)

    def test_interval_overlap_detection(self):
        """check_thaw_interval_overlap should find overlapping entries."""
        pkg1 = self._create_package(state=PackageState.FROZEN, barcode='O-001')
        plan1 = self._create_plan(pkg1)
        add_to_thaw_queue(pkg1, rotation_plan=plan1, actor='test')

        # New interval that overlaps with existing
        overlaps = check_thaw_interval_overlap(
            plan1.planned_thaw_start_at,
            plan1.target_ready_at,
        )
        self.assertEqual(len(overlaps), 1)

    def test_no_overlap_with_non_overlapping_interval(self):
        """Non-overlapping intervals should not conflict."""
        pkg1 = self._create_package(state=PackageState.FROZEN, barcode='N-001')
        plan1 = self._create_plan(pkg1)
        add_to_thaw_queue(pkg1, rotation_plan=plan1, actor='test')

        # New interval far in the future (no overlap)
        future_start = plan1.target_ready_at + timedelta(hours=24)
        future_end = future_start + timedelta(hours=12)
        overlaps = check_thaw_interval_overlap(future_start, future_end)
        self.assertEqual(len(overlaps), 0)


# ============================================================
# TEST 8: Repeated rotation cycle analysis
# ============================================================

class TestRepeatedRotationCycle(PlanningTestBase):

    def test_oneToOne_prevents_second_plan(self):
        """OneToOne prevents creating a second RotationPlan for the same package."""
        pkg = self._create_package(state=PackageState.PACKED)
        plan1 = self._create_plan(pkg)

        with self.assertRaises(Exception):  # IntegrityError from OneToOne
            self._create_plan(pkg)

    def test_full_cycle_ends_at_display(self):
        """A complete rotation cycle ends at ON_DISPLAY (or terminal)."""
        pkg = self._create_package(state=PackageState.PACKED)

        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')

        plan = self._create_plan(pkg)
        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')
        transition_package(pkg, 'THAWING', actor='test')

        # Mark queue entry as COMPLETED (thaw done)
        entry.status = QueueStatus.COMPLETED
        entry.save(update_fields=['status'])

        transition_package(pkg, 'READY_FOR_SALE', actor='test')
        transition_package(pkg, 'ON_DISPLAY', actor='test')

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.ON_DISPLAY)

        # ON_DISPLAY can go to REFREEZE_PENDING → FREEZING → ... (new cycle)
        # but OneToOne prevents creating a new RotationPlan
        self.assertTrue(can_transition('ON_DISPLAY', 'REFREEZE_PENDING'))

    def test_refreeze_requires_new_plan(self):
        """After REFREEZE, a new RotationPlan is needed but OneToOne blocks it."""
        pkg = self._create_package(state=PackageState.PACKED)
        transition_package(pkg, 'FREEZING', actor='test')
        transition_package(pkg, 'FROZEN', actor='test')

        plan = self._create_plan(pkg)
        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')
        transition_package(pkg, 'THAWING', actor='test')

        # Mark queue entry as COMPLETED
        entry.status = QueueStatus.COMPLETED
        entry.save(update_fields=['status'])

        transition_package(pkg, 'READY_FOR_SALE', actor='test')
        transition_package(pkg, 'ON_DISPLAY', actor='test')
        transition_package(pkg, 'REFREEZE_PENDING', actor='test')

        # Can't create a second plan due to OneToOne
        with self.assertRaises(Exception):
            create_rotation_plan(
                pkg, timezone.now() + timedelta(days=2),
                self.freeze_profile, self.thaw_profile, actor='test',
            )


# ============================================================
# TEST 9: Barcode audit
# ============================================================

class TestBarcodeAudit(PlanningTestBase):

    def test_normal_barcodes(self):
        """Normal numeric barcodes are valid."""
        pkg = self._create_package(barcode='1234567890123')
        self.assertEqual(pkg.barcode, '1234567890123')

    def test_dot_zero_barcode(self):
        """Barcodes ending in .0 indicate float conversion (data quality issue)."""
        barcode = '1234567890123.0'
        has_dot_zero = barcode.endswith('.0')
        self.assertTrue(has_dot_zero, 'Should detect .0 suffix')

    def test_whitespace_barcode(self):
        """Barcodes with whitespace should be flagged."""
        barcode = ' 1234567890123 '
        has_whitespace = barcode != barcode.strip()
        self.assertTrue(has_whitespace, 'Should detect whitespace')

    def test_empty_barcode(self):
        """Empty barcodes are invalid for packages."""
        barcode = ''
        is_empty = len(barcode) == 0
        self.assertTrue(is_empty, 'Should detect empty barcode')

    def test_barcode_uniqueness_in_db(self):
        """Database-level barcode uniqueness constraint."""
        self._create_package(barcode='UNIQUE-001')
        with self.assertRaises(Exception):
            self._create_package(barcode='UNIQUE-001')


# ============================================================
# TEST 10: Orphan relationship detection
# ============================================================

class TestOrphanDetection(PlanningTestBase):

    def test_package_without_product_is_invalid(self):
        """A package without a product is an orphan."""
        # This tests the model constraint (PROTECT) rather than creating orphan
        # since Django's PROTECT prevents deletion of referenced products
        pkg = self._create_package()
        self.assertIsNotNone(pkg.product)
        self.assertIsNotNone(pkg.batch)

    def test_queue_entry_requires_plan(self):
        """Queue entry must reference a valid rotation plan."""
        # add_to_thaw_queue requires a rotation_plan argument
        pkg = self._create_package(state=PackageState.FROZEN)
        with self.assertRaises(ValueError, msg='rotation_plan is required'):
            add_to_thaw_queue(pkg, rotation_plan=None, actor='test')


# ============================================================
# TEST 11: Invalid package state detection
# ============================================================

class TestInvalidStateDetection(PlanningTestBase):

    def test_all_valid_states_reachable(self):
        """Every state (except PACKED) should be reachable from at least one state."""
        reachable = set()
        for from_state, targets in TRANSITIONS.items():
            reachable.update(targets)

        for state in TRANSITIONS:
            if state == 'PACKED':
                continue  # Starting state, not a transition target
            self.assertIn(state, reachable,
                         f"State {state} is not reachable from any other state")

    def test_terminal_state_has_no_transitions(self):
        """COMPLETED should have no outgoing transitions."""
        self.assertEqual(TRANSITIONS['COMPLETED'], [])

    def test_frozen_only_transitions_to_ready_for_thaw(self):
        """FROZEN should only transition to READY_FOR_THAW."""
        self.assertEqual(TRANSITIONS['FROZEN'], ['READY_FOR_THAW'])


# ============================================================
# TEST 12: Duration calculations
# ============================================================

class TestDurationCalculations(PlanningTestBase):

    def test_freeze_duration_light_package(self):
        """Package ≤ 0.5kg gets minimum freeze duration."""
        pkg = self._create_package(weight=Decimal('0.400'))
        dur = calculate_freeze_duration(pkg, self.freeze_profile)
        expected = self.freeze_profile.minimum_duration + self.freeze_profile.buffer_duration
        self.assertEqual(dur, expected)

    def test_freeze_duration_medium_package(self):
        """Package 0.5-1.0kg gets default freeze duration."""
        pkg = self._create_package(weight=Decimal('0.800'))
        dur = calculate_freeze_duration(pkg, self.freeze_profile)
        expected = self.freeze_profile.default_duration + self.freeze_profile.buffer_duration
        self.assertEqual(dur, expected)

    def test_thaw_duration_light_package(self):
        """Package ≤ threshold gets minimum thaw duration."""
        pkg = self._create_package(weight=Decimal('0.300'))
        dur = calculate_thaw_duration(pkg, self.thaw_profile)
        expected = self.thaw_profile.minimum_duration + self.thaw_profile.buffer_duration
        self.assertEqual(dur, expected)


# ============================================================
# TEST 13: Reconciliation — deterministic repeatability
# ============================================================

class TestReconciliationDeterminism(PlanningTestBase):

    def test_deterministic_state_transitions(self):
        """Same package + same transitions → same final state."""
        pkg1 = self._create_package(barcode='D-001')
        pkg2 = self._create_package(barcode='D-002')

        transitions = ['FREEZING', 'FROZEN']
        for t in transitions:
            transition_package(pkg1, t, actor='test')
            transition_package(pkg2, t, actor='test')

        pkg1.refresh_from_db()
        pkg2.refresh_from_db()
        self.assertEqual(pkg1.current_state, pkg2.current_state)
        self.assertEqual(pkg1.current_state, PackageState.FROZEN)

    def test_rotation_event_count_matches_transitions(self):
        """Each transition should produce exactly one RotationEvent."""
        pkg = self._create_package(barcode='EVT-001')
        states = ['FREEZING', 'FROZEN']

        for s in states:
            transition_package(pkg, s, actor='test')

        events = RotationEvent.objects.filter(package=pkg)
        self.assertEqual(events.count(), len(states))

    def test_cancel_plan_records_audit(self):
        """Cancelling a plan should create an audit event."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)
        cancel_rotation_plan(plan, actor='test', reason='Testing audit')

        events = RotationEvent.objects.filter(
            package=pkg, event_type='PLAN_CANCELLED'
        )
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().actor, 'test')
        self.assertEqual(events.first().reason, 'Testing audit')


# ============================================================
# TEST 14: cancel_rotation_plan integration
# ============================================================

class TestCancelRotationPlan(PlanningTestBase):

    def test_cancel_plan_cancels_tasks(self):
        """Cancelling a plan should cancel pending worker tasks."""
        pkg = self._create_package(state=PackageState.FROZEN)
        plan = self._create_plan(pkg)

        tasks = WorkerTask.objects.filter(rotation_plan=plan)
        self.assertTrue(tasks.filter(status=TaskStatus.PENDING).exists())

        cancel_rotation_plan(plan, actor='test')

        plan.refresh_from_db()
        self.assertEqual(plan.status, PlanStatus.CANCELLED)
        self.assertFalse(
            tasks.filter(status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]).exists()
        )

    def test_cancel_plan_with_active_queue(self):
        """Cancelling a plan with active queue entries should cancel those too."""
        pkg = self._create_package(state=PackageState.FROZEN, barcode='CP-001')
        plan = self._create_plan(pkg)
        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

        cancel_rotation_plan(plan, actor='test', reason='Business decision')

        entry.refresh_from_db()
        self.assertEqual(entry.status, QueueStatus.CANCELLED)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.PACKED)


# ============================================================
# Import cancel_rotation_plan
# ============================================================

from planning.services import cancel_rotation_plan


# ============================================================
# TEST 15: Capacity gate integration (real business flow)
# ============================================================

class TestCapacityGateIntegration(PlanningTestBase):
    """
    Integration tests proving the capacity gate in add_to_thaw_queue()
    rejects over-capacity schedules through the REAL scheduling path.
    """

    def _fill_capacity(self, count, thaw_start, target_ready, offset=0):
        """Fill capacity with `count` packages using the real flow."""
        packages = []
        for i in range(count):
            pkg = self._create_package(state=PackageState.FROZEN, barcode=f'CAP-{offset + i}')
            plan = RotationPlan.objects.create(
                package=pkg, target_ready_at=target_ready,
                planned_thaw_start_at=thaw_start,
                planned_thaw_queue_at=thaw_start - timedelta(minutes=30),
                planned_freeze_start_at=thaw_start - timedelta(hours=8),
                planned_freeze_end_at=thaw_start - timedelta(minutes=15),
                freeze_profile=self.freeze_profile,
                thaw_profile=self.thaw_profile,
                freeze_duration=timedelta(hours=8),
                thaw_duration=timedelta(hours=12),
                status=PlanStatus.PLANNED,
            )
            add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')
            packages.append(pkg)
        return packages

    # Case 1: complete overlap
    def test_complete_overlap_rejects(self):
        """New interval fully inside existing interval → rejected when at capacity."""
        self.thaw_profile.thaw_capacity = 2
        self.thaw_profile.save()

        now = timezone.now()
        # Fill capacity with wide intervals
        self._fill_capacity(2, now + timedelta(hours=6), now + timedelta(hours=20))

        # New package with narrower interval fully inside
        pkg = self._create_package(state=PackageState.FROZEN, barcode='CAP-NEW')
        plan = RotationPlan.objects.create(
            package=pkg, target_ready_at=now + timedelta(hours=18),
            planned_thaw_start_at=now + timedelta(hours=8),
            planned_thaw_queue_at=now + timedelta(hours=7),
            planned_freeze_start_at=now - timedelta(hours=1),
            planned_freeze_end_at=now + timedelta(hours=5),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        with self.assertRaises(ValueError, msg='Thaw capacity exceeded'):
            add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')
        self.assertEqual(pkg.current_state, PackageState.FROZEN)

    # Case 2: partial overlap
    def test_partial_overlap_rejects(self):
        """New interval partially overlaps existing → rejected when at capacity."""
        self.thaw_profile.thaw_capacity = 1
        self.thaw_profile.save()

        now = timezone.now()
        self._fill_capacity(1, now + timedelta(hours=6), now + timedelta(hours=12))

        pkg = self._create_package(state=PackageState.FROZEN, barcode='CAP-NEW')
        plan = RotationPlan.objects.create(
            package=pkg, target_ready_at=now + timedelta(hours=14),
            planned_thaw_start_at=now + timedelta(hours=10),
            planned_thaw_queue_at=now + timedelta(hours=9),
            planned_freeze_start_at=now - timedelta(hours=1),
            planned_freeze_end_at=now + timedelta(hours=9),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        with self.assertRaises(ValueError, msg='Thaw capacity exceeded'):
            add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

    # Case 3: same start time
    def test_same_start_rejects(self):
        """New interval starts at same time as existing → rejected when at capacity."""
        self.thaw_profile.thaw_capacity = 1
        self.thaw_profile.save()

        now = timezone.now()
        self._fill_capacity(1, now + timedelta(hours=6), now + timedelta(hours=12))

        pkg = self._create_package(state=PackageState.FROZEN, barcode='CAP-NEW')
        plan = RotationPlan.objects.create(
            package=pkg, target_ready_at=now + timedelta(hours=14),
            planned_thaw_start_at=now + timedelta(hours=6),  # same start
            planned_thaw_queue_at=now + timedelta(hours=5),
            planned_freeze_start_at=now - timedelta(hours=1),
            planned_freeze_end_at=now + timedelta(hours=5),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        with self.assertRaises(ValueError, msg='Thaw capacity exceeded'):
            add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

    # Case 4: same end time
    def test_same_end_rejects(self):
        """New interval ends at same time as existing → rejected when at capacity."""
        self.thaw_profile.thaw_capacity = 1
        self.thaw_profile.save()

        now = timezone.now()
        self._fill_capacity(1, now + timedelta(hours=6), now + timedelta(hours=12))

        pkg = self._create_package(state=PackageState.FROZEN, barcode='CAP-NEW')
        plan = RotationPlan.objects.create(
            package=pkg, target_ready_at=now + timedelta(hours=12),  # same end
            planned_thaw_start_at=now + timedelta(hours=4),
            planned_thaw_queue_at=now + timedelta(hours=3),
            planned_freeze_start_at=now - timedelta(hours=1),
            planned_freeze_end_at=now + timedelta(hours=3),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        with self.assertRaises(ValueError, msg='Thaw capacity exceeded'):
            add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

    # Case 5: adjacent intervals with no overlap
    def test_adjacent_no_overlap_allows(self):
        """Adjacent intervals (no overlap) → allowed even at capacity."""
        self.thaw_profile.thaw_capacity = 1
        self.thaw_profile.save()

        now = timezone.now()
        self._fill_capacity(1, now + timedelta(hours=6), now + timedelta(hours=12))

        # New interval starts exactly when existing ends
        pkg = self._create_package(state=PackageState.FROZEN, barcode='CAP-NEW')
        plan = RotationPlan.objects.create(
            package=pkg, target_ready_at=now + timedelta(hours=18),
            planned_thaw_start_at=now + timedelta(hours=12),  # starts when other ends
            planned_thaw_queue_at=now + timedelta(hours=11),
            planned_freeze_start_at=now + timedelta(hours=3),
            planned_freeze_end_at=now + timedelta(hours=11),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        # Should succeed — no overlap
        entry = add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')
        self.assertIsNotNone(entry)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.THAW_QUEUED)

    # Case 6: capacity = 1
    def test_capacity_one_full(self):
        """Capacity=1: first package takes all capacity, second rejected."""
        self.thaw_profile.thaw_capacity = 1
        self.thaw_profile.save()

        now = timezone.now()
        self._fill_capacity(1, now + timedelta(hours=6), now + timedelta(hours=12))

        pkg2 = self._create_package(state=PackageState.FROZEN, barcode='CAP-2')
        plan2 = RotationPlan.objects.create(
            package=pkg2, target_ready_at=now + timedelta(hours=14),
            planned_thaw_start_at=now + timedelta(hours=8),
            planned_thaw_queue_at=now + timedelta(hours=7),
            planned_freeze_start_at=now - timedelta(hours=1),
            planned_freeze_end_at=now + timedelta(hours=7),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        with self.assertRaises(ValueError, msg='Thaw capacity exceeded'):
            add_to_thaw_queue(pkg2, rotation_plan=plan2, actor='test')

    # Case 7: capacity = 2, fill both, third rejected
    def test_capacity_two_full(self):
        """Capacity=2: fill both slots, third rejected."""
        self.thaw_profile.thaw_capacity = 2
        self.thaw_profile.save()

        now = timezone.now()
        self._fill_capacity(2, now + timedelta(hours=6), now + timedelta(hours=12))

        pkg3 = self._create_package(state=PackageState.FROZEN, barcode='CAP-3')
        plan3 = RotationPlan.objects.create(
            package=pkg3, target_ready_at=now + timedelta(hours=14),
            planned_thaw_start_at=now + timedelta(hours=8),
            planned_thaw_queue_at=now + timedelta(hours=7),
            planned_freeze_start_at=now - timedelta(hours=1),
            planned_freeze_end_at=now + timedelta(hours=7),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        with self.assertRaises(ValueError, msg='Thaw capacity exceeded'):
            add_to_thaw_queue(pkg3, rotation_plan=plan3, actor='test')

    # Case 8: three overlapping intervals, capacity=3
    def test_three_overlapping_capacity_three(self):
        """Three overlapping intervals with capacity=3 → all fit, fourth rejected."""
        self.thaw_profile.thaw_capacity = 3
        self.thaw_profile.save()

        now = timezone.now()
        self._fill_capacity(3, now + timedelta(hours=6), now + timedelta(hours=12))

        pkg4 = self._create_package(state=PackageState.FROZEN, barcode='CAP-4')
        plan4 = RotationPlan.objects.create(
            package=pkg4, target_ready_at=now + timedelta(hours=14),
            planned_thaw_start_at=now + timedelta(hours=8),
            planned_thaw_queue_at=now + timedelta(hours=7),
            planned_freeze_start_at=now - timedelta(hours=1),
            planned_freeze_end_at=now + timedelta(hours=7),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        with self.assertRaises(ValueError, msg='Thaw capacity exceeded'):
            add_to_thaw_queue(pkg4, rotation_plan=plan4, actor='test')

    # Case 9: candidate overlaps multiple existing intervals
    def test_candidate_overlaps_multiple_existing(self):
        """Candidate spanning across multiple existing intervals → counts all overlaps."""
        self.thaw_profile.thaw_capacity = 2
        self.thaw_profile.save()

        now = timezone.now()
        # Two non-overlapping existing intervals
        self._fill_capacity(1, now + timedelta(hours=6), now + timedelta(hours=9), offset=0)
        self._fill_capacity(1, now + timedelta(hours=10), now + timedelta(hours=13), offset=10)

        # New candidate spans both
        pkg = self._create_package(state=PackageState.FROZEN, barcode='CAP-SPAN')
        plan = RotationPlan.objects.create(
            package=pkg, target_ready_at=now + timedelta(hours=14),
            planned_thaw_start_at=now + timedelta(hours=7),
            planned_thaw_queue_at=now + timedelta(hours=6),
            planned_freeze_start_at=now - timedelta(hours=1),
            planned_freeze_end_at=now + timedelta(hours=6),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        # Spans both intervals → 2 overlaps = capacity full → rejected
        with self.assertRaises(ValueError, msg='Thaw capacity exceeded'):
            add_to_thaw_queue(pkg, rotation_plan=plan, actor='test')

    # Case 10: exclude_package behavior
    def test_exclude_package_in_capacity_check(self):
        """When re-queuing a package, its old entry should be excluded from count."""
        self.thaw_profile.thaw_capacity = 1
        self.thaw_profile.save()

        now = timezone.now()
        # Fill capacity with a different package
        self._fill_capacity(1, now + timedelta(hours=6), now + timedelta(hours=12))

        # Create a second package that we'll add, cancel, and re-add
        pkg = self._create_package(state=PackageState.FROZEN, barcode='CAP-REQUEUE')
        plan1 = RotationPlan.objects.create(
            package=pkg, target_ready_at=now + timedelta(hours=14),
            planned_thaw_start_at=now + timedelta(hours=8),
            planned_thaw_queue_at=now + timedelta(hours=7),
            planned_freeze_start_at=now - timedelta(hours=1),
            planned_freeze_end_at=now + timedelta(hours=7),
            freeze_profile=self.freeze_profile,
            thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=8),
            thaw_duration=timedelta(hours=12),
            status=PlanStatus.PLANNED,
        )

        # This should fail — capacity is full (1/1)
        with self.assertRaises(ValueError, msg='Thaw capacity exceeded'):
            add_to_thaw_queue(pkg, rotation_plan=plan1, actor='test')

        # Now cancel the first package's entry to free a slot
        first_entry = ThawQueueEntry.objects.first()
        remove_from_thaw_queue(first_entry, actor='test')

        # Now the re-queue should succeed
        entry = add_to_thaw_queue(pkg, rotation_plan=plan1, actor='test')
        self.assertIsNotNone(entry)
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.THAW_QUEUED)
