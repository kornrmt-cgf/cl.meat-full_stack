"""
Tests สำหรับ Milestone 2 - Scheduling & Queue Management

ทดสอบ:
- Schedule validation
- Queue ordering
- Rescheduling
- Conflict detection
- Postponement
- TaskTemplate
- Recurring tasks
- Permissions
- Timezone
"""

from datetime import timedelta, time, datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from core.utils import today_local

from tasks.models import Task, TaskActivity, TaskAssignment, TaskTemplate
from tasks.services import TaskService

from .services import SchedulingService

User = get_user_model()


# === Schedule Validation Tests ===


class ScheduleValidationTest(TestCase):
    """ทดสอบ schedule validation"""

    def test_valid_schedule(self):
        """ทดสอบ valid schedule: prepare <= start <= deadline"""
        now = timezone.now()
        prepare = now
        start = now + timedelta(minutes=30)
        deadline = now + timedelta(hours=2)

        # ไม่ควร raise error
        SchedulingService.validate_schedule(prepare, start, deadline)

    def test_invalid_prepare_after_start(self):
        """ทดสอบ invalid: prepare_at > start_at"""
        now = timezone.now()
        prepare = now + timedelta(hours=1)
        start = now

        with self.assertRaises(ValueError) as ctx:
            SchedulingService.validate_schedule(prepare, start)
        self.assertIn("เตรียมงาน", str(ctx.exception))

    def test_invalid_start_after_deadline(self):
        """ทดสอบ invalid: start_at > deadline"""
        now = timezone.now()
        start = now + timedelta(hours=2)
        deadline = now

        with self.assertRaises(ValueError) as ctx:
            SchedulingService.validate_schedule(start_at=start, deadline=deadline)
        self.assertIn("เริ่มงาน", str(ctx.exception))

    def test_valid_equal_times(self):
        """ทดสอบ valid: prepare = start = deadline"""
        now = timezone.now()
        SchedulingService.validate_schedule(now, now, now)

    def test_partial_schedule_valid(self):
        """ทดสอบ partial schedule (เฉพาะ deadline)"""
        SchedulingService.validate_schedule(deadline=timezone.now() + timedelta(hours=1))


# === Queue Ordering Tests ===


class QueueOrderingTest(TestCase):
    """ทดสอบ queue ordering"""

    def setUp(self):
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com", password="adminpass123"
        )
        self.staff = User.objects.create_user(
            userid="staff",
            email="staff@example.com", password="staffpass123"
        )
        self.today = today_local()

    def _create_task(self, title, start_at=None, queue_position=0):
        """สร้าง task พร้อม assignment"""
        task = Task.objects.create(
            title=title,
            task_date=self.today,
            start_at=start_at,
            status=Task.Status.SCHEDULED,
            queue_position=queue_position,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin
        )
        return task

    def test_default_ordering(self):
        """ทดสอบ default ordering: start_at -> queue_position -> priority"""
        t1 = self._create_task("งาน A", start_at=timezone.now().replace(hour=9, minute=0), queue_position=0)
        t2 = self._create_task("งาน B", start_at=timezone.now().replace(hour=10, minute=0), queue_position=0)
        t3 = self._create_task("งาน C", start_at=timezone.now().replace(hour=9, minute=0), queue_position=1)

        tasks = SchedulingService.get_user_tasks_for_date(self.staff, self.today)
        self.assertEqual(list(tasks), [t1, t3, t2])

    def test_reorder_task(self):
        """ทดสอบ reorder task"""
        t1 = self._create_task("งาน A", queue_position=0)
        t2 = self._create_task("งาน B", queue_position=1)
        t3 = self._create_task("งาน C", queue_position=2)

        # เลื่อน t3 ขึ้นไปตำแหน่ง 0
        SchedulingService.reorder_task(t3, 0, self.admin)

        t1.refresh_from_db()
        t2.refresh_from_db()
        t3.refresh_from_db()

        self.assertEqual(t3.queue_position, 0)
        self.assertEqual(t1.queue_position, 1)
        self.assertEqual(t2.queue_position, 2)

    def test_reorder_task_same_position(self):
        """ทดสอบ reorder ไปตำแหน่งเดิม"""
        t1 = self._create_task("งาน A", queue_position=0)
        SchedulingService.reorder_task(t1, 0, self.admin)
        t1.refresh_from_db()
        self.assertEqual(t1.queue_position, 0)

    def test_reorder_without_task_date(self):
        """ทดสอบ reorder task ที่ไม่มี task_date"""
        task = Task.objects.create(
            title="งานไม่มีวันที่",
            status=Task.Status.SCHEDULED,
            created_by=self.admin,
        )
        with self.assertRaises(ValueError):
            SchedulingService.reorder_task(task, 0, self.admin)

    def test_activity_logged_on_reorder(self):
        """ทดสอบว่ามี activity log เมื่อ reorder"""
        t1 = self._create_task("งาน A", queue_position=0)
        t2 = self._create_task("งาน B", queue_position=1)

        initial_count = TaskActivity.objects.filter(task=t2).count()
        SchedulingService.reorder_task(t2, 0, self.admin)
        new_count = TaskActivity.objects.filter(task=t2).count()
        self.assertGreater(new_count, initial_count)


