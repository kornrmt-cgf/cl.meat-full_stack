"""
Tests สำหรับ Worker Operations Views

ทดสอบ:
- Task list view
- Task detail view
- Claim/start/complete workflow via HTTP
- Barcode scan API
- Wrong package rejection
- Stale task rejection
- History view
- Security/permissions
"""
import json

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from inventory.models import (
    Batch,
    Category,
    Package,
    PackageState,
    Product,
    StorageLocation,
    Supplier,
)
from operations import services
from operations.models import (
    TaskEvent,
    TaskStatus,
    TaskType,
    WorkerTask,
)
from planning.models import (
    FreezeProfile,
    RotationCycle,
    RotationPlan,
    ThawProfile,
)
from planning.services import (
    add_to_thaw_queue,
    cancel_rotation_plan,
    complete_freeze,
    complete_thaw,
    create_rotation_plan,
    move_to_display,
    start_freeze,
    start_thaw,
)

User = get_user_model()


# ============================================================
# HELPERS
# ============================================================

def _create_user(userid='worker1', email='w1@test.com'):
    return User.objects.create_user(
        userid=userid, password='testpass123', email=email,
        first_name='Worker', last_name='One'
    )


def _create_user2(userid='worker2', email='w2@test.com'):
    return User.objects.create_user(
        userid=userid, password='testpass123', email=email,
        first_name='Worker', last_name='Two'
    )


def _create_category():
    cat, _ = Category.objects.get_or_create(
        code='PORK', defaults={'name': 'Pork', 'name_thai': 'หมู'}
    )
    return cat


def _create_supplier():
    sup, _ = Supplier.objects.get_or_create(name='Supplier A')
    return sup


_prod_counter = 0


def _create_product(cat=None, supplier=None):
    global _prod_counter
    _prod_counter += 1
    if cat is None:
        cat = _create_category()
    if supplier is None:
        supplier = _create_supplier()
    return Product.objects.create(
        sku=f'MP-{_prod_counter:04d}', name='Pork Neck', name_thai='คอหมู',
        category=cat, supplier=supplier,
        cost_per_kg='120', selling_price_per_kg='180',
    )


_batch_counter = 0


def _create_batch(product=None, supplier=None):
    global _batch_counter
    _batch_counter += 1
    if product is None:
        product = _create_product()
    if supplier is None:
        supplier = product.supplier
    return Batch.objects.create(
        batch_number=f'B-TEST-{_batch_counter:04d}', supplier=supplier,
        received_at=timezone.now(),
    )


def _create_package(product=None, batch=None, barcode='PKG-TEST-001'):
    if product is None:
        product = _create_product()
    if batch is None:
        batch = _create_batch(product)
    return Package.objects.create(
        product=product, batch=batch, barcode=barcode,
        weight='1.500', selling_price='270',
        packed_at=timezone.now(),
        current_state=PackageState.PACKED,
    )


def _create_freeze_profile():
    from datetime import timedelta
    return FreezeProfile.objects.create(
        name='Standard Freeze',
        target_temperature=Decimal('-18.00'),
        minimum_duration=timedelta(hours=12),
        default_duration=timedelta(hours=24),
        buffer_duration=timedelta(hours=2),
    )


def _create_thaw_profile():
    from datetime import timedelta
    return ThawProfile.objects.create(
        name='Standard Thaw',
        default_duration=timedelta(hours=8),
        minimum_duration=timedelta(hours=4),
        buffer_duration=timedelta(hours=1),
        thaw_capacity=3,
    )


_pkg_counter = 0


def _make_package_with_plan():
    """Create a package with a rotation plan and freeze profile."""
    global _pkg_counter
    _pkg_counter += 1
    prod = _create_product()
    batch = _create_batch(prod)
    pkg = _create_package(prod, batch, barcode=f'PKG-{_pkg_counter:06d}')
    fp = _create_freeze_profile()
    tp = _create_thaw_profile()
    plan = create_rotation_plan(
        pkg,
        timezone.now() + timedelta(days=5),
        fp, tp,
    )
    return pkg, plan, fp, tp


# ============================================================
# TEST CLASS
# ============================================================

