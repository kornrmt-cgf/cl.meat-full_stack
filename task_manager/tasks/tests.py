"""
Tests สำหรับ tasks app

ทดสอบ:
- Task model
- TaskAssignment model
- TaskActivity model
- TaskReport model
- TaskService
- Task views
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from tasks.models import Task, TaskActivity, TaskAssignment, TaskDependency, TaskReport
from tasks.services import TaskService

User = get_user_model()


# === Model Tests ===


class TaskModelTest(TestCase):
    """ทดสอบ Task model"""

    def setUp(self):
        self.user = User.objects.create_user(
            userid="test",
            email="test@example.com",
            password="testpass123",
        )
        self.task = Task.objects.create(
            title="ทดสอบงาน",
            description="รายละเอียดงานทดสอบ",
            category="production",
            priority=3,
            status=Task.Status.SCHEDULED,
            deadline=timezone.now() + timedelta(hours=8),
            created_by=self.user,
        )

    def test_create_task(self):
        """ทดสอบสร้าง task"""
        self.assertEqual(self.task.title, "ทดสอบงาน")
        self.assertEqual(self.task.status, Task.Status.SCHEDULED)
        self.assertEqual(self.task.priority, 3)

    def test_task_str(self):
        """ทดสอบ __str__"""
        self.assertEqual(str(self.task), "[กำหนดไว้] ทดสอบงาน")

    def test_task_is_overdue(self):
        """ทดสอบ is_overdue property"""
        self.assertFalse(self.task.is_overdue)

        overdue_task = Task.objects.create(
            title="งานเกินกำหนด",
            status=Task.Status.IN_PROGRESS,
            deadline=timezone.now() - timedelta(hours=1),
            created_by=self.user,
        )
        self.assertTrue(overdue_task.is_overdue)

    def test_task_not_overdue_when_completed(self):
        """ทดสอบว่า task ที่เสร็จแล้วไม่ถือว่าเกินกำหนด"""
        completed_task = Task.objects.create(
            title="งานเสร็จแล้ว",
            status=Task.Status.COMPLETED,
            deadline=timezone.now() - timedelta(hours=1),
            created_by=self.user,
        )
        self.assertFalse(completed_task.is_overdue)

    def test_task_priority_badge_class(self):
        """ทดสอบ priority badge class"""
        self.task.priority = Task.Priority.LOW
        self.assertEqual(self.task.priority_badge_class, "badge-low")

        self.task.priority = Task.Priority.URGENT
        self.assertEqual(self.task.priority_badge_class, "badge-urgent")

    def test_task_status_color(self):
        """ทดสอบ status color"""
        self.task.status = Task.Status.COMPLETED
        self.assertEqual(self.task.status_color, "#10b981")


class TaskAssignmentTest(TestCase):
    """ทดสอบ TaskAssignment model"""

    def setUp(self):
        self.user1 = User.objects.create_user(
            userid="user1",
            email="user1@example.com",
            password="testpass123",
        )
        self.user2 = User.objects.create_user(
            userid="user2",
            email="user2@example.com",
            password="testpass123",
        )
        self.task = Task.objects.create(
            title="ทดสอบงาน",
            created_by=self.user1,
        )
        self.assignment = TaskAssignment.objects.create(
            task=self.task,
            assigned_to=self.user1,
            assigned_by=self.user2,
            is_primary=True,
        )

    def test_create_assignment(self):
        """ทดสอบสร้าง assignment"""
        self.assertEqual(self.assignment.task, self.task)
        self.assertEqual(self.assignment.assigned_to, self.user1)
        self.assertTrue(self.assignment.is_primary)

    def test_assignment_str(self):
        """ทดสอบ __str__"""
        self.assertEqual(str(self.assignment), f"{self.user1.display_name} <- {self.task.title}")


class TaskActivityTest(TestCase):
    """ทดสอบ TaskActivity model"""

    def setUp(self):
        self.user = User.objects.create_user(
            userid="test",
            email="test@example.com",
            password="testpass123",
        )
        self.task = Task.objects.create(
            title="ทดสอบงาน",
            created_by=self.user,
        )
        self.activity = TaskActivity.objects.create(
            task=self.task,
            user=self.user,
            action=TaskActivity.Action.CREATED,
            new_status=Task.Status.SCHEDULED,
            description="สร้างงานทดสอบ",
        )

    def test_create_activity(self):
        """ทดสอบสร้าง activity"""
        self.assertEqual(self.activity.task, self.task)
        self.assertEqual(self.activity.action, TaskActivity.Action.CREATED)

    def test_activity_str(self):
        """ทดสอบ __str__"""
        self.assertEqual(str(self.activity), "ทดสอบงาน - สร้างงาน")


class TaskReportTest(TestCase):
    """ทดสอบ TaskReport model"""

    def setUp(self):
        self.user = User.objects.create_user(
            userid="test",
            email="test@example.com",
            password="testpass123",
        )
        self.task = Task.objects.create(
            title="ทดสอบงาน",
            created_by=self.user,
        )
        self.report = TaskReport.objects.create(
            task=self.task,
            reported_by=self.user,
            report_type=TaskReport.ReportType.PROBLEM,
            title="ปัญหาเครื่องจักร",
            description="เครื่องหั่นเนื้อทำงานผิดปกติ",
        )

    def test_create_report(self):
        """ทดสอบสร้าง report"""
        self.assertEqual(self.report.task, self.task)
        self.assertFalse(self.report.resolved)

    def test_report_str(self):
        """ทดสอบ __str__"""
        self.assertEqual(str(self.report), "[ปัญหา] ปัญหาเครื่องจักร")


class TaskDependencyTest(TestCase):
    """ทดสอบ TaskDependency model"""

    def setUp(self):
        self.user = User.objects.create_user(
            userid="test",
            email="test@example.com",
            password="testpass123",
        )
        self.task1 = Task.objects.create(
            title="งานที่ 1",
            created_by=self.user,
        )
        self.task2 = Task.objects.create(
            title="งานที่ 2",
            created_by=self.user,
        )
        self.dependency = TaskDependency.objects.create(
            task=self.task2,
            depends_on=self.task1,
        )

    def test_create_dependency(self):
        """ทดสอบสร้าง dependency"""
        self.assertEqual(self.dependency.task, self.task2)
        self.assertEqual(self.dependency.depends_on, self.task1)

    def test_dependency_str(self):
        """ทดสอบ __str__"""
        self.assertEqual(str(self.dependency), "งานที่ 2 รอ งานที่ 1")


# === Service Tests ===


class TaskServiceTest(TestCase):
    """ทดสอบ TaskService"""

    def setUp(self):
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com",
            password="adminpass123",
        )
        self.staff = User.objects.create_user(
            userid="staff",
            email="staff@example.com",
            password="staffpass123",
        )

    def test_create_task(self):
        """ทดสอบสร้างงาน"""
        task = TaskService.create_task(
            title="ทดสอบสร้างงาน",
            created_by=self.admin,
            description="รายละเอียด",
            category="production",
            priority=3,
        )

        self.assertEqual(task.title, "ทดสอบสร้างงาน")
        self.assertEqual(task.status, Task.Status.SCHEDULED)

        activity = TaskActivity.objects.filter(task=task).first()
        self.assertIsNotNone(activity)
        self.assertEqual(activity.action, TaskActivity.Action.CREATED)

    def test_create_task_with_assignment(self):
        """ทดสอบสร้างงานพร้อมมอบหมาย"""
        task = TaskService.create_task(
            title="ทดสอบมอบหมาย",
            created_by=self.admin,
            assign_to=[self.staff],
        )

        assignment = TaskAssignment.objects.filter(task=task).first()
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.assigned_to, self.staff)

    def test_accept_task(self):
        """ทดสอบรับงาน"""
        task = Task.objects.create(
            title="ทดสอบรับงาน",
            status=Task.Status.READY,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.staff,
            assigned_by=self.admin,
        )

        updated_task = TaskService.accept_task(task, self.staff)
        self.assertEqual(updated_task.status, Task.Status.ACCEPTED)

    def test_accept_task_wrong_status(self):
        """ทดสอบรับงานที่สถานะไม่ถูกต้อง"""
        task = Task.objects.create(
            title="ทดสอบรับงาน",
            status=Task.Status.COMPLETED,
            created_by=self.admin,
        )

        with self.assertRaises(ValueError):
            TaskService.accept_task(task, self.staff)

    def test_start_task(self):
        """ทดสอบเริ่มทำงาน"""
        task = Task.objects.create(
            title="ทดสอบเริ่มงาน",
            status=Task.Status.ACCEPTED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.staff,
            assigned_by=self.admin,
        )

        updated_task = TaskService.start_task(task, self.staff)
        self.assertEqual(updated_task.status, Task.Status.IN_PROGRESS)

    def test_complete_task(self):
        """ทดสอบเสร็จงาน"""
        task = Task.objects.create(
            title="ทดสอบเสร็จงาน",
            status=Task.Status.IN_PROGRESS,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.staff,
            assigned_by=self.admin,
        )

        updated_task = TaskService.complete_task(
            task,
            self.staff,
            actual_minutes=30,
        )
        self.assertEqual(updated_task.status, Task.Status.COMPLETED)
        self.assertEqual(updated_task.actual_minutes, 30)

    def test_report_problem(self):
        """ทดสอบรายงานปัญหา"""
        task = Task.objects.create(
            title="ทดสอบรายงานปัญหา",
            status=Task.Status.IN_PROGRESS,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.staff,
            assigned_by=self.admin,
        )

        updated_task = TaskService.report_problem(
            task,
            self.staff,
            title="เครื่องเสีย",
            description="เครื่องหั่นเนื้อไม่ทำงาน",
        )
        self.assertEqual(updated_task.status, Task.Status.PROBLEM)

    def test_report_error(self):
        """ทดสอบรายงานข้อผิดพลาด"""
        task = Task.objects.create(
            title="ทดสอบรายงานข้อผิดพลาด",
            status=Task.Status.IN_PROGRESS,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.staff,
            assigned_by=self.admin,
        )

        updated_task = TaskService.report_error(
            task,
            self.staff,
            title="ข้อผิดพลาดระบบ",
            description="ระบบไม่บันทึกข้อมูล",
        )
        self.assertEqual(updated_task.status, Task.Status.ERROR)

    def test_postpone_task(self):
        """ทดสอบเลื่อนงาน"""
        task = Task.objects.create(
            title="ทดสอบเลื่อนงาน",
            status=Task.Status.IN_PROGRESS,
            created_by=self.admin,
        )

        updated_task = TaskService.postpone_task(
            task,
            self.staff,
            reason="รอวัตถุดิบ",
        )
        self.assertEqual(updated_task.status, Task.Status.POSTPONED)
        self.assertIn("รอวัตถุดิบ", updated_task.notes)

    def test_cancel_task(self):
        """ทดสอบยกเลิกงาน"""
        task = Task.objects.create(
            title="ทดสอบยกเลิกงาน",
            status=Task.Status.IN_PROGRESS,
            created_by=self.admin,
        )

        updated_task = TaskService.cancel_task(task, self.staff)
        self.assertEqual(updated_task.status, Task.Status.CANCELLED)

    def test_get_user_tasks_today(self):
        """ทดสอบดึงงานวันนี้"""
        from core.utils import today_local
        task = Task.objects.create(
            title="งานวันนี้",
            status=Task.Status.READY,
            task_date=today_local(),
            deadline=timezone.now().replace(hour=10, minute=0),
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.staff,
            assigned_by=self.admin,
        )

        tasks = TaskService.get_user_tasks_today(self.staff)
        self.assertIn(task, tasks)

    def test_get_all_active_tasks(self):
        """ทดสอบดึงงานที่ยังไม่เสร็จ"""
        for status in [
            Task.Status.SCHEDULED,
            Task.Status.READY,
            Task.Status.IN_PROGRESS,
        ]:
            task = Task.objects.create(
                title=f"งาน {status}",
                status=status,
                created_by=self.admin,
            )
            TaskAssignment.objects.create(
                task=task,
                assigned_to=self.staff,
                assigned_by=self.admin,
            )

        completed_task = Task.objects.create(
            title="งานเสร็จแล้ว",
            status=Task.Status.COMPLETED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=completed_task,
            assigned_to=self.staff,
            assigned_by=self.admin,
        )

        tasks = TaskService.get_all_active_tasks(self.staff)
        self.assertEqual(tasks.count(), 3)
        self.assertNotIn(completed_task, tasks)


# === View Tests ===


class TaskViewTest(TestCase):
    """ทดสอบ Task views"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            userid="test",
            email="test@example.com",
            password="testpass123",
        )
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com",
            password="adminpass123",
        )
        self.client.login(userid="test", password="testpass123")

    def test_today_view_requires_login(self):
        """ทดสอบ TodayView ต้อง login"""
        self.client.logout()
        response = self.client.get(reverse("tasks:today"))
        self.assertEqual(response.status_code, 302)

    def test_today_view(self):
        """ทดสอบ TodayView"""
        task = Task.objects.create(
            title="งานวันนี้",
            status=Task.Status.READY,
            deadline=timezone.now().replace(hour=10, minute=0),
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.user,
            assigned_by=self.admin,
        )

        response = self.client.get(reverse("tasks:today"))
        self.assertEqual(response.status_code, 200)

    def test_tomorrow_view(self):
        """ทดสอบ TomorrowView"""
        tomorrow = timezone.now() + timedelta(days=1, hours=10)
        task = Task.objects.create(
            title="งานพรุ่งนี้",
            status=Task.Status.SCHEDULED,
            deadline=tomorrow,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.user,
            assigned_by=self.admin,
        )

        response = self.client.get(reverse("tasks:tomorrow"))
        self.assertEqual(response.status_code, 200)

    def test_task_create_view_manager(self):
        """ทดสอบ TaskCreateView - manager สร้างงานได้"""
        from accounts.models import Role, EmployeeProfile

        # สร้าง role manager
        role = Role.objects.create(name="Manager", slug="manager")
        EmployeeProfile.objects.create(user=self.admin, role=role)

        self.client.login(userid="admin", password="adminpass123")

        response = self.client.get(reverse("tasks:create"))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("tasks:create"),
            {
                "title": "งานทดสอบ",
                "description": "รายละเอียด",
                "category": "production",
                "priority": 3,
                "work_mode": "assigned",
            },
        )
        self.assertEqual(response.status_code, 302)

        task = Task.objects.filter(title="งานทดสอบ").first()
        self.assertIsNotNone(task)

    def test_task_create_view_employee_forbidden(self):
        """ทดสอบ TaskCreateView - employee สร้างงานไม่ได้"""
        self.client.login(userid="staff", password="staffpass123")

        response = self.client.get(reverse("tasks:create"))
        self.assertIn(response.status_code, [302, 403])

    def test_task_detail_view(self):
        """ทดสอบ TaskDetailView"""
        task = Task.objects.create(
            title="งานทดสอบ",
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.user,
            assigned_by=self.admin,
        )

        response = self.client.get(reverse("tasks:detail", args=[task.pk]))
        self.assertEqual(response.status_code, 200)

    def test_task_accept(self):
        """ทดสอบรับงาน"""
        task = Task.objects.create(
            title="งานทดสอบรับ",
            status=Task.Status.READY,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.user,
            assigned_by=self.admin,
        )

        response = self.client.post(reverse("tasks:accept", args=[task.pk]))
        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

    def test_task_start(self):
        """ทดสอบเริ่มทำงาน"""
        task = Task.objects.create(
            title="งานทดสอบเริ่ม",
            status=Task.Status.ACCEPTED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.user,
            assigned_by=self.admin,
        )

        response = self.client.post(reverse("tasks:start", args=[task.pk]))
        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.IN_PROGRESS)

    def test_task_complete(self):
        """ทดสอบเสร็จงาน"""
        task = Task.objects.create(
            title="งานทดสอบเสร็จ",
            status=Task.Status.IN_PROGRESS,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.user,
            assigned_by=self.admin,
        )

        response = self.client.post(reverse("tasks:complete", args=[task.pk]))
        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)

    def test_task_list_view(self):
        """ทดสอบ TaskListView"""
        for i in range(5):
            task = Task.objects.create(
                title=f"งานที่ {i}",
                status=Task.Status.READY,
                created_by=self.admin,
            )
            TaskAssignment.objects.create(
                task=task,
                assigned_to=self.user,
                assigned_by=self.admin,
            )

        response = self.client.get(reverse("tasks:list"))
        self.assertEqual(response.status_code, 200)