# === Reschedule Tests ===


class RescheduleTest(TestCase):
    """ทดสอบ reschedule"""

    def setUp(self):
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com", password="adminpass123"
        )
        self.task = Task.objects.create(
            title="ทดสอบ reschedule",
            task_date=today_local(),
            start_at=timezone.now().replace(hour=9, minute=0),
            deadline=timezone.now().replace(hour=10, minute=0),
            status=Task.Status.SCHEDULED,
            created_by=self.admin,
        )

    def test_reschedule_task(self):
        """ทดสอบ reschedule เปลี่ยนเวลา"""
        new_start = timezone.now().replace(hour=11, minute=0)
        new_deadline = timezone.now().replace(hour=12, minute=0)
        new_date = today_local() + timedelta(days=1)

        SchedulingService.reschedule_task(
            self.task, self.admin,
            start_at=new_start,
            deadline=new_deadline,
            task_date=new_date,
        )

        self.task.refresh_from_db()
        self.assertEqual(self.task.task_date, new_date)
        self.assertEqual(self.task.start_at.hour, 11)
        self.assertEqual(self.task.deadline.hour, 12)

    def test_reschedule_activity_logged(self):
        """ทดสอบว่า reschedule มี activity log"""
        initial_count = TaskActivity.objects.filter(task=self.task).count()

        SchedulingService.reschedule_task(
            self.task, self.admin,
            start_at=timezone.now().replace(hour=14, minute=0),
            deadline=timezone.now().replace(hour=15, minute=0),
        )

        new_count = TaskActivity.objects.filter(task=self.task).count()
        self.assertGreater(new_count, initial_count)

    def test_reschedule_invalid_schedule(self):
        """ทดสอบ reschedule ด้วยเวลาที่ไม่ถูกต้อง"""
        with self.assertRaises(ValueError):
            SchedulingService.reschedule_task(
                self.task, self.admin,
                start_at=timezone.now().replace(hour=12, minute=0),
                deadline=timezone.now().replace(hour=10, minute=0),
            )


# === Conflict Detection Tests ===


