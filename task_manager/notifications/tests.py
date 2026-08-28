"""
Tests สำหรับ notifications app

ทดสอบ:
- NotificationService: create, duplicate prevention, preferences
- Task event notifications: assigned, starting, overdue, problem, error, rescheduled, postponed
- Notification views: list, unread count, mark as read
- Management command: process_task_automation
- Permissions: employee sees own notifications only
- Timezone correctness
- Duplicate prevention (idempotent)
"""

from datetime import timedelta

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from core.utils import today_local

from accounts.models import EmployeeProfile, Team
from tasks.models import Task, TaskAssignment, TaskActivity, TaskDependency
from notifications.models import Notification
from notifications.services import NotificationService

User = get_user_model()


class NotificationModelTest(TestCase):
    """ทดสอบ Notification model"""

    def setUp(self):
        self.user = User.objects.create_user(
            userid="user",
            email="user@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        self.task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )

    def test_create_notification(self):
        """ทดสอบสร้าง notification"""
        notif = Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="ได้รับมอบหมายงาน",
            message="คุณได้รับมอบหมายงานใหม่",
            task=self.task,
        )
        self.assertEqual(notif.user, self.user)
        self.assertEqual(notif.notification_type, Notification.Type.TASK_ASSIGNED)
        self.assertFalse(notif.is_read)
        self.assertIsNone(notif.read_at)

    def test_mark_as_read(self):
        """ทดสอบทำเครื่องหมายว่าอ่านแล้ว"""
        notif = Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="ได้รับมอบหมายงาน",
            message="คุณได้รับมอบหมายงานใหม่",
        )
        self.assertFalse(notif.is_read)
        notif.mark_as_read()
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertIsNotNone(notif.read_at)

    def test_mark_as_read_idempotent(self):
        """ทดสอบว่า mark_as_read ทำซ้ำได้โดยไม่ error"""
        notif = Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="ได้รับมอบหมายงาน",
            message="คุณได้รับมอบหมายงานใหม่",
        )
        notif.mark_as_read()
        first_read_at = notif.read_at
        notif.mark_as_read()  # ทำซ้ำ
        notif.refresh_from_db()
        self.assertEqual(notif.read_at, first_read_at)

    def test_notification_str(self):
        """ทดสอบ __str__"""
        notif = Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="ได้รับมอบหมายงาน",
            message="คุณได้รับมอบหมายงานใหม่",
        )
        self.assertIn("ได้รับมอบหมายงาน", str(notif))