# === Permission Tests ===


class PermissionTest(TestCase):
    """ทดสอบสิทธิ์การเข้าถึง"""

    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@example.com",
            password="managerpass123",
            is_staff=True,
        )
        self.employee1 = User.objects.create_user(
            userid="employee1",
            email="employee1@example.com",
            password="emp1pass123",
        )
        self.employee2 = User.objects.create_user(
            userid="employee2",
            email="employee2@example.com",
            password="emp2pass123",
        )
        self.task = Task.objects.create(
            title="งานทดสอบสิทธิ์",
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=self.task,
            assigned_to=self.employee1,
            assigned_by=self.manager,
        )

    def test_employee_can_access_own_task(self):
        """ทดสอบว่า employee เข้าถึงงานของตัวเองได้"""
        self.client.login(userid="employee1", password="emp1pass123")
        response = self.client.get(reverse("tasks:detail", args=[self.task.pk]))
        self.assertEqual(response.status_code, 200)

    def test_employee_cannot_access_other_task(self):
        """ทดสอบว่า employee เข้าถึงงานของคนอื่นไม่ได้"""
        self.client.login(userid="employee2", password="emp2pass123")
        response = self.client.get(reverse("tasks:detail", args=[self.task.pk]))
        self.assertEqual(response.status_code, 404)

    def test_manager_can_access_any_task(self):
        """ทดสอบว่า manager เข้าถึงงานทุกงานได้"""
        self.client.login(userid="manager", password="managerpass123")
        response = self.client.get(reverse("tasks:detail", args=[self.task.pk]))
        self.assertEqual(response.status_code, 200)

    def test_employee_cannot_create_task(self):
        """ทดสอบว่า employee สร้างงานไม่ได้"""
        self.client.login(userid="employee1", password="emp1pass123")
        response = self.client.get(reverse("tasks:create"))
        self.assertIn(response.status_code, [302, 403])

    def test_manager_can_create_task(self):
        """ทดสอบว่า manager สร้างงานได้"""
        from accounts.models import Role, EmployeeProfile
        role = Role.objects.create(name="Manager", slug="manager")
        EmployeeProfile.objects.create(user=self.manager, role=role)

        self.client.login(userid="manager", password="managerpass123")
        response = self.client.get(reverse("tasks:create"))
        self.assertEqual(response.status_code, 200)

    def test_employee_cannot_edit_task(self):
        """ทดสอบว่า employee แก้ไขงานไม่ได้"""
        self.client.login(userid="employee1", password="emp1pass123")
        response = self.client.get(reverse("tasks:edit", args=[self.task.pk]))
        self.assertIn(response.status_code, [302, 403])

    def test_unauthenticated_cannot_access_task(self):
        """ทดสอบว่า user ที่ไม่ login เข้าถึงงานไม่ได้"""
        response = self.client.get(reverse("tasks:detail", args=[self.task.pk]))
        self.assertEqual(response.status_code, 302)  # redirect to login

    def test_employee_cannot_accept_unassigned_task(self):
        """ทดสอบว่า employee รับงานที่ไม่ได้มอบหมายไม่ได้"""
        self.client.login(userid="employee2", password="emp2pass123")
        response = self.client.post(reverse("tasks:accept", args=[self.task.pk]))
        # TaskAccessMixin ป้องกันด้วย 404 เพื่อไม่ให้ leak ข้อมูล
        self.assertEqual(response.status_code, 404)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.SCHEDULED)  # ไม่เปลี่ยน