class ConflictDetectionTest(TestCase):
    """ทดสอบ conflict detection"""

    def setUp(self):
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com", password="adminpass123"
        )
        self.staff = User.objects.create_user(
            userid="staff",
            email="staff@example.com", password="staffpass123"
        )

    def _create_task(self, start_hour, end_hour):
        now = timezone.now()
        task = Task.objects.create(
            title=f"งาน {start_hour}-{end_hour}",
            task_date=now.date(),
            start_at=now.replace(hour=start_hour, minute=0),
            deadline=now.replace(hour=end_hour, minute=0),
            status=Task.Status.SCHEDULED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.staff, assigned_by=self.admin
        )
        return task

    def test_detect_overlapping_tasks(self):
        """ทดสอบ detect tasks ที่ทับซ้อน"""
        self._create_task(9, 10)  # 09:00-10:00

        # 09:30-11:00 ทับซ้อนกับงานแรก
        conflicts = SchedulingService.detect_conflicts(
            self.staff,
            timezone.now().replace(hour=9, minute=30),
            timezone.now().replace(hour=11, minute=0),
        )
        self.assertTrue(conflicts.exists())

    def test_no_conflict_non_overlapping(self):
        """ทดสอบว่าไม่มี conflict เมื่อไม่ทับซ้อน"""
        self._create_task(9, 10)  # 09:00-10:00

        # 10:30-11:00 ไม่ทับซ้อน
        conflicts = SchedulingService.detect_conflicts(
            self.staff,
            timezone.now().replace(hour=10, minute=30),
            timezone.now().replace(hour=11, minute=0),
        )
        self.assertFalse(conflicts.exists())

    def test_has_conflict(self):
        """ทดสอบ has_conflict helper"""
        self._create_task(9, 10)

        self.assertTrue(SchedulingService.has_conflict(
            self.staff,
            timezone.now().replace(hour=9, minute=30),
            timezone.now().replace(hour=11, minute=0),
        ))

    def test_exclude_task_from_conflict(self):
        """ทดสอบ exclude task จาก conflict check"""
        task = self._create_task(9, 10)

        # ตรวจสอบทับซ้อนกับตัวเอง แต่ exclude ออก
        conflicts = SchedulingService.detect_conflicts(
            self.staff,
            timezone.now().replace(hour=9, minute=0),
            timezone.now().replace(hour=10, minute=0),
            exclude_task=task,
        )
        self.assertFalse(conflicts.exists())


# === Postponement Tests ===


class PostponementTest(TestCase):
    """ทดสอบ postponement"""

    def setUp(self):
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com", password="adminpass123"
        )
        self.staff = User.objects.create_user(
            userid="staff",
            email="staff@example.com", password="staffpass123"
        )
        self.task = Task.objects.create(
            title="ทดสอบเลื่อน",
            task_date=today_local(),
            start_at=timezone.now().replace(hour=9, minute=0),
            deadline=timezone.now().replace(hour=10, minute=0),
            status=Task.Status.IN_PROGRESS,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=self.task, assigned_to=self.staff, assigned_by=self.admin
        )

    def test_postpone_task(self):
        """ทดสอบเลื่อนงานไปวันใหม่"""
        new_date = today_local() + timedelta(days=1)
        new_start = timezone.now().replace(hour=11, minute=0) + timedelta(days=1)
        new_deadline = timezone.now().replace(hour=12, minute=0) + timedelta(days=1)

        SchedulingService.postpone_task_with_time(
            self.task, self.admin,
            new_task_date=new_date,
            new_start_at=new_start,
            new_deadline=new_deadline,
            reason="รอวัตถุดิบ",
        )

        self.task.refresh_from_db()
        self.assertEqual(self.task.task_date, new_date)
        self.assertEqual(self.task.status, Task.Status.POSTPONED)
        self.assertIn("รอวัตถุดิบ", self.task.notes)

    def test_postpone_preserves_assignment(self):
        """ทดสอบว่าเลื่อนงานยังคง assignment"""
        new_date = today_local() + timedelta(days=1)
        SchedulingService.postpone_task_with_time(
            self.task, self.admin, new_task_date=new_date
        )
        self.assertTrue(self.task.assignments.filter(assigned_to=self.staff).exists())

    def test_postpone_activity_logged(self):
        """ทดสอบว่าเลื่อนงานมี activity log"""
        initial_count = TaskActivity.objects.filter(task=self.task).count()
        new_date = today_local() + timedelta(days=1)
        SchedulingService.postpone_task_with_time(
            self.task, self.admin, new_task_date=new_date
        )
        new_count = TaskActivity.objects.filter(task=self.task).count()
        self.assertGreater(new_count, initial_count)

    def test_cannot_postpone_completed(self):
        """ทดสอบว่ายกเลิกงานที่เสร็จแล้วไม่ได้"""
        self.task.status = Task.Status.COMPLETED
        self.task.save()

        with self.assertRaises(ValueError):
            SchedulingService.postpone_task_with_time(
                self.task, self.admin, new_task_date=today_local()
            )