class TestWorkerTaskListView(TestCase):
    """Task list view tests."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')
        self.url = reverse('operations:task-list')

    def test_list_view_returns_200(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_list_view_requires_login(self):
        self.client.logout()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_list_view_shows_tasks(self):
        pkg, plan, _, _ = _make_package_with_plan()
        WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'งานของฉัน')


class TestWorkerTaskDetailView(TestCase):
    """Task detail view tests."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_detail_view_returns_200(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.get(reverse('operations:task-detail', args=[task.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_detail_view_shows_package_info(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.get(reverse('operations:task-detail', args=[task.pk]))
        self.assertContains(resp, pkg.barcode)
        self.assertContains(resp, pkg.product.display_name)


class TestWorkerClaimFlow(TestCase):
    """Claim workflow via HTTP."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_claim_pending_task(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.post(
            reverse('operations:task-claim', args=[task.pk])
        )
        self.assertEqual(resp.status_code, 302)  # redirect
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CLAIMED)
        self.assertEqual(task.claimed_by, self.user)

    def test_claim_already_claimed_task(self):
        user2 = _create_user2()
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        # First claim
        self.client.post(reverse('operations:task-claim', args=[task.pk]))
        # Second claim by different user
        self.client.logout()
        self.client.login(userid='worker2', password='testpass123')
        resp = self.client.post(reverse('operations:task-claim', args=[task.pk]))
        self.assertEqual(resp.status_code, 302)
        # Task should still be claimed by user1
        task.refresh_from_db()
        self.assertEqual(task.claimed_by, self.user)


class TestWorkerStartFlow(TestCase):
    """Start workflow via HTTP."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_start_claimed_task(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        self.client.post(reverse('operations:task-claim', args=[task.pk]))
        resp = self.client.post(reverse('operations:task-start', args=[task.pk]))
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertIsNotNone(task.started_at)


class TestWorkerCompleteFlow(TestCase):
    """Complete workflow via HTTP with barcode validation."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_complete_task_no_barcode(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        # Claim + start
        self.client.post(reverse('operations:task-claim', args=[task.pk]))
        self.client.post(reverse('operations:task-start', args=[task.pk]))
        # Complete
        resp = self.client.post(
            reverse('operations:task-complete', args=[task.pk]),
            {'notes': 'Test completion'}
        )
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED)

    def test_complete_task_with_correct_barcode(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        self.client.post(reverse('operations:task-claim', args=[task.pk]))
        self.client.post(reverse('operations:task-start', args=[task.pk]))
        resp = self.client.post(
            reverse('operations:task-complete', args=[task.pk]),
            {'barcode': pkg.barcode, 'notes': ''}
        )
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED)

    def test_complete_task_wrong_barcode_rejected(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        self.client.post(reverse('operations:task-claim', args=[task.pk]))
        self.client.post(reverse('operations:task-start', args=[task.pk]))
        resp = self.client.post(
            reverse('operations:task-complete', args=[task.pk]),
            {'barcode': 'WRONG-BARCODE'}
        )
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        # Task should NOT be completed
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)


class TestStaleTaskRejection(TestCase):
    """Stale task rejection via HTTP."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_stale_task_rejected(self):
        """Stale task: package state changed externally, must be detected."""
        pkg, plan, _, _ = _make_package_with_plan()
        # Directly set package state to simulate an external operation
        Package.objects.filter(pk=pkg.pk).update(current_state='FREEZING')
        pkg.refresh_from_db()
        self.assertEqual(pkg.current_state, 'FREEZING')

        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        # Stale detection: expected=PACKED, actual=FREEZING
        self.assertTrue(services._is_stale(task))