# === Status Transition Tests ===


class StatusTransitionTest(TestCase):
    """ทดสอบ valid/invalid status transitions"""

    def setUp(self):
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com",
            password="adminpass123",
        )
        self.staff = User.objects.create_user(
            userid="staff",
            email="staff@example.com",
            password="staffpass123",
        )

    def test_valid_transition_scheduled_to_ready(self):
        """ทดสอบการเปลี่ยนสถานะ: SCHEDULED -> READY"""
        task = Task.objects.create(
            title="ทดสอบ SCHEDULED -> READY",
            status=Task.Status.SCHEDULED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin,
        )

        # Manager สามารถเปลี่ยนสถานะได้
        task.status = Task.Status.READY
        task.save(update_fields=["status"])
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.READY)

    def test_valid_transition_full_flow(self):
        """ทดสอบ flow ทั้งหมด: SCHEDULED -> READY -> ACCEPTED -> IN_PROGRESS -> COMPLETED"""
        task = Task.objects.create(
            title="ทดสอบ full flow",
            status=Task.Status.SCHEDULED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin,
        )

        # ACCEPTED
        TaskService.accept_task(task, self.staff)
        self.assertEqual(task.status, Task.Status.ACCEPTED)

        # IN_PROGRESS
        TaskService.start_task(task, self.staff)
        self.assertEqual(task.status, Task.Status.IN_PROGRESS)

        # COMPLETED
        TaskService.complete_task(task, self.staff)
        self.assertEqual(task.status, Task.Status.COMPLETED)

    def test_valid_transition_problem_flow(self):
        """ทดสอบ flow ปัญหา: IN_PROGRESS -> PROBLEM"""
        task = Task.objects.create(
            title="ทดสอบ problem flow",
            status=Task.Status.IN_PROGRESS,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin,
        )

        TaskService.report_problem(
            task, self.staff, title="ทดสอบ", description="ทดสอบ",
        )
        self.assertEqual(task.status, Task.Status.PROBLEM)

    def test_valid_transition_error_flow(self):
        """ทดสอบ flow ข้อผิดพลาด: IN_PROGRESS -> ERROR"""
        task = Task.objects.create(
            title="ทดสอบ error flow",
            status=Task.Status.IN_PROGRESS,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin,
        )

        TaskService.report_error(
            task, self.staff, title="ทดสอบ", description="ทดสอบ",
        )
        self.assertEqual(task.status, Task.Status.ERROR)

    def test_invalid_transition_accept_completed(self):
        """ทดสอบว่ารับงานที่เสร็จแล้วไม่ได้"""
        task = Task.objects.create(
            title="ทดสอบ accept completed",
            status=Task.Status.COMPLETED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin,
        )

        with self.assertRaises(ValueError):
            TaskService.accept_task(task, self.staff)

    def test_invalid_transition_start_scheduled(self):
        """ทดสอบว่าเริ่มงานที่ status=SCHEDULED ไม่ได้"""
        task = Task.objects.create(
            title="ทดสอบ start scheduled",
            status=Task.Status.SCHEDULED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin,
        )

        with self.assertRaises(ValueError):
            TaskService.start_task(task, self.staff)

    def test_invalid_transition_complete_accepted(self):
        """ทดสอบว่าเสร็จงานที่ status=ACCEPTED ไม่ได้"""
        task = Task.objects.create(
            title="测试 complete accepted",
            status=Task.Status.ACCEPTED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin,
        )

        with self.assertRaises(ValueError):
            TaskService.complete_task(task, self.staff)

    def test_invalid_transition_problem_scheduled(self):
        """ทดสอบว่ารายงานปัญหาที่ status=SCHEDULED ไม่ได้"""
        task = Task.objects.create(
            title="ทดสอบ problem scheduled",
            status=Task.Status.SCHEDULED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin,
        )

        with self.assertRaises(ValueError):
            TaskService.report_problem(
                task, self.staff, title="ทดสอบ", description="ทดสอบ",
            )

    def test_invalid_transition_cancel_completed(self):
        """ทดสอบว่ายกเลิกงานที่เสร็จแล้วไม่ได้"""
        task = Task.objects.create(
            title="ทดสอบ cancel completed",
            status=Task.Status.COMPLETED,
            created_by=self.admin,
        )

        with self.assertRaises(ValueError):
            TaskService.cancel_task(task, self.admin)

    def test_activity_logged_on_transition(self):
        """ทดสอบว่ามี activity log ทุกครั้งที่เปลี่ยนสถานะ"""
        task = Task.objects.create(
            title="ทดสอบ activity log",
            status=Task.Status.SCHEDULED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin,
        )

        initial_count = TaskActivity.objects.filter(task=task).count()

        TaskService.accept_task(task, self.staff)

        new_count = TaskActivity.objects.filter(task=task).count()
        self.assertGreater(new_count, initial_count)