# === TaskTemplate Tests ===


class TaskTemplateTest(TestCase):
    """ทดสอบ TaskTemplate"""

    def setUp(self):
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com", password="adminpass123"
        )

    def test_create_template(self):
        """ทดสอบสร้าง template"""
        template = TaskTemplate.objects.create(
            name="ทำความสะอาด",
            description="ทำความสะอาดพื้นที่ผลิต",
            category="cleaning",
            priority=Task.Priority.NORMAL,
            default_duration_minutes=60,
            recurrence_type=TaskTemplate.RecurrenceType.WEEKDAYS,
            recurrence_time=time(17, 0),
            created_by=self.admin,
        )
        self.assertEqual(template.name, "ทำความสะอาด")
        self.assertEqual(template.recurrence_type, TaskTemplate.RecurrenceType.WEEKDAYS)

    def test_generate_task_from_template(self):
        """ทดสอบสร้าง task จาก template"""
        template = TaskTemplate.objects.create(
            name="ทำความสะอาด",
            description="ทำความสะอาดพื้นที่",
            category="cleaning",
            default_duration_minutes=60,
            default_prepare_minutes_before=15,
            recurrence_type=TaskTemplate.RecurrenceType.DAILY,
            recurrence_time=time(17, 0),
            created_by=self.admin,
        )

        today = today_local()
        task = template.generate_task(today, self.admin)

        self.assertIsNotNone(task)
        self.assertEqual(task.title, "ทำความสะอาด")
        self.assertEqual(task.task_date, today)
        self.assertEqual(task.status, Task.Status.SCHEDULED)
        self.assertEqual(task.template, template)
        self.assertTrue(task.is_recurring)
        self.assertIsNotNone(task.recurrence_id)

    def test_duplicate_prevention(self):
        """ทดสอบป้องกัน duplicate recurrence"""
        template = TaskTemplate.objects.create(
            name="ทำความสะอาด",
            category="cleaning",
            recurrence_type=TaskTemplate.RecurrenceType.DAILY,
            recurrence_time=time(17, 0),
            created_by=self.admin,
        )

        today = today_local()
        task1 = template.generate_task(today, self.admin)
        task2 = template.generate_task(today, self.admin)

        self.assertIsNotNone(task1)
        self.assertIsNone(task2)  # duplicate ถูกป้องกัน

    def test_template_does_not_modify_existing_task(self):
        """ทดสอบว่าแก้ไข template ไม่กระทบ task ที่สร้างแล้ว"""
        template = TaskTemplate.objects.create(
            name="ทำความสะอาด",
            category="cleaning",
            default_duration_minutes=60,
            recurrence_type=TaskTemplate.RecurrenceType.DAILY,
            recurrence_time=time(17, 0),
            created_by=self.admin,
        )

        today = today_local()
        task = template.generate_task(today, self.admin)

        # แก้ไข template
        template.name = "ทำความสะอาด v2"
        template.save()

        # Task ยังคงชื่อเดิม
        task.refresh_from_db()
        self.assertEqual(task.title, "ทำความสะอาด")


# === Recurring Task Tests ===