class TestUnsupportedTaskType(TestCase):
    """Unsupported task types fail closed."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_unsupported_task_type_not_completed(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.REFREEZE,  # No handler
            scheduled_at=timezone.now(),
        )
        self.client.post(reverse('operations:task-claim', args=[task.pk]))
        self.client.post(reverse('operations:task-start', args=[task.pk]))
        resp = self.client.post(
            reverse('operations:task-complete', args=[task.pk]),
            {'notes': ''}
        )
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        # Must NOT be completed
        self.assertNotEqual(task.status, TaskStatus.COMPLETED)


class TestBarcodeScanAPI(TestCase):
    """Barcode scan AJAX endpoint."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_scan_valid_barcode(self):
        pkg = _create_package(barcode='SCAN-001')
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': 'SCAN-001'}
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertEqual(data['package']['barcode'], 'SCAN-001')

    def test_scan_invalid_barcode(self):
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': 'NOTEXIST'}
        )
        self.assertEqual(resp.status_code, 404)

    def test_scan_empty_barcode(self):
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': ''}
        )
        self.assertEqual(resp.status_code, 400)

    def test_scan_with_task_id_match(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': pkg.barcode, 'task_id': task.pk}
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertTrue(data['task_match'])

    def test_scan_with_task_id_mismatch(self):
        pkg1 = _create_package(barcode='SCAN-M1')
        prod2 = _create_product()
        batch2 = _create_batch(prod2)
        pkg2 = _create_package(prod2, batch2, barcode='SCAN-M2')
        plan = create_rotation_plan(
            pkg1, timezone.now() + timedelta(days=5),
            _create_freeze_profile(), _create_thaw_profile(),
        )
        task = WorkerTask.objects.create(
            package=pkg1, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': pkg2.barcode, 'task_id': task.pk}
        )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertFalse(data['success'])
        self.assertIn('ไม่ตรงกับรายการงาน', data['error'])


class TestTaskHistoryView(TestCase):
    """Task history view."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_history_view_returns_200(self):
        resp = self.client.get(reverse('operations:task-history'))
        self.assertEqual(resp.status_code, 200)

    def test_history_shows_completed_tasks(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.COMPLETED,
            claimed_by=self.user,
            completed_by=self.user,
            completed_at=timezone.now(),
        )
        resp = self.client.get(reverse('operations:task-history'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, pkg.barcode)


class TestTaskSecurity(TestCase):
    """Security and permission tests."""

    def setUp(self):
        self.client = Client()

    def test_task_list_requires_login(self):
        resp = self.client.get(reverse('operations:task-list'))
        self.assertEqual(resp.status_code, 302)

    def test_task_detail_requires_login(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.get(reverse('operations:task-detail', args=[task.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_task_claim_requires_login(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.post(reverse('operations:task-claim', args=[task.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_barcode_scan_requires_login(self):
        resp = self.client.post(reverse('operations:barcode-scan'), {'barcode': 'test'})
        self.assertEqual(resp.status_code, 302)


class TestCancelWorkflow(TestCase):
    """Cancel task workflow."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_cancel_pending_task(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.post(
            reverse('operations:task-cancel', args=[task.pk]),
            {'reason': 'ไม่ต้องการ'}
        )
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELLED)


class TestConcurrentClaim(TestCase):
    """Concurrent claim via HTTP — both users try to claim same task."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_two_users_claim_same_task(self):
        user1 = self.user
        user2 = _create_user('u2', 'u2@test.com')
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )

        # First claim succeeds
        resp1 = self.client.post(reverse('operations:task-claim', args=[task.pk]))
        self.assertEqual(resp1.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CLAIMED)
        self.assertEqual(task.claimed_by, user1)

        # Second user tries to claim — should fail (already CLAIMED)
        self.client.logout()
        self.client.login(userid=user2.userid, password='testpass123')
        resp2 = self.client.post(reverse('operations:task-claim', args=[task.pk]))
        self.assertEqual(resp2.status_code, 302)  # redirect
        task.refresh_from_db()
        # Still claimed by first user
        self.assertEqual(task.claimed_by, user1)
        self.assertEqual(task.status, TaskStatus.CLAIMED)


class TestAjaxEndpoints(TestCase):
    """AJAX endpoints tests."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def test_task_status_ajax(self):
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
        )
        resp = self.client.get(
            reverse('operations:task-status-ajax', args=[task.pk])
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['status'], 'PENDING')

    def test_task_list_ajax(self):
        resp = self.client.get(reverse('operations:task-list-ajax'))
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn('pending', data)
        self.assertIn('total', data)