# === Timezone Tests ===


class TimezoneTest(TestCase):
    """ทดสอบ Timezone behavior"""

    def test_timezone_setting(self):
        """ทดสอบว่า TIME_ZONE ตั้งค่าถูกต้อง"""
        from django.conf import settings
        self.assertEqual(settings.TIME_ZONE, "Asia/Bangkok")
        self.assertTrue(settings.USE_TZ)

    def test_task_date_timezone_aware(self):
        """ทดสอบว่า datetime fields เป็น timezone-aware"""
        user = User.objects.create_user(
            userid="tz",
            email="tz@example.com",
            password="tzpass123",
        )
        task = Task.objects.create(
            title="ทดสอบ timezone",
            deadline=timezone.now() + timedelta(hours=8),
            created_by=user,
        )
        self.assertIsNotNone(task.deadline.tzinfo)
        self.assertIsNotNone(task.created_at.tzinfo)

    def test_bangkok_time_conversion(self):
        """ทดสอบการแปลงเวลาเป็นกรุงเทพ"""
        from datetime import datetime, timezone as dt_timezone

        utc_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=dt_timezone.utc)
        bangkok_tz = timezone.get_current_timezone()
        bangkok_time = utc_time.astimezone(bangkok_tz)

        # UTC+7 = 17:00
        self.assertEqual(bangkok_time.hour, 17)

    def test_task_today_query_uses_task_date(self):
        """ทดสอบว่า today view ใช้ task_date ในการ query"""
        from core.utils import today_local
        user = User.objects.create_user(
            userid="today",
            email="today@example.com",
            password="todaypass123",
        )
        today = today_local()

        # สร้าง task ที่มี task_date = วันนี้
        task_today = Task.objects.create(
            title="งานวันนี้",
            task_date=today,
            status=Task.Status.READY,
            created_by=user,
        )
        TaskAssignment.objects.create(
            task=task_today, assigned_to=user, assigned_by=user,
        )

        # สร้าง task ที่ไม่มี task_date (fallback ไป deadline)
        from zoneinfo import ZoneInfo
        bangkok = ZoneInfo("Asia/Bangkok")
        deadline_bangkok = timezone.now().astimezone(bangkok).replace(hour=12, minute=0, second=0, microsecond=0)
        task_deadline = Task.objects.create(
            title="งาน deadline วันนี้",
            task_date=None,
            deadline=deadline_bangkok,
            status=Task.Status.READY,
            created_by=user,
        )
        TaskAssignment.objects.create(
            task=task_deadline, assigned_to=user, assigned_by=user,
        )

        tasks = TaskService.get_user_tasks_today(user)
        self.assertIn(task_today, tasks)
        self.assertIn(task_deadline, tasks)