class RecurringTaskTest(TestCase):
    """ทดสอบ recurring tasks"""

    def setUp(self):
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com", password="adminpass123"
        )

    def test_daily_recurrence(self):
        """ทดสอบ daily recurrence"""
        template = TaskTemplate.objects.create(
            name="งาน daily",
            category="cleaning",
            recurrence_type=TaskTemplate.RecurrenceType.DAILY,
            recurrence_time=time(17, 0),
            default_duration_minutes=60,
            created_by=self.admin,
        )

        today = today_local()
        tasks = SchedulingService.generate_recurring_tasks(today, self.admin)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "งาน daily")

    def test_weekdays_recurrence(self):
        """ทดสอบ weekdays recurrence"""
        template = TaskTemplate.objects.create(
            name="งาน weekdays",
            category="cleaning",
            recurrence_type=TaskTemplate.RecurrenceType.WEEKDAYS,
            recurrence_time=time(17, 0),
            default_duration_minutes=60,
            created_by=self.admin,
        )

        # หาวันจันทร์ที่จะมาถึง
        today = today_local()
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = today + timedelta(days=days_until_monday)

        tasks = SchedulingService.generate_recurring_tasks(next_monday, self.admin)
        self.assertEqual(len(tasks), 1)

    def test_weekend_no_weekdays_recurrence(self):
        """ทดสอบว่าวันเสาร์-อาทิตย์ไม่สร้างงาน weekdays"""
        template = TaskTemplate.objects.create(
            name="งาน weekdays",
            category="cleaning",
            recurrence_type=TaskTemplate.RecurrenceType.WEEKDAYS,
            recurrence_time=time(17, 0),
            default_duration_minutes=60,
            created_by=self.admin,
        )

        # หาวันเสาร์
        today = today_local()
        days_until_saturday = (5 - today.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7
        next_saturday = today + timedelta(days=days_until_saturday)

        tasks = SchedulingService.generate_recurring_tasks(next_saturday, self.admin)
        self.assertEqual(len(tasks), 0)

    def test_duplicate_prevention_on_generate(self):
        """ทดสอบป้องกัน duplicate เมื่อ generate ซ้ำ"""
        template = TaskTemplate.objects.create(
            name="งาน daily",
            category="cleaning",
            recurrence_type=TaskTemplate.RecurrenceType.DAILY,
            recurrence_time=time(17, 0),
            default_duration_minutes=60,
            created_by=self.admin,
        )

        today = today_local()
        tasks1 = SchedulingService.generate_recurring_tasks(today, self.admin)
        tasks2 = SchedulingService.generate_recurring_tasks(today, self.admin)

        self.assertEqual(len(tasks1), 1)
        self.assertEqual(len(tasks2), 0)  # ไม่สร้างซ้ำ


# === Create from Template Tests ===


class CreateFromTemplateTest(TestCase):
    """ทดสอบสร้าง task จาก template"""

    def setUp(self):
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com", password="adminpass123"
        )
        self.staff = User.objects.create_user(
            userid="staff",
            email="staff@example.com", password="staffpass123"
        )
        self.template = TaskTemplate.objects.create(
            name="ทำความสะอาด",
            description="ทำความสะอาดพื้นที่ผลิต",
            category="cleaning",
            priority=Task.Priority.HIGH,
            default_duration_minutes=60,
            default_prepare_minutes_before=15,
            recurrence_type=TaskTemplate.RecurrenceType.NONE,
            location="โรงงาน A",
            created_by=self.admin,
        )

    def test_create_from_template(self):
        """ทดสอบสร้าง task จาก template"""
        today = today_local()
        task = SchedulingService.create_task_from_template(
            self.template, today, self.admin, assign_to=[self.staff]
        )

        self.assertEqual(task.title, "ทำความสะอาด")
        self.assertEqual(task.task_date, today)
        self.assertEqual(task.priority, Task.Priority.HIGH)
        self.assertEqual(task.location, "โรงงาน A")
        self.assertEqual(task.template, self.template)
        self.assertTrue(task.assignments.filter(assigned_to=self.staff).exists())

    def test_create_from_template_with_overrides(self):
        """ทดสอบสร้าง task จาก template พร้อม override"""
        today = today_local()
        task = SchedulingService.create_task_from_template(
            self.template, today, self.admin,
            title="ทำความสะอาดพิเศษ",
            location="โรงงาน B",
        )

        self.assertEqual(task.title, "ทำความสะอาดพิเศษ")
        self.assertEqual(task.location, "โรงงาน B")


# === Employee Week View Tests ===