class NotificationServiceTest(TestCase):
    """ทดสอบ NotificationService"""

    def setUp(self):
        self.user = User.objects.create_user(
            userid="user",
            email="user@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        EmployeeProfile.objects.create(user=self.user)
        EmployeeProfile.objects.create(user=self.manager)
        self.task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )

    def test_create_notification(self):
        """ทดสอบสร้าง notification ผ่าน service"""
        notif = NotificationService.create_notification(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="ได้รับมอบหมายงาน",
            message="คุณได้รับมอบหมายงานใหม่",
            task=self.task,
        )
        self.assertIsNotNone(notif)
        self.assertEqual(notif.user, self.user)

    def test_duplicate_prevention(self):
        """ทดสอบป้องกัน duplicate notification"""
        NotificationService.create_notification(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="ได้รับมอบหมายงาน",
            message="คุณได้รับมอบหมายงานใหม่",
            task=self.task,
        )
        # สร้างซ้ำ
        notif2 = NotificationService.create_notification(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="ได้รับมอบหมายงาน",
            message="คุณได้รับมอบหมายงานใหม่",
            task=self.task,
        )
        self.assertIsNone(notif2)
        # ตรวจสอบว่ามีแค่ 1 record
        self.assertEqual(
            Notification.objects.filter(
                user=self.user,
                notification_type=Notification.Type.TASK_ASSIGNED,
            ).count(),
            1,
        )

    def test_no_duplicate_without_task(self):
        """ทดสอบว่า notification ที่ไม่มี task ไม่ check duplicate"""
        notif1 = NotificationService.create_notification(
            user=self.user,
            notification_type=Notification.Type.SYSTEM,
            title="ระบบ",
            message="ข้อความระบบ",
        )
        notif2 = NotificationService.create_notification(
            user=self.user,
            notification_type=Notification.Type.SYSTEM,
            title="ระบบ",
            message="ข้อความระบบ",
        )
        self.assertIsNotNone(notif1)
        self.assertIsNotNone(notif2)

    def test_notification_preference_disabled(self):
        """ทดสอบว่า user ปิด notification แล้วไม่ได้รับ"""
        self.user.profile.notification_enabled = False
        self.user.profile.save()

        notif = NotificationService.create_notification(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="ได้รับมอบหมายงาน",
            message="คุณได้รับมอบหมายงานใหม่",
        )
        self.assertIsNone(notif)

    def test_per_type_preference_disabled(self):
        """ทดสอบ per-type notification preference"""
        self.user.profile.notify_task_assigned = False
        self.user.profile.save()

        notif = NotificationService.create_notification(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="ได้รับมอบหมายงาน",
            message="คุณได้รับมอบหมายงานใหม่",
        )
        self.assertIsNone(notif)

    def test_get_unread_count(self):
        """ทดสอบนับ unread count"""
        Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="แจ้งเตือน 1",
            message="ข้อความ 1",
        )
        Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="แจ้งเตือน 2",
            message="ข้อความ 2",
            is_read=True,
        )

        count = NotificationService.get_unread_count(self.user)
        self.assertEqual(count, 1)

    def test_mark_as_read(self):
        """ทดสอบ mark as read ผ่าน service"""
        notif = Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="แจ้งเตือน",
            message="ข้อความ",
        )
        result = NotificationService.mark_as_read(notif.pk, self.user)
        self.assertTrue(result)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_mark_as_read_wrong_user(self):
        """ทดสอบว่า user อื่น mark as read ไม่ได้"""
        notif = Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="แจ้งเตือน",
            message="ข้อความ",
        )
        other_user = User.objects.create_user(
            userid="other",
            email="other@test.com", password="testpass123"
        )
        result = NotificationService.mark_as_read(notif.pk, other_user)
        self.assertFalse(result)
        notif.refresh_from_db()
        self.assertFalse(notif.is_read)

    def test_mark_all_as_read(self):
        """ทดสอบ mark all as read"""
        for i in range(3):
            Notification.objects.create(
                user=self.user,
                notification_type=Notification.Type.TASK_ASSIGNED,
                title=f"แจ้งเตือน {i}",
                message=f"ข้อความ {i}",
            )

        count = NotificationService.mark_all_as_read(self.user)
        self.assertEqual(count, 3)
        self.assertEqual(
            Notification.objects.filter(user=self.user, is_read=False).count(), 0
        )


class TaskAssignedNotificationTest(TestCase):
    """ทดสอบ notification เมื่อมอบหมายงาน"""

    def setUp(self):
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        EmployeeProfile.objects.create(user=self.manager)
        EmployeeProfile.objects.create(user=self.employee)

    def test_assignment_creates_notification(self):
        """ทดสอบว่ามอบหมายงานสร้าง notification"""
        task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )
        notif = NotificationService.notify_task_assigned(task, self.employee, self.manager)
        self.assertIsNotNone(notif)
        self.assertEqual(notif.user, self.employee)
        self.assertEqual(notif.notification_type, Notification.Type.TASK_ASSIGNED)
        self.assertEqual(notif.task, task)


class TaskOverdueNotificationTest(TestCase):
    """ทดสอบ notification เมื่องานเกินกำหนด"""

    def setUp(self):
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        EmployeeProfile.objects.create(user=self.manager)
        EmployeeProfile.objects.create(user=self.employee)

    def test_overdue_creates_notification(self):
        """ทดสอบว่า overdue สร้าง notification"""
        task = Task.objects.create(
            title="งานเกินกำหนด",
            deadline=timezone.now() - timedelta(hours=1),
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.employee, assigned_by=self.manager
        )

        NotificationService.notify_task_overdue(task)
        notif = Notification.objects.filter(
            user=self.employee,
            notification_type=Notification.Type.TASK_OVERDUE,
        ).first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.task, task)


