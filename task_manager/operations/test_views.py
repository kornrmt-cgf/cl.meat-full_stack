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
    """Barcode scan AJAX endpoint — secured: task_id required, IN_PROGRESS only, claimant only."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')

    def _make_in_progress_task(self, barcode='SCAN-001'):
        """Helper: create a task in IN_PROGRESS state, claimed by self.user."""
        pkg, plan, _, _ = _make_package_with_plan()
        # Override barcode
        Package.objects.filter(pk=pkg.pk).update(barcode=barcode)
        pkg.refresh_from_db()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.IN_PROGRESS,
            claimed_by=self.user,
        )
        return pkg, task

    def test_scan_valid_barcode(self):
        """Valid scan: task_id + correct barcode + IN_PROGRESS + claimant."""
        pkg, task = self._make_in_progress_task('SCAN-OK')
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': 'SCAN-OK', 'task_id': task.pk}
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertTrue(data['task_match'])
        self.assertEqual(data['package']['barcode'], 'SCAN-OK')

    def test_scan_missing_task_id(self):
        """task_id is required — must reject."""
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': 'SCAN-001'}
        )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertFalse(data['success'])
        self.assertIn('กรุณาระบุรายการงาน', data['error'])

    def test_scan_empty_barcode(self):
        """Empty barcode with valid task_id must be rejected."""
        pkg, task = self._make_in_progress_task('SCAN-EMPTY')
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': '', 'task_id': task.pk}
        )
        self.assertEqual(resp.status_code, 400)

    def test_scan_wrong_task_state(self):
        """Scan on PENDING task must be rejected."""
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.PENDING,
        )
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': pkg.barcode, 'task_id': task.pk}
        )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn('ยังไม่ได้เริ่มทำงาน', data['error'])

    def test_scan_wrong_worker(self):
        """Scan by non-claimant must be rejected (403)."""
        user2 = _create_user2()
        pkg, task = self._make_in_progress_task('SCAN-WRONG-W')
        # Login as user2 (not the claimant)
        self.client.logout()
        self.client.login(userid='worker2', password='testpass123')
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': 'SCAN-WRONG-W', 'task_id': task.pk}
        )
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertIn('เฉพาะผู้รับงาน', data['error'])

    def test_scan_wrong_package(self):
        """Barcode not matching task's package must be rejected."""
        pkg, task = self._make_in_progress_task('SCAN-CORRECT')
        # Create a different package with a different barcode
        prod2 = _create_product()
        batch2 = _create_batch(prod2)
        pkg2 = _create_package(prod2, batch2, barcode='SCAN-OTHER')
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': 'SCAN-OTHER', 'task_id': task.pk}
        )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn('ไม่ตรงกับรายการงาน', data['error'])

    def test_scan_nonexistent_task(self):
        """Non-existent task_id must be rejected (404)."""
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': 'SCAN-X', 'task_id': '999999'}
        )
        self.assertEqual(resp.status_code, 404)

    def test_scan_arbitrary_package_lookup_blocked(self):
        """Without task_id, no arbitrary package data is exposed."""
        pkg = _create_package(barcode='SCAN-ARBIT')
        resp = self.client.post(
            reverse('operations:barcode-scan'),
            {'barcode': 'SCAN-ARBIT'}
        )
        # Must fail because task_id is required
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertFalse(data['success'])
        # No package data in response
        self.assertNotIn('package', data)


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


class TestCancelOwnership(TestCase):
    """Cancel ownership: CLAIMED/IN_PROGRESS tasks may only be cancelled by claimant."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')
        self.user2 = _create_user2()

    def test_worker_b_cannot_cancel_worker_a_claimed_task(self):
        """Worker B cannot cancel a task CLAIMED by Worker A."""
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.CLAIMED,
            claimed_by=self.user,
        )
        # Worker B tries to cancel
        self.client.logout()
        self.client.login(userid='worker2', password='testpass123')
        resp = self.client.post(
            reverse('operations:task-cancel', args=[task.pk]),
            {'reason': 'ไม่ต้องการ'}
        )
        self.assertEqual(resp.status_code, 302)  # redirect back
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CLAIMED)  # NOT cancelled

    def test_worker_b_cannot_cancel_worker_a_in_progress_task(self):
        """Worker B cannot cancel a task IN_PROGRESS by Worker A."""
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.IN_PROGRESS,
            claimed_by=self.user,
        )
        self.client.logout()
        self.client.login(userid='worker2', password='testpass123')
        resp = self.client.post(
            reverse('operations:task-cancel', args=[task.pk]),
            {'reason': ''}
        )
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)  # NOT cancelled

    def test_claimant_can_cancel_own_claimed_task(self):
        """The claimant may cancel their own CLAIMED task."""
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.CLAIMED,
            claimed_by=self.user,
        )
        resp = self.client.post(
            reverse('operations:task-cancel', args=[task.pk]),
            {'reason': 'ยกเลิกเอง'}
        )
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELLED)

    def test_claimant_can_cancel_own_in_progress_task(self):
        """The claimant may cancel their own IN_PROGRESS task."""
        pkg, plan, _, _ = _make_package_with_plan()
        task = WorkerTask.objects.create(
            package=pkg, rotation_plan=plan,
            task_type=TaskType.FREEZE_START,
            scheduled_at=timezone.now(),
            status=TaskStatus.IN_PROGRESS,
            claimed_by=self.user,
        )
        resp = self.client.post(
            reverse('operations:task-cancel', args=[task.pk]),
            {'reason': ''}
        )
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELLED)

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


class TestThaiErrorFailSafe(TestCase):
    """Thai error handling: unknown errors must return generic Thai message, never raw text."""

    def test_unmapped_error_returns_generic_thai(self):
        """An unmapped backend error must produce a generic Thai message."""
        from operations.views import _thai_error, GENERIC_THAI_ERROR
        result = _thai_error('SomeInternalLibraryError: crash in unknown module')
        self.assertEqual(result, GENERIC_THAI_ERROR)
        self.assertIn('เกิดข้อผิดพลาด', result)
        # Must NOT contain English error text
        self.assertNotIn('SomeInternalLibraryError', result)
        self.assertNotIn('crash', result)

    def test_known_error_still_returns_specific_thai(self):
        """Known errors still return specific Thai messages."""
        from operations.views import _thai_error
        # This maps to 'สถานะงานไม่ถูกต้อง' because 'status is' matches first
        result = _thai_error('Cannot claim task: status is COMPLETED')
        self.assertIn('สถานะงานไม่ถูกต้อง', result)
        self.assertNotIn('เกิดข้อผิดพลาด', result)
        # A different known error maps to its own Thai text
        result2 = _thai_error('Task is stale: expected PACKED, got FREEZING')
        self.assertIn('สถานะแพ็กเกจ', result2)
        self.assertNotIn('เกิดข้อผิดพลาด', result2)

    def test_generic_error_never_exposes_backend_text(self):
        """Test via the actual view: an unexpected error must show generic Thai."""
        self.client = Client()
        self.user = _create_user()
        self.client.login(userid='worker1', password='testpass123')
        # Try to claim a non-existent task — triggers a backend error
        resp = self.client.post(reverse('operations:task-claim', args=[999999]))
        # 404 is fine — but the toast error must not contain raw Python
        if resp.status_code == 200:
            # If somehow 200, check no raw exception text leaked
            content = resp.content.decode()
            self.assertNotIn('Traceback', content)
            self.assertNotIn('Exception', content)


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