class EmployeeWeekViewTest(TestCase):
    """ทดสอบ employee week view"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            userid="user",
            email="user@example.com", password="userpass123"
        )
        self.admin = User.objects.create_user(
            userid="admin",
            email="admin@example.com", password="adminpass123"
        )

    def test_week_view_requires_login(self):
        """ทดสอบ week view ต้อง login"""
        response = self.client.get(reverse("scheduling:week"))
        self.assertEqual(response.status_code, 302)

    def test_week_view(self):
        """ทดสอบ week view"""
        self.client.login(userid="user", password="userpass123")

        # สร้างงานวันนี้
        today = today_local()
        task = Task.objects.create(
            title="งานวันนี้",
            task_date=today,
            start_at=timezone.now().replace(hour=9, minute=0),
            status=Task.Status.SCHEDULED,
            created_by=self.admin,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.user, assigned_by=self.admin
        )

        response = self.client.get(reverse("scheduling:week"))
        self.assertEqual(response.status_code, 200)

    def test_week_view_with_date(self):
        """ทดสอบ week view พร้อม date parameter"""
        self.client.login(userid="user", password="userpass123")
        today = today_local()
        response = self.client.get(f"{reverse('scheduling:week')}?date={today}")
        self.assertEqual(response.status_code, 200)


# === Manager Schedule View Tests ===


class ManagerScheduleViewTest(TestCase):
    """ทดสอบ manager schedule view"""

    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@example.com", password="managerpass123", is_staff=True
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@example.com", password="emppass123"
        )

    def test_manager_schedule_requires_manager(self):
        """ทดสอบว่า employee เข้า schedule ไม่ได้"""
        self.client.login(userid="employee", password="emppass123")
        response = self.client.get(reverse("scheduling:manager-schedule"))
        self.assertIn(response.status_code, [302, 403])

    def test_manager_schedule_view(self):
        """ทดสอบ manager schedule view"""
        self.client.login(userid="manager", password="managerpass123")
        response = self.client.get(reverse("scheduling:manager-schedule"))
        self.assertEqual(response.status_code, 200)

    def test_manager_schedule_with_employee_filter(self):
        """ทดสอบ manager schedule พร้อม employee filter"""
        self.client.login(userid="manager", password="managerpass123")
        response = self.client.get(
            f"{reverse('scheduling:manager-schedule')}?employee={self.employee.pk}"
        )
        self.assertEqual(response.status_code, 200)


# === Reorder HTMX Tests ===


class ReorderHTMXTest(TestCase):
    """ทดสอบ reorder HTMX endpoint"""

    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@example.com", password="managerpass123", is_staff=True
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@example.com", password="emppass123"
        )
        self.today = today_local()
        self.task = Task.objects.create(
            title="งานทดสอบ",
            task_date=self.today,
            status=Task.Status.SCHEDULED,
            queue_position=0,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=self.task, assigned_to=self.employee, assigned_by=self.manager
        )

    def test_reorder_requires_manager(self):
        """ทดสอบว่า employee reorder ไม่ได้"""
        self.client.login(userid="employee", password="emppass123")
        response = self.client.post(
            reverse("scheduling:htmx-reorder", args=[self.task.pk]),
            data='{"position": 1}',
            content_type="application/json",
        )
        self.assertIn(response.status_code, [302, 403])

    def test_reorder_manager(self):
        """ทดสอบ manager reorder"""
        self.client.login(userid="manager", password="managerpass123")
        response = self.client.post(
            reverse("scheduling:htmx-reorder", args=[self.task.pk]),
            data='{"position": 1}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_reorder_invalid_position(self):
        """ทดสอบ reorder ด้วย invalid data"""
        self.client.login(userid="manager", password="managerpass123")
        response = self.client.post(
            reverse("scheduling:htmx-reorder", args=[self.task.pk]),
            data='not json',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


# === Conflict Check HTMX Tests ===


class ConflictCheckHTMXTest(TestCase):
    """ทดสอบ conflict check HTMX endpoint"""

    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@example.com", password="managerpass123", is_staff=True
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@example.com", password="emppass123"
        )

    def test_conflict_check_requires_manager(self):
        """ทดสอบว่า employee check conflict ไม่ได้"""
        self.client.login(userid="employee", password="emppass123")
        response = self.client.post(
            reverse("scheduling:htmx-conflict-check"),
            data='{}',
            content_type="application/json",
        )
        self.assertIn(response.status_code, [302, 403])


# === TaskTemplate View Tests ===


class TaskTemplateViewTest(TestCase):
    """ทดสอบ TaskTemplate views"""

    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@example.com", password="managerpass123", is_staff=True
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@example.com", password="emppass123"
        )

    def test_template_list_requires_manager(self):
        """ทดสอบว่า employee เข้า template list ไม่ได้"""
        self.client.login(userid="employee", password="emppass123")
        response = self.client.get(reverse("scheduling:template-list"))
        self.assertIn(response.status_code, [302, 403])

    def test_template_list_view(self):
        """ทดสอบ template list view"""
        self.client.login(userid="manager", password="managerpass123")
        response = self.client.get(reverse("scheduling:template-list"))
        self.assertEqual(response.status_code, 200)

    def test_template_create_view(self):
        """ทดสอบ template create view"""
        self.client.login(userid="manager", password="managerpass123")

        response = self.client.get(reverse("scheduling:template-create"))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("scheduling:template-create"),
            {
                "name": "ทดสอบแม่แบบ",
                "category": "cleaning",
                "priority": 2,
                "default_duration_minutes": 60,
                "default_prepare_minutes_before": 15,
                "recurrence_type": "none",
                "is_open": False,
                "reward": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TaskTemplate.objects.filter(name="ทดสอบแม่แบบ").exists())


# === Timezone Tests ===


class SchedulingTimezoneTest(TestCase):
    """ทดสอบ timezone สำหรับ scheduling"""

    def test_timezone_aware_schedule(self):
        """ทดสอบว่า schedule times เป็น timezone-aware"""
        user = User.objects.create_user(
            userid="tz",
            email="tz@example.com", password="tzpass123"
        )
        now = timezone.now()
        task = Task.objects.create(
            title="ทดสอบ timezone",
            task_date=now.date(),
            start_at=now + timedelta(hours=1),
            deadline=now + timedelta(hours=2),
            prepare_at=now,
            status=Task.Status.SCHEDULED,
            created_by=user,
        )
        self.assertIsNotNone(task.start_at.tzinfo)
        self.assertIsNotNone(task.deadline.tzinfo)
        self.assertIsNotNone(task.prepare_at.tzinfo)

    def test_schedule_validation_timezone_aware(self):
        """ทดสอบว่า schedule validation ใช้ timezone-aware datetime"""
        now = timezone.now()
        # ไม่ควร raise error
        SchedulingService.validate_schedule(
            prepare_at=now,
            start_at=now + timedelta(hours=1),
            deadline=now + timedelta(hours=2),
        )


# === Permission Tests for Scheduling ===


class SchedulingPermissionTest(TestCase):
    """ทดสอบ permissions สำหรับ scheduling"""

    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@example.com", password="managerpass123", is_staff=True
        )
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@example.com", password="emppass123"
        )

    def test_employee_cannot_reschedule(self):
        """ทดสอบว่า employee reschedule ไม่ได้"""
        task = Task.objects.create(
            title="งานทดสอบ",
            task_date=today_local(),
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task, assigned_to=self.employee, assigned_by=self.manager
        )

        self.client.login(userid="employee", password="emppass123")
        response = self.client.post(
            reverse("scheduling:htmx-reschedule", args=[task.pk]),
            {"start_at": "2025-01-01T10:00:00"},
        )
        self.assertIn(response.status_code, [302, 403])

    def test_manager_can_reschedule(self):
        """ทดสอบว่า manager reschedule ได้"""
        task = Task.objects.create(
            title="งานทดสอบ",
            task_date=today_local(),
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )

        self.client.login(userid="manager", password="managerpass123")
        response = self.client.post(
            reverse("scheduling:htmx-reschedule", args=[task.pk]),
            {"start_at": "2025-01-01T10:00:00", "deadline": "2025-01-01T11:00:00"},
        )
        self.assertEqual(response.status_code, 200)