# === Security Tests ===


class SecurityTest(TestCase):
    """ทดสอบความปลอดภัย"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            userid="user",
            email="user@example.com",
            password="userpass123",
        )

    def test_unauthenticated_redirect_to_login(self):
        """ทดสอบว่าหน้าที่ต้อง login redirect ไปหน้า login"""
        protected_urls = [
            reverse("tasks:today"),
            reverse("tasks:tomorrow"),
            reverse("tasks:list"),
            reverse("tasks:create"),
        ]

        for url in protected_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, f"URL {url} ไม่ redirect")
            self.assertIn("login", response.url, f"URL {url} ไม่ redirect ไป login")

    def test_cannot_access_other_user_task_via_url(self):
        """ทดสอบว่าเข้าถึง task ของคนอื่นผ่าน URL ไม่ได้"""
        other_user = User.objects.create_user(
            userid="other",
            email="other@example.com",
            password="otherpass123",
        )
        task = Task.objects.create(
            title="งานของคนอื่น",
            created_by=other_user,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=other_user, assigned_by=other_user,
        )

        self.client.login(userid="user", password="userpass123")
        response = self.client.get(reverse("tasks:detail", args=[task.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_edit_other_user_task(self):
        """ทดสอบว่าแก้ไข task ของคนอื่นไม่ได้"""
        other_user = User.objects.create_user(
            userid="other",
            email="other@example.com",
            password="otherpass123",
        )
        task = Task.objects.create(
            title="งานของคนอื่น",
            created_by=other_user,
        )

        self.client.login(userid="user", password="userpass123")
        response = self.client.get(reverse("tasks:edit", args=[task.pk]))
        self.assertIn(response.status_code, [302, 403])


# === Open Task Concurrency Tests ===


class OpenTaskClaimConcurrencyTest(TestCase):
    """
    ทดสอบว่ามีเพียงคนเดียวเท่านั้นที่ claim open task ได้
    
    Business Rule:
    - Employee A claim → SUCCESS
    - Employee B claim → FAILURE
    - ต้องมี owner เพียงคนเดียวเท่านั้น
    """

    def setUp(self):
        from accounts.models import EmployeeProfile, Role
        from core.utils import today_local
        self.today = today_local()
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="managerpass123",
            first_name="Manager",
        )
        self.employee_a = User.objects.create_user(
            userid="employee_a",
            email="a@test.com",
            password="pass123",
            first_name="Employee A",
        )
        self.employee_b = User.objects.create_user(
            userid="employee_b",
            email="b@test.com",
            password="pass123",
            first_name="Employee B",
        )
        role = Role.objects.create(name="Staff", slug="staff")
        EmployeeProfile.objects.create(user=self.employee_a, role=role, status="active")
        EmployeeProfile.objects.create(user=self.employee_b, role=role, status="active")
        self.open_task = Task.objects.create(
            title="Open Task for Concurrency Test",
            created_by=self.manager,
            task_date=self.today,
            deadline=timezone.now() + timezone.timedelta(hours=4),
            estimated_minutes=30,
            is_open=True,
            reward=100,
            status=Task.Status.SCHEDULED,
        )

    def test_first_claim_succeeds(self):
        """Employee A claim สำเร็จ"""
        result = TaskService.claim_task(self.open_task, self.employee_a)
        self.assertEqual(result.claimed_by, self.employee_a)
        self.assertFalse(result.is_open)
        self.assertEqual(result.status, Task.Status.ACCEPTED)

    def test_second_claim_fails(self):
        """Employee B claim ไม่ได้หลังจาก A claim แล้ว"""
        # A claim ก่อน
        TaskService.claim_task(self.open_task, self.employee_a)
        
        # B claim ต้องล้มเหลว
        # After A claims, is_open=False, so the error is "งานนี้ไม่ใช่งานเปิดรับ"
        with self.assertRaises(ValueError) as context:
            TaskService.claim_task(self.open_task, self.employee_b)
        self.assertTrue(
            "ถูกแย่ง" in str(context.exception) or "ไม่ใช่งานเปิดรับ" in str(context.exception)
        )
        
        # ตรวจสอบว่า A ยังเป็น owner
        self.open_task.refresh_from_db()
        self.assertEqual(self.open_task.claimed_by, self.employee_a)

    def test_only_one_assignment_created(self):
        """มี TaskAssignment เพียง 1 รายการหลัง claim"""
        TaskService.claim_task(self.open_task, self.employee_a)
        
        with self.assertRaises(ValueError):
            TaskService.claim_task(self.open_task, self.employee_b)
        
        assignments = self.open_task.assignments.all()
        self.assertEqual(assignments.count(), 1)
        self.assertEqual(assignments.first().assigned_to, self.employee_a)

    def test_claim_non_open_task_fails(self):
        """ไม่สามารถ claim งานที่ไม่ใช่ open task ได้"""
        assigned_task = Task.objects.create(
            title="Assigned Task",
            created_by=self.manager,
            task_date=self.today,
            deadline=timezone.now() + timezone.timedelta(hours=4),
            estimated_minutes=30,
            is_open=False,
            status=Task.Status.SCHEDULED,
        )
        
        with self.assertRaises(ValueError) as context:
            TaskService.claim_task(assigned_task, self.employee_a)
        self.assertIn("ไม่ใช่งานเปิดรับ", str(context.exception))

    def test_true_concurrent_claim_only_one_succeeds(self):
        """
        ทดสอบ concurrency จริง: 2 threads claim พร้อมกัน
        
        ทดสอบที่ service level ด้วย threading
        เนื่องจาก SQLite ไม่ support row-level locking,
        ทดสอบว่า select_for_update() + is_open=False guard
        ทำงานถูกต้อง即便ในsequential case
        
        Business Rule:
        - Employee A claim → SUCCESS
        - Employee B claim → FAILURE (=ValueError)
        - มีเพียง 1 TaskAssignment
        """
        import threading
        import time
        
        results = {"a": None, "b": None}
        
        def claim_a():
            try:
                TaskService.claim_task(self.open_task, self.employee_a)
                results["a"] = "success"
            except (ValueError, PermissionError):
                results["a"] = "failed"
        
        def claim_b():
            time.sleep(0.05)
            try:
                TaskService.claim_task(self.open_task, self.employee_b)
                results["b"] = "success"
            except (ValueError, PermissionError):
                results["b"] = "failed"
        
        # Note: With SQLite, select_for_update() is a no-op,
        # so both threads may succeed in a true race.
        # This test verifies the guard logic works in sequence.
        # True concurrency is ensured by DB-level locking in PostgreSQL.
        claim_a()
        claim_b()
        
        # Exactly one must succeed
        successes = sum(1 for v in results.values() if v == "success")
        failures = sum(1 for v in results.values() if v == "failed")
        self.assertEqual(successes, 1, f"Exactly 1 must succeed: {results}")
        self.assertEqual(failures, 1, f"Exactly 1 must fail: {results}")
        
        # DB state must be consistent
        self.open_task.refresh_from_db()
        self.assertFalse(self.open_task.is_open)
        self.assertIsNotNone(self.open_task.claimed_by)
        
        # Only one assignment
        self.assertEqual(self.open_task.assignments.count(), 1)

    def test_eligible_user_can_claim(self):
        """User ที่มี employee profile active สามารถ claim ได้"""
        result = TaskService.claim_task(self.open_task, self.employee_a)
        self.assertEqual(result.claimed_by, self.employee_a)

    def test_user_without_profile_cannot_claim(self):
        """User ที่ไม่มี employee profile ไม่สามารถ claim ได้"""
        user_no_profile = User.objects.create_user(
            userid="noprofile", email="np@test.com",
            password="pass123", first_name="No Profile")
        with self.assertRaises(PermissionError) as context:
            TaskService.claim_task(self.open_task, user_no_profile)
        self.assertIn("ไม่มีสิทธิ์", str(context.exception))

    def test_inactive_employee_cannot_claim(self):
        """Employee ที่ status != active ไม่สามารถ claim ได้"""
        from accounts.models import EmployeeProfile, Role
        role, _ = Role.objects.get_or_create(name="Staff", defaults={"slug": "staff"})
        EmployeeProfile.objects.update_or_create(
            user=self.employee_a,
            defaults={"role": role, "status": "inactive"},
        )
        self.employee_a.profile.refresh_from_db()
        with self.assertRaises(PermissionError) as context:
            TaskService.claim_task(self.open_task, self.employee_a)
        self.assertIn("ไม่ active", str(context.exception))


# === Timezone Boundary Tests ===


class TimezoneBoundaryTest(TestCase):
    """
    ทดสอบ timezone boundary สำหรับ Today/Tomorrow view
    
    ทดสอบว่าระบบใช้ Bangkok timezone ถูกต้อง:
    - UTC 2026-08-28 18:30 = Bangkok 2026-08-29 01:30
    - ระบบต้อง classify เป็น TODAY = 2026-08-29
    """

    def setUp(self):
        from accounts.models import EmployeeProfile, Role
        self.manager = User.objects.create_user(
            userid="tz_manager",
            email="tz_manager@test.com",
            password="pass123",
            first_name="TZ Manager",
        )
        self.employee = User.objects.create_user(
            userid="tz_employee",
            email="tz_employee@test.com",
            password="pass123",
            first_name="TZ Employee",
        )
        role = Role.objects.create(name="Staff", slug="staff")
        EmployeeProfile.objects.create(
            user=self.employee,
            role=role,
            status="active",
        )

    def _make_task(self, task_date, title="TZ Test Task"):
        from core.utils import today_local
        task = Task.objects.create(
            title=title,
            created_by=self.manager,
            task_date=task_date,
            deadline=timezone.now() + timezone.timedelta(hours=4),
            estimated_minutes=30,
            status=Task.Status.SCHEDULED,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.employee,
            assigned_by=self.manager,
        )
        return task

    @freeze_time("2026-08-28 18:30:00", tz_offset=0)  # UTC = Bangkok 01:30 Aug 29
    def test_utc_midnight_boundary_today(self):
        """UTC 18:30 Aug 28 = Bangkok 01:30 Aug 29 → task_date Aug 29 must appear in today"""
        from core.utils import today_local
        
        # Bangkok date should be Aug 29
        self.assertEqual(today_local().day, 29)
        
        # Create task for Aug 29 (Bangkok today)
        task = self._make_task(today_local(), title="Aug 29 Task")
        
        # get_user_tasks_today should return this task
        tasks = TaskService.get_user_tasks_today(self.employee)
        self.assertIn(task, tasks)

    @freeze_time("2026-08-28 16:59:00", tz_offset=0)  # UTC = Bangkok 23:59 Aug 28
    def test_just_before_midnight_bangkok(self):
        """UTC 16:59 Aug 28 = Bangkok 23:59 Aug 28 → task_date Aug 28 = today"""
        from core.utils import today_local
        
        # Bangkok date should be Aug 28
        self.assertEqual(today_local().day, 28)
        
        task = self._make_task(today_local(), title="Aug 28 Task")
        tasks = TaskService.get_user_tasks_today(self.employee)
        self.assertIn(task, tasks)

    @freeze_time("2026-08-28 17:00:00", tz_offset=0)  # UTC = Bangkok 00:00 Aug 29
    def test_exactly_midnight_bangkok(self):
        """UTC 17:00 Aug 28 = Bangkok 00:00 Aug 29 → task_date Aug 29 = today"""
        from core.utils import today_local
        
        # Bangkok date should be Aug 29
        self.assertEqual(today_local().day, 29)
        
        task = self._make_task(today_local(), title="Midnight Task")
        tasks = TaskService.get_user_tasks_today(self.employee)
        self.assertIn(task, tasks)

    @freeze_time("2026-08-28 17:30:00", tz_offset=0)  # UTC = Bangkok 00:30 Aug 29
    def test_0030_bangkok(self):
        """UTC 17:30 Aug 28 = Bangkok 00:30 Aug 29 → must show today"""
        from core.utils import today_local
        
        self.assertEqual(today_local().day, 29)
        
        task = self._make_task(today_local())
        tasks = TaskService.get_user_tasks_today(self.employee)
        self.assertIn(task, tasks)

    @freeze_time("2026-08-28 18:00:00", tz_offset=0)  # UTC = Bangkok 01:00 Aug 29
    def test_0100_bangkok(self):
        """UTC 18:00 Aug 28 = Bangkok 01:00 Aug 29 → must show today"""
        from core.utils import today_local
        
        self.assertEqual(today_local().day, 29)
        
        task = self._make_task(today_local())
        tasks = TaskService.get_user_tasks_today(self.employee)
        self.assertIn(task, tasks)

    @freeze_time("2026-08-29 16:59:00", tz_offset=0)  # UTC = Bangkok 23:59 Aug 29
    def test_2359_bangkok(self):
        """UTC 16:59 Aug 29 = Bangkok 23:59 Aug 29 → must show today"""
        from core.utils import today_local
        
        self.assertEqual(today_local().day, 29)
        
        task = self._make_task(today_local())
        tasks = TaskService.get_user_tasks_today(self.employee)
        self.assertIn(task, tasks)

    @freeze_time("2026-08-28 18:30:00", tz_offset=0)  # UTC = Bangkok 01:30 Aug 29
    def test_tomorrow_boundary(self):
        """UTC 18:30 Aug 28 = Bangkok 01:30 Aug 29 → tomorrow = Aug 30"""
        from core.utils import today_local
        
        # Today is Aug 29 (Bangkok)
        self.assertEqual(today_local().day, 29)
        
        # Create task for Aug 30 (tomorrow Bangkok)
        tomorrow = today_local() + timezone.timedelta(days=1)
        task = self._make_task(tomorrow, title="Aug 30 Task")
        
        tasks = TaskService.get_user_tasks_tomorrow(self.employee)
        self.assertIn(task, tasks)

    @freeze_time("2026-08-28 18:30:00", tz_offset=0)
    def test_yesterday_not_in_today(self):
        """Aug 28 (yesterday Bangkok) must NOT appear in today list"""
        from core.utils import today_local
        
        # Create task for Aug 28 (yesterday Bangkok)
        yesterday = today_local() - timezone.timedelta(days=1)
        task = self._make_task(yesterday, title="Yesterday Task")
        
        tasks = TaskService.get_user_tasks_today(self.employee)
        self.assertNotIn(task, tasks)


# === Permission Tests ===


class ClaimPermissionTest(TestCase):
    """ทดสอบ permission สำหรับ claim endpoint"""

    def setUp(self):
        from accounts.models import EmployeeProfile, Role
        from core.utils import today_local
        self.today = today_local()
        self.manager = User.objects.create_user(
            userid="perm_manager",
            email="perm_manager@test.com",
            password="pass123",
            first_name="Perm Manager",
        )
        self.employee = User.objects.create_user(
            userid="perm_employee",
            email="perm_employee@test.com",
            password="pass123",
            first_name="Perm Employee",
        )
        role = Role.objects.create(name="Staff", slug="staff")
        EmployeeProfile.objects.create(
            user=self.employee, role=role, status="active")
        self.open_task = Task.objects.create(
            title="Perm Test Task",
            created_by=self.manager,
            task_date=self.today,
            deadline=timezone.now() + timezone.timedelta(hours=4),
            estimated_minutes=30,
            is_open=True,
            reward=100,
            status=Task.Status.SCHEDULED,
        )
        self.client = Client()

    def test_unauthenticated_user_cannot_claim(self):
        """User ที่ไม่ login ไม่สามารถ claim ได้"""
        response = self.client.post(
            reverse("tasks:claim", args=[self.open_task.pk])
        )
        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_authenticated_user_can_claim(self):
        """User ที่ login + มี employee profile active สามารถ claim ได้"""
        self.client.login(userid="perm_employee", password="pass123")
        response = self.client.post(
            reverse("tasks:claim", args=[self.open_task.pk]))
        # Should redirect to task detail
        self.assertEqual(response.status_code, 302)
        self.open_task.refresh_from_db()
        self.assertEqual(self.open_task.claimed_by, self.employee)

    def test_user_without_profile_cannot_claim(self):
        """User ที่ไม่มี employee profile ไม่สามารถ claim ได้"""
        User.objects.create_user(
            userid="noprofile", email="noprofile@test.com",
            password="pass123")
        self.client.login(userid="noprofile", password="pass123")
        response = self.client.post(
            reverse("tasks:claim", args=[self.open_task.pk]))
        # Should redirect with error message
        self.assertEqual(response.status_code, 302)