class TaskProblemNotificationTest(TestCase):
    """ทดสอบ notification เมื่อรายงานปัญหา"""

    def setUp(self):
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        EmployeeProfile.objects.create(user=self.manager)
        EmployeeProfile.objects.create(user=self.employee)

    def test_problem_notifies_manager(self):
        """ทดสอบว่ารายงานปัญหา notify manager"""
        task = Task.objects.create(
            title="งานมีปัญหา",
            status=Task.Status.PROBLEM,
            created_by=self.manager,
        )
        NotificationService.notify_task_problem(task, self.employee)
        notif = Notification.objects.filter(
            user=self.manager,
            notification_type=Notification.Type.TASK_PROBLEM,
        ).first()
        self.assertIsNotNone(notif)

    def test_error_notifies_manager(self):
        """ทดสอบว่ารายงานข้อผิดพลาด notify manager"""
        task = Task.objects.create(
            title="งานมีข้อผิดพลาด",
            status=Task.Status.ERROR,
            created_by=self.manager,
        )
        NotificationService.notify_task_error(task, self.employee)
        notif = Notification.objects.filter(
            user=self.manager,
            notification_type=Notification.Type.TASK_ERROR,
        ).first()
        self.assertIsNotNone(notif)


class TaskRescheduledNotificationTest(TestCase):
    """ทดสอบ notification เมื่อเปลี่ยนเวลางาน"""

    def setUp(self):
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        EmployeeProfile.objects.create(user=self.manager)
        EmployeeProfile.objects.create(user=self.employee)

    def test_reschedule_notifies_employee(self):
        """ทดสอบว่าเปลี่ยนเวลางาน notify employee"""
        task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.employee, assigned_by=self.manager
        )

        NotificationService.notify_task_rescheduled(task, self.employee, "เปลี่ยนเวลา: 09:00 -> 10:00")
        notif = Notification.objects.filter(
            user=self.employee,
            notification_type=Notification.Type.TASK_RESCHEDULED,
        ).first()
        self.assertIsNotNone(notif)

    def test_postpone_notifies_employee(self):
        """ทดสอบว่าเลื่อนงาน notify employee"""
        task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.POSTPONED,
            task_date=today_local() + timedelta(days=1),
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.employee, assigned_by=self.manager
        )

        NotificationService.notify_task_postponed(task, self.employee, "ติดธุระ")
        notif = Notification.objects.filter(
            user=self.employee,
            notification_type=Notification.Type.TASK_POSTPONED,
        ).first()
        self.assertIsNotNone(notif)


class TaskCompleteNotificationTest(TestCase):
    """ทดสอบ notification เมื่องานเสร็จ"""

    def setUp(self):
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        EmployeeProfile.objects.create(user=self.manager)
        EmployeeProfile.objects.create(user=self.employee)

    def test_complete_notifies_manager(self):
        """ทดสอบว่างานเสร็จ notify manager"""
        task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.employee, assigned_by=self.manager
        )

        NotificationService.notify_task_completed(task, self.manager)
        notif = Notification.objects.filter(
            user=self.manager,
            notification_type=Notification.Type.TASK_COMPLETED,
        ).first()
        self.assertIsNotNone(notif)


