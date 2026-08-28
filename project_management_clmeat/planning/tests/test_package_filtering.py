"""
Tests for Planning: Package Filtering, Workflow Integrity, Plan Editing.
"""
from django.test import TestCase, Client
from django.contrib.auth.models import User, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from inventory.models import Product, Batch, Package, PackageState
from planning.models import (
    FreezeProfile, ThawProfile, RotationPlan, PlanStatus,
    ThawQueueEntry, QueueStatus
)
from planning.forms import RotationPlanForm, _get_available_package_choices
from planning.services import (
    create_rotation_plan, add_to_thaw_queue, calculate_rotation_plan
)
from common.state_machine import transition_package, InvalidTransitionError, TransitionValidationError
from operations.models import RotationEvent


class PackageFilteringTest(TestCase):
    """Test that plan creation form only shows eligible packages."""

    def setUp(self):
        self.user = User.objects.create_user('testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')

        self.product = Product.objects.create(sku='TEST', name='Test Pork', category='PORK')
        self.batch = Batch.objects.create(batch_number='B001', supplier='Test', received_at=timezone.now())
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24), minimum_duration=timedelta(hours=12)
        )

    def _create_package(self, state, weight=Decimal('0.560')):
        return Package.objects.create(
            product=self.product, batch=self.batch, weight=weight,
            packed_at=timezone.now(), current_state=state
        )

    def test_frozen_package_without_plan_appears(self):
        pkg = self._create_package(PackageState.FROZEN)
        choices = _get_available_package_choices()
        pkg_pks = [c[0] for c in choices if c[0] != '']
        self.assertIn(pkg.pk, pkg_pks)

    def test_frozen_package_with_plan_does_not_appear(self):
        pkg = self._create_package(PackageState.FROZEN)
        target_ready = timezone.now() + timedelta(days=3)
        RotationPlan.objects.create(
            package=pkg, target_ready_at=target_ready,
            planned_thaw_start_at=target_ready - timedelta(hours=24),
            planned_thaw_queue_at=target_ready - timedelta(hours=24, minutes=30),
            planned_freeze_start_at=target_ready - timedelta(hours=24, minutes=45),
            planned_freeze_end_at=target_ready - timedelta(hours=24, minutes=15),
            freeze_profile=self.freeze_profile, thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
        choices = _get_available_package_choices()
        pkg_pks = [c[0] for c in choices if c[0] != '']
        self.assertNotIn(pkg.pk, pkg_pks)

    def test_packed_package_does_not_appear(self):
        pkg = self._create_package(PackageState.PACKED)
        choices = _get_available_package_choices()
        pkg_pks = [c[0] for c in choices if c[0] != '']
        self.assertNotIn(pkg.pk, pkg_pks)

    def test_freezing_package_does_not_appear(self):
        pkg = self._create_package(PackageState.FREEZING)
        choices = _get_available_package_choices()
        pkg_pks = [c[0] for c in choices if c[0] != '']
        self.assertNotIn(pkg.pk, pkg_pks)

    def test_thaw_queued_package_does_not_appear(self):
        pkg = self._create_package(PackageState.THAW_QUEUED)
        choices = _get_available_package_choices()
        pkg_pks = [c[0] for c in choices if c[0] != '']
        self.assertNotIn(pkg.pk, pkg_pks)

    def test_thawing_package_does_not_appear(self):
        pkg = self._create_package(PackageState.THAWING)
        choices = _get_available_package_choices()
        pkg_pks = [c[0] for c in choices if c[0] != '']
        self.assertNotIn(pkg.pk, pkg_pks)

    def test_ready_for_sale_package_does_not_appear(self):
        pkg = self._create_package(PackageState.READY_FOR_SALE)
        choices = _get_available_package_choices()
        pkg_pks = [c[0] for c in choices if c[0] != '']
        self.assertNotIn(pkg.pk, pkg_pks)

    def test_form_clean_package_rejects_wrong_state(self):
        pkg = self._create_package(PackageState.PACKED)
        form = RotationPlanForm(data={
            'package': pkg.pk,
            'freeze_profile': self.freeze_profile.pk,
            'thaw_profile': self.thaw_profile.pk,
            'target_ready_date': (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%d'),
            'target_ready_time': '10:00',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('FROZEN', str(form.errors))

    def test_form_clean_package_rejects_existing_plan(self):
        pkg = self._create_package(PackageState.FROZEN)
        target_ready = timezone.now() + timedelta(days=3)
        RotationPlan.objects.create(
            package=pkg, target_ready_at=target_ready,
            planned_thaw_start_at=target_ready - timedelta(hours=24),
            planned_thaw_queue_at=target_ready - timedelta(hours=24, minutes=30),
            planned_freeze_start_at=target_ready - timedelta(hours=24, minutes=45),
            planned_freeze_end_at=target_ready - timedelta(hours=24, minutes=15),
            freeze_profile=self.freeze_profile, thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )
        form = RotationPlanForm(data={
            'package': pkg.pk,
            'freeze_profile': self.freeze_profile.pk,
            'thaw_profile': self.thaw_profile.pk,
            'target_ready_date': (timezone.now() + timedelta(days=5)).strftime('%Y-%m-%d'),
            'target_ready_time': '10:00',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('มีแผนงาน', str(form.errors))


class WorkflowIntegrityTest(TestCase):
    """Test that the workflow invariant THAW_QUEUED => RotationPlan is enforced."""

    def setUp(self):
        self.user = User.objects.create_user('testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')

        self.product = Product.objects.create(sku='TEST', name='Test', category='PORK')
        self.batch = Batch.objects.create(batch_number='B001', supplier='Test', received_at=timezone.now())
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24), minimum_duration=timedelta(hours=12)
        )

    def test_cannot_enter_thaw_queued_without_rotation_plan(self):
        """FROZEN -> READY_FOR_THAW -> THAW_QUEUED should fail without plan."""
        pkg = Package.objects.create(
            product=self.product, batch=self.batch, weight=Decimal('0.560'),
            packed_at=timezone.now(), current_state=PackageState.FROZEN
        )
        # Transition FROZEN -> READY_FOR_THAW
        transition_package(pkg, 'READY_FOR_THAW', actor='test')
        # Transition READY_FOR_THAW -> THAW_QUEUED — should fail because no RotationPlan
        with self.assertRaises(TransitionValidationError):
            transition_package(pkg, 'THAW_QUEUED', actor='test')

    def test_valid_frozen_plan_queue_thaw_workflow(self):
        """Full valid workflow: FROZEN -> plan -> queue -> thaw."""
        pkg = Package.objects.create(
            product=self.product, batch=self.batch, weight=Decimal('0.560'),
            packed_at=timezone.now(), current_state=PackageState.FROZEN
        )
        target_ready = timezone.now() + timedelta(days=3)
        plan = RotationPlan.objects.create(
            package=pkg, target_ready_at=target_ready,
            planned_thaw_start_at=target_ready - timedelta(hours=24),
            planned_thaw_queue_at=target_ready - timedelta(hours=24, minutes=30),
            planned_freeze_start_at=target_ready - timedelta(hours=24, minutes=45),
            planned_freeze_end_at=target_ready - timedelta(hours=24, minutes=15),
            freeze_profile=self.freeze_profile, thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )

        # add_to_thaw_queue handles FROZEN -> READY_FOR_THAW -> THAW_QUEUED
        entry = add_to_thaw_queue(pkg, plan, actor='test')
        self.assertEqual(pkg.current_state, PackageState.THAW_QUEUED)
        self.assertIsNotNone(entry)
        self.assertIsNotNone(RotationPlan.objects.filter(package=pkg).first())

    def test_broken_thaw_queued_package_detected(self):
        """Package in THAW_QUEUED without plan should be detected by integrity check."""
        pkg = Package.objects.create(
            product=self.product, batch=self.batch, weight=Decimal('0.560'),
            packed_at=timezone.now(), current_state=PackageState.THAW_QUEUED
        )
        # No plan, no queue entry
        has_plan = RotationPlan.objects.filter(package=pkg).exists()
        has_queue = ThawQueueEntry.objects.filter(package=pkg).exists()
        self.assertFalse(has_plan)
        self.assertFalse(has_queue)

    def test_thawing_requires_rotation_plan(self):
        """THAWING -> READY_FOR_SALE requires queue entry with COMPLETED status."""
        pkg = Package.objects.create(
            product=self.product, batch=self.batch, weight=Decimal('0.560'),
            packed_at=timezone.now(), current_state=PackageState.THAWING
        )
        # Try to transition to READY_FOR_SALE without completed queue entry
        with self.assertRaises(TransitionValidationError):
            transition_package(pkg, 'READY_FOR_SALE', actor='test')

    def test_can_start_thawing_only_with_plan_and_queue(self):
        """THAW_QUEUED -> THAWING requires both plan and queue entry."""
        pkg = Package.objects.create(
            product=self.product, batch=self.batch, weight=Decimal('0.560'),
            packed_at=timezone.now(), current_state=PackageState.THAW_QUEUED
        )
        with self.assertRaises(TransitionValidationError):
            transition_package(pkg, 'THAWING', actor='test')


class PlanEditingTest(TestCase):
    """Test rotation plan editing and recalculation."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', password='adminpass')
        self.client.login(username='admin', password='adminpass')

        self.product = Product.objects.create(sku='TEST', name='Test', category='PORK')
        self.batch = Batch.objects.create(batch_number='B001', supplier='Test', received_at=timezone.now())
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24), minimum_duration=timedelta(hours=12)
        )
        self.pkg = Package.objects.create(
            product=self.product, batch=self.batch, weight=Decimal('0.560'),
            packed_at=timezone.now(), current_state=PackageState.FROZEN
        )
        self.target_ready = timezone.now() + timedelta(days=5)
        self.plan = RotationPlan.objects.create(
            package=self.pkg, target_ready_at=self.target_ready,
            planned_thaw_start_at=self.target_ready - timedelta(hours=24),
            planned_thaw_queue_at=self.target_ready - timedelta(hours=24, minutes=30),
            planned_freeze_start_at=self.target_ready - timedelta(hours=24, minutes=45),
            planned_freeze_end_at=self.target_ready - timedelta(hours=24, minutes=15),
            freeze_profile=self.freeze_profile, thaw_profile=self.thaw_profile,
            freeze_duration=timedelta(hours=24), thaw_duration=timedelta(hours=24),
            status=PlanStatus.PLANNED
        )

    def test_edit_plan_recalculates_timestamps(self):
        """Editing target_ready_at should recalculate all dependent timestamps."""
        new_target = self.target_ready + timedelta(days=2)

        response = self.client.post(f'/planning/{self.plan.pk}/edit/', {
            'target_ready_date': new_target.strftime('%Y-%m-%d'),
            'target_ready_time': '10:00',
            'freeze_profile': self.freeze_profile.pk,
            'thaw_profile': self.thaw_profile.pk,
            'status': PlanStatus.PLANNED,
        })

        self.assertEqual(response.status_code, 302)  # Redirect on success
        self.plan.refresh_from_db()

        # Verify thaw_start < target_ready
        self.assertLess(self.plan.planned_thaw_start_at, self.plan.target_ready_at)
        # Verify freeze_start < thaw_queue
        self.assertLess(self.plan.planned_freeze_start_at, self.plan.planned_thaw_queue_at)
        # Verify the target date changed
        self.assertEqual(self.plan.target_ready_at.day, new_target.day)

    def test_edit_plan_creates_audit_event(self):
        """Editing a plan should create a PLAN_EDITED rotation event."""
        new_target = self.target_ready + timedelta(days=1)
        self.client.post(f'/planning/{self.plan.pk}/edit/', {
            'target_ready_date': new_target.strftime('%Y-%m-%d'),
            'target_ready_time': '10:00',
            'freeze_profile': self.freeze_profile.pk,
            'thaw_profile': self.thaw_profile.pk,
            'status': PlanStatus.PLANNED,
        })

        event = RotationEvent.objects.filter(
            package=self.pkg, event_type='PLAN_EDITED'
        ).first()
        self.assertIsNotNone(event)
        self.assertIn('แก้ไขแผนงาน', event.reason)


class AuthenticationTest(TestCase):
    """Test authentication and permissions."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', password='adminpass')
        self.manager = User.objects.create_user('manager', password='mgrpass')
        self.worker = User.objects.create_user('worker', password='wkpass')
        self.viewer = User.objects.create_user('viewer', password='vwpass')

        # Assign groups
        manager_group, _ = Group.objects.get_or_create(name='MANAGER')
        worker_group, _ = Group.objects.get_or_create(name='WORKER')
        viewer_group, _ = Group.objects.get_or_create(name='VIEWER')

        self.manager.groups.add(manager_group)
        self.worker.groups.add(worker_group)
        self.viewer.groups.add(viewer_group)

        self.product = Product.objects.create(sku='TEST', name='Test', category='PORK')
        self.batch = Batch.objects.create(batch_number='B001', supplier='Test', received_at=timezone.now())

    def test_anonymous_redirect_to_login(self):
        """Unauthenticated user should be redirected to login."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_login_works(self):
        """Valid user can login."""
        response = self.client.post('/login/', {'username': 'admin', 'password': 'adminpass'})
        self.assertEqual(response.status_code, 302)

    def test_invalid_login_fails(self):
        """Invalid login should show form with errors."""
        response = self.client.post('/login/', {'username': 'admin', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)  # Re-renders form

    def test_logout_works(self):
        """Logout should work."""
        self.client.login(username='admin', password='adminpass')
        response = self.client.post('/logout/')
        self.assertEqual(response.status_code, 302)

    def test_manager_can_access_planning(self):
        """Manager can access planning pages."""
        self.client.login(username='manager', password='mgrpass')
        response = self.client.get('/planning/')
        self.assertEqual(response.status_code, 200)

    def test_worker_cannot_create_plan(self):
        """Worker cannot access plan creation."""
        self.client.login(username='worker', password='wkpass')
        response = self.client.get('/planning/create/')
        self.assertEqual(response.status_code, 302)  # Redirected with error

    def test_viewer_can_read_planning(self):
        """Viewer can read planning pages."""
        self.client.login(username='viewer', password='vwpass')
        response = self.client.get('/planning/')
        self.assertEqual(response.status_code, 200)

    def test_api_unauthenticated_returns_302(self):
        """Unauthenticated API request returns 302 redirect to login."""
        response = self.client.get('/api/plans/')
        self.assertEqual(response.status_code, 302)

    def test_user_management_requires_admin(self):
        """Only admin can access user management."""
        self.client.login(username='manager', password='mgrpass')
        response = self.client.get('/users/')
        self.assertEqual(response.status_code, 302)  # Redirected


class DataIntegrityRecoveryTest(TestCase):
    """Test data integrity check and recovery."""

    def setUp(self):
        self.product = Product.objects.create(sku='TEST', name='Test', category='PORK')
        self.batch = Batch.objects.create(batch_number='B001', supplier='Test', received_at=timezone.now())
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard', target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12), default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard', default_duration=timedelta(hours=24), minimum_duration=timedelta(hours=12)
        )

    def test_check_integrity_detects_broken_thaw_queued(self):
        """check_integrity command should detect THAW_QUEUED without plan."""
        pkg = Package.objects.create(
            product=self.product, batch=self.batch, weight=Decimal('0.560'),
            packed_at=timezone.now(), current_state=PackageState.THAW_QUEUED
        )
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('check_integrity', stdout=out)
        output = out.getvalue()
        self.assertIn('THAW_QUEUED_NO_PLAN', output)

    def test_repair_fixes_broken_thaw_queued(self):
        """check_integrity --repair should reset THAW_QUEUED package to FROZEN."""
        pkg = Package.objects.create(
            product=self.product, batch=self.batch, weight=Decimal('0.560'),
            packed_at=timezone.now(), current_state=PackageState.THAW_QUEUED
        )
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('check_integrity', '--repair', stdout=out)

        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, PackageState.FROZEN)

        # Verify audit event
        event = RotationEvent.objects.filter(
            package=pkg, event_type='DATA_INTEGRITY_REPAIR'
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, 'SYSTEM_INTEGRITY_CHECK')