class NotificationViewTest(TestCase):
    """ทดสอบ Notification views"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            userid="user",
            email="user@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        EmployeeProfile.objects.create(user=self.user)
        self.client.login(userid="user", password="testpass123")

    def test_notification_list_view(self):
        """ทดสอบหน้า notification list"""
        response = self.client.get(reverse("notifications:list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("notifications", response.context)

    def test_unread_count_htmx(self):
        """ทดสอบ HTMX unread count"""
        Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="แจ้งเตือน",
            message="ข้อความ",
        )
        response = self.client.get(reverse("notifications:unread-count"))
        self.assertEqual(response.status_code, 200)

    def test_mark_as_read(self):
        """ทดสอบ mark as read"""
        notif = Notification.objects.create(
            user=self.user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="แจ้งเตือน",
            message="ข้อความ",
        )
        response = self.client.post(
            reverse("notifications:mark-read", args=[notif.pk]),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_mark_all_as_read(self):
        """ทดสอบ mark all as read"""
        for i in range(3):
            Notification.objects.create(
                user=self.user,
                notification_type=Notification.Type.TASK_ASSIGNED,
                title=f"แจ้งเตือน {i}",
                message=f"ข้อความ {i}",
            )

        response = self.client.post(
            reverse("notifications:mark-all-read"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Notification.objects.filter(user=self.user, is_read=False).count(), 0
        )

    def test_unauthenticated_redirected(self):
        """ทดสอบว่า user ที่ไม่ login redirect ไป login"""
        self.client.logout()
        response = self.client.get(reverse("notifications:list"))
        self.assertEqual(response.status_code, 302)

    def test_cannot_access_other_user_notifications(self):
        """ทดสอบว่าเข้าถึง notification ของคนอื่นไม่ได้"""
        other_user = User.objects.create_user(
            userid="other",
            email="other@test.com", password="testpass123"
        )
        notif = Notification.objects.create(
            user=other_user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="แจ้งเตือนของคนอื่น",
            message="ข้อความ",
        )
        # mark as read ของคนอื่น ต้อง fail
        result = NotificationService.mark_as_read(notif.pk, self.user)
        self.assertFalse(result)


class DependencyAwarenessTest(TestCase):
    """ทดสอบ dependency awareness"""

    def setUp(self):
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        EmployeeProfile.objects.create(user=self.manager)

    def test_dependency_block_detection(self):
        """ทดสอบว่าตรวจจับ dependency blocks ได้"""
        task_a = Task.objects.create(
            title="งาน A (ต้องทำก่อน)",
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        task_b = Task.objects.create(
            title="งาน B (รอ A)",
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )
        TaskDependency.objects.create(task=task_b, depends_on=task_a)

        blocked = NotificationService.check_dependency_blocks()
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["task"], task_b)
        self.assertEqual(blocked[0]["blocked_by"], task_a)

    def test_no_block_when_completed(self):
        """ทดสอบว่าไม่มี block เมื่องานที่รอเสร็จแล้ว"""
        task_a = Task.objects.create(
            title="งาน A",
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        task_b = Task.objects.create(
            title="งาน B",
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )
        TaskDependency.objects.create(task=task_b, depends_on=task_a)

        blocked = NotificationService.check_dependency_blocks()
        self.assertEqual(len(blocked), 0)

    def test_multiple_dependencies(self):
        """ทดสอบ dependency หลายรายการ"""
        task_a = Task.objects.create(
            title="งาน A", status=Task.Status.IN_PROGRESS, created_by=self.manager,
        )
        task_b = Task.objects.create(
            title="งาน B", status=Task.Status.IN_PROGRESS, created_by=self.manager,
        )
        task_c = Task.objects.create(
            title="งาน C (รอ A และ B)", status=Task.Status.SCHEDULED, created_by=self.manager,
        )
        TaskDependency.objects.create(task=task_c, depends_on=task_a)
        TaskDependency.objects.create(task=task_c, depends_on=task_b)

        blocked = NotificationService.check_dependency_blocks()
        self.assertEqual(len(blocked), 2)


class AutomationCommandTest(TestCase):
    """ทดสอบ management command"""

    def setUp(self):
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        EmployeeProfile.objects.create(user=self.manager)

    def test_dry_run_command(self):
        """ทดสอบ dry-run ของ command"""
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command("process_task_automation", "--dry-run", stdout=out)
        output = out.getvalue()
        self.assertIn("เสร็จสิ้นการประมวลผล", output)

    def test_command_runs_without_error(self):
        """ทดสอบว่า command รันได้โดยไม่ error"""
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command("process_task_automation", stdout=out)
        output = out.getvalue()
        self.assertIn("เสร็จสิ้นการประมวลผล", output)

    def test_idempotent_recurring_generation(self):
        """ทดสอบว่า generate recurring ซ้ำไม่สร้าง duplicate"""
        from tasks.models import TaskTemplate
        from django.core.management import call_command
        from io import StringIO

        template = TaskTemplate.objects.create(
            name="งานทำความสะอาด",
            recurrence_type=TaskTemplate.RecurrenceType.DAILY,
            recurrence_time="17:00",
            default_duration_minutes=60,
            is_active=True,
            created_by=self.manager,
        )

        # รันครั้งที่ 1
        call_command("process_task_automation", stdout=StringIO())
        today = today_local()
        count_1 = Task.objects.filter(recurrence_id=f"{template.pk}_{today.isoformat()}").count()

        # รันครั้งที่ 2
        call_command("process_task_automation", stdout=StringIO())
        count_2 = Task.objects.filter(recurrence_id=f"{template.pk}_{today.isoformat()}").count()

        self.assertEqual(count_1, 1)
        self.assertEqual(count_2, 1)


class TaskServiceNotificationIntegrationTest(TestCase):
    """ทดสอบว่า TaskService ส่ง notification ถูกต้อง"""

    def setUp(self):
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            first_name="สมศักดิ์",
            last_name=".manager",
            is_staff=True,
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        EmployeeProfile.objects.create(user=self.manager)
        EmployeeProfile.objects.create(user=self.employee)

    def test_assign_task_creates_notification(self):
        """ทดสอบว่า assign_task สร้าง notification"""
        from tasks.services import TaskService

        task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )
        TaskService.assign_task(task, [self.employee], self.manager)

        notif = Notification.objects.filter(
            user=self.employee,
            notification_type=Notification.Type.TASK_ASSIGNED,
            task=task,
        ).first()
        self.assertIsNotNone(notif)

    def test_complete_task_notifies_manager(self):
        """ทดสอบว่า complete_task notify manager"""
        from tasks.services import TaskService

        task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.employee, assigned_by=self.manager
        )
        TaskService.complete_task(task, self.employee)

        notif = Notification.objects.filter(
            user=self.manager,
            notification_type=Notification.Type.TASK_COMPLETED,
            task=task,
        ).first()
        self.assertIsNotNone(notif)

    def test_report_problem_notifies_manager(self):
        """ทดสอบว่า report_problem notify manager"""
        from tasks.services import TaskService

        task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.employee, assigned_by=self.manager
        )
        TaskService.report_problem(task, self.employee, "ปัญหา", "รายละเอียด")

        notif = Notification.objects.filter(
            user=self.manager,
            notification_type=Notification.Type.TASK_PROBLEM,
            task=task,
        ).first()
        self.assertIsNotNone(notif)

    def test_report_error_notifies_manager(self):
        """ทดสอบว่า report_error notify manager"""
        from tasks.services import TaskService

        task = Task.objects.create(
            title="งานทดสอบ",
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.employee, assigned_by=self.manager
        )
        TaskService.report_error(task, self.employee, "ข้อผิดพลาด", "รายละเอียด")

        notif = Notification.objects.filter(
            user=self.manager,
            notification_type=Notification.Type.TASK_ERROR,
            task=task,
        ).first()
        self.assertIsNotNone(notif)


class TimezoneNotificationTest(TestCase):
    """ทดสอบ timezone ใน notifications"""

    def test_notification_created_with_timezone(self):
        """ทดสอบว่า notification สร้างด้วย timezone-aware datetime"""
        user = User.objects.create_user(
            userid="user",
            email="user@test.com", password="testpass123"
        )
        notif = Notification.objects.create(
            user=user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title="แจ้งเตือน",
            message="ข้อความ",
        )
        self.assertTrue(timezone.is_aware(notif.created_at))

    def test_overdue_detection_uses_thailand_time(self):
        """ทดสอบว่า overdue detection ใช้ Thailand time"""
        from django.conf import settings
        self.assertEqual(settings.TIME_ZONE, "Asia/Bangkok")
        self.assertTrue(settings.USE_TZ)
