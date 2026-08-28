"""
Tests สำหรับ reports app

ทดสอบ:
- Daily Report
- Employee Report
- Status Report
- Performance Metrics
- CSV Export
- Permissions: employee ถูกบล็อก, manager เข้าได้
- Calculated metrics: completion rate, on-time, problem/error rate
- Division by zero safety
- Date range support
- Timezone correctness
"""

import csv
import io
from datetime import timedelta

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from core.utils import today_local

from accounts.models import EmployeeProfile, Team
from tasks.models import Task, TaskAssignment, TaskReport

User = get_user_model()


class DailyReportViewTest(TestCase):
    """ทดสอบ DailyReportView"""

    def setUp(self):
        self.client = Client()
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

        self.client.login(userid="manager", password="testpass123")
        from core.utils import today_local; self.today = today_local()

    def test_daily_report_view(self):
        """ทดสอบว่า daily report view ทำงานได้"""
        response = self.client.get(reverse("reports:daily"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("total", response.context)
        self.assertIn("completed", response.context)
        self.assertIn("incomplete", response.context)

    def test_daily_report_task_counts(self):
        """ทดสอบว่า daily report นับ tasks ถูกต้อง"""
        Task.objects.create(
            title="งาน 1", task_date=self.today,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        Task.objects.create(
            title="งาน 2", task_date=self.today,
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        Task.objects.create(
            title="งาน 3", task_date=self.today,
            status=Task.Status.PROBLEM,
            created_by=self.manager,
        )

        response = self.client.get(reverse("reports:daily"))
        self.assertEqual(response.context["total"], 3)
        self.assertEqual(response.context["completed"], 1)
        self.assertEqual(response.context["incomplete"], 2)
        self.assertEqual(response.context["problem_tasks"], 1)

    def test_daily_report_date_navigation(self):
        """ทดสอบว่า daily report มีลิงก์ prev/next date"""
        response = self.client.get(reverse("reports:daily"))
        self.assertIn("prev_date", response.context)
        self.assertIn("next_date", response.context)
        self.assertEqual(response.context["prev_date"], self.today - timedelta(days=1))
        self.assertEqual(response.context["next_date"], self.today + timedelta(days=1))

    def test_daily_report_specific_date(self):
        """ทดสอบ daily report สำหรับวันที่กำหนด"""
        yesterday = self.today - timedelta(days=1)
        Task.objects.create(
            title="งานวันวาน", task_date=yesterday,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )

        response = self.client.get(reverse("reports:daily") + f"?date={yesterday}")
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["target_date"], yesterday)

    def test_daily_report_no_tasks(self):
        """ทดสอบ daily report เมื่อไม่มีงาน"""
        response = self.client.get(reverse("reports:daily"))
        self.assertEqual(response.context["total"], 0)
        self.assertEqual(response.context["completed"], 0)
        self.assertEqual(response.context["incomplete"], 0)

    def test_employee_blocked(self):
        """ทดสอบว่า employee เข้า daily report ไม่ได้"""
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(reverse("reports:daily"))
        self.assertEqual(response.status_code, 403)


class EmployeeReportViewTest(TestCase):
    """ทดสอบ EmployeeReportView"""

    def setUp(self):
        self.client = Client()
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

        self.client.login(userid="manager", password="testpass123")
        from core.utils import today_local; self.today = today_local()

    def test_employee_report_view(self):
        """ทดสอบว่า employee report view ทำงานได้"""
        response = self.client.get(reverse("reports:employee"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("employees", response.context)

    def test_employee_report_with_selection(self):
        """ทดสอบ employee report เมื่อเลือก employee"""
        task = Task.objects.create(
            title="งาน A", task_date=self.today,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.employee,
            assigned_by=self.manager,
        )

        response = self.client.get(
            reverse("reports:employee") + f"?employee={self.employee.pk}"
        )
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["completed"], 1)
        self.assertEqual(response.context["user"], self.employee)

    def test_employee_report_with_date_range(self):
        """ทดสอบ employee report พร้อม date range"""
        yesterday = self.today - timedelta(days=1)
        task = Task.objects.create(
            title="งานวันวาน", task_date=yesterday,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.employee,
            assigned_by=self.manager,
        )

        response = self.client.get(
            reverse("reports:employee")
            + f"?employee={self.employee.pk}&date_from={yesterday}&date_to={yesterday}"
        )
        self.assertEqual(response.context["total"], 1)

    def test_employee_blocked(self):
        """ทดสอบว่า employee เข้า employee report ไม่ได้"""
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(reverse("reports:employee"))
        self.assertEqual(response.status_code, 403)


class StatusReportViewTest(TestCase):
    """ทดสอบ StatusReportView"""

    def setUp(self):
        self.client = Client()
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
        )
        EmployeeProfile.objects.create(user=self.manager)
        EmployeeProfile.objects.create(user=self.employee)

        self.client.login(userid="manager", password="testpass123")
        from core.utils import today_local; self.today = today_local()

    def test_status_report_view(self):
        """ทดสอบว่า status report view ทำงานได้"""
        response = self.client.get(reverse("reports:status"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("status_counts", response.context)
        self.assertIn("total", response.context)

    def test_status_report_counts(self):
        """ทดสอบว่า status report นับถูกต้อง"""
        Task.objects.create(
            title="งาน 1", task_date=self.today,
            status=Task.Status.COMPLETED, created_by=self.manager,
        )
        Task.objects.create(
            title="งาน 2", task_date=self.today,
            status=Task.Status.COMPLETED, created_by=self.manager,
        )
        Task.objects.create(
            title="งาน 3", task_date=self.today,
            status=Task.Status.IN_PROGRESS, created_by=self.manager,
        )
        Task.objects.create(
            title="งาน 4", task_date=self.today,
            status=Task.Status.CANCELLED, created_by=self.manager,
        )

        response = self.client.get(reverse("reports:status"))
        self.assertEqual(response.context["total"], 3)  # ไม่รวม CANCELLED
        self.assertEqual(
            response.context["status_counts"][Task.Status.COMPLETED]["count"], 2
        )
        self.assertEqual(
            response.context["status_counts"][Task.Status.IN_PROGRESS]["count"], 1
        )
        self.assertEqual(
            response.context["status_counts"][Task.Status.CANCELLED]["count"], 1
        )

    def test_status_report_date_range(self):
        """ทดสอบ status report พร้อม date range"""
        yesterday = self.today - timedelta(days=1)
        Task.objects.create(
            title="งานวันวาน", task_date=yesterday,
            status=Task.Status.COMPLETED, created_by=self.manager,
        )
        Task.objects.create(
            title="งานวันนี้", task_date=self.today,
            status=Task.Status.IN_PROGRESS, created_by=self.manager,
        )

        response = self.client.get(
            reverse("reports:status")
            + f"?date_from={yesterday}&date_to={yesterday}"
        )
        self.assertEqual(response.context["total"], 1)

    def test_employee_blocked(self):
        """ทดสอบว่า employee เข้า status report ไม่ได้"""
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(reverse("reports:status"))
        self.assertEqual(response.status_code, 403)


class PerformanceMetricsViewTest(TestCase):
    """ทดสอบ PerformanceMetricsView"""

    def setUp(self):
        self.client = Client()
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

        self.client.login(userid="manager", password="testpass123")
        from core.utils import today_local; self.today = today_local()

    def test_performance_metrics_view(self):
        """ทดสอบว่า performance metrics view ทำงานได้"""
        response = self.client.get(reverse("reports:performance"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("completion_rate", response.context)
        self.assertIn("on_time_rate", response.context)
        self.assertIn("problem_rate", response.context)
        self.assertIn("error_rate", response.context)

    def test_completion_rate_calculation(self):
        """ทดสอบว่า completion rate คำนวณถูกต้อง"""
        # 4 completed, 1 in progress -> 80%
        for i in range(4):
            Task.objects.create(
                title=f"งานเสร็จ {i}", task_date=self.today,
                status=Task.Status.COMPLETED,
                completed_at=timezone.now(),
                created_by=self.manager,
            )
        Task.objects.create(
            title="งานไม่เสร็จ", task_date=self.today,
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )

        response = self.client.get(reverse("reports:performance"))
        self.assertEqual(response.context["total_tasks"], 5)
        self.assertEqual(response.context["total_completed"], 4)
        self.assertEqual(response.context["completion_rate"], 80.0)

    def test_on_time_rate_calculation(self):
        """ทดสอบว่า on-time rate คำนวณถูกต้อง"""
        deadline = timezone.now() + timedelta(hours=2)
        # on time: completed_at < deadline
        Task.objects.create(
            title="งานตรงเวลา", task_date=self.today,
            status=Task.Status.COMPLETED,
            deadline=deadline,
            completed_at=deadline - timedelta(hours=1),
            created_by=self.manager,
        )
        # late
        Task.objects.create(
            title="งานสาย", task_date=self.today,
            status=Task.Status.COMPLETED,
            deadline=deadline,
            completed_at=deadline + timedelta(hours=1),
            created_by=self.manager,
        )

        response = self.client.get(reverse("reports:performance"))
        self.assertEqual(response.context["on_time_rate"], 50.0)

    def test_problem_rate_calculation(self):
        """ทดสอบว่า problem rate คำนวณถูกต้อง"""
        task = Task.objects.create(
            title="งานมีปัญหา", task_date=self.today,
            status=Task.Status.PROBLEM,
            created_by=self.manager,
        )
        TaskReport.objects.create(
            task=task,
            reported_by=self.employee,
            report_type=TaskReport.ReportType.PROBLEM,
            description="ปัญหา",
        )
        Task.objects.create(
            title="งานปกติ", task_date=self.today,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )

        response = self.client.get(reverse("reports:performance"))
        self.assertEqual(response.context["total_tasks"], 2)
        self.assertEqual(response.context["problem_rate"], 50.0)

    def test_error_rate_calculation(self):
        """ทดสอบว่า error rate คำนวณถูกต้อง"""
        task = Task.objects.create(
            title="งานมีข้อผิดพลาด", task_date=self.today,
            status=Task.Status.ERROR,
            created_by=self.manager,
        )
        TaskReport.objects.create(
            task=task,
            reported_by=self.employee,
            report_type=TaskReport.ReportType.ERROR,
            description="ข้อผิดพลาด",
        )
        Task.objects.create(
            title="งานปกติ", task_date=self.today,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )

        response = self.client.get(reverse("reports:performance"))
        self.assertEqual(response.context["error_rate"], 50.0)

    def test_division_by_zero_no_tasks(self):
        """ทดสอบว่าไม่มี error เมื่อไม่มีงาน (division by zero)"""
        response = self.client.get(reverse("reports:performance"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["completion_rate"], 0)
        self.assertEqual(response.context["on_time_rate"], 0)
        self.assertEqual(response.context["problem_rate"], 0)
        self.assertEqual(response.context["error_rate"], 0)

    def test_employee_blocked(self):
        """ทดสอบว่า employee เข้า performance metrics ไม่ได้"""
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(reverse("reports:performance"))
        self.assertEqual(response.status_code, 403)


class CSVExportViewTest(TestCase):
    """ทดสอบ CSVExportView"""

    def setUp(self):
        self.client = Client()
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

        self.client.login(userid="manager", password="testpass123")
        from core.utils import today_local; self.today = today_local()

    def test_csv_export_view(self):
        """ทดสอบว่า CSV export ทำงานได้"""
        response = self.client.get(reverse("reports:csv-export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_csv_export_has_headers(self):
        """ทดสอบว่า CSV มี headers ถูกต้อง"""
        response = self.client.get(reverse("reports:csv-export"))
        # อ่าน BOM + content
        content = response.content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        headers = next(reader)
        self.assertIn("รหัส", headers)
        self.assertIn("ชื่องาน", headers)
        self.assertIn("สถานะ", headers)

    def test_csv_export_with_tasks(self):
        """ทดสอบว่า CSV export มีข้อมูล tasks"""
        task = Task.objects.create(
            title="งาน A", task_date=self.today,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.employee,
            assigned_by=self.manager,
        )

        response = self.client.get(reverse("reports:csv-export"))
        content = response.content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        # header + 1 data row
        self.assertEqual(len(rows), 2)
        self.assertIn("งาน A", rows[1])

    def test_csv_export_with_date_filter(self):
        """ทดสอบว่า CSV export กรองตามวันที่ถูกต้อง"""
        yesterday = self.today - timedelta(days=1)
        Task.objects.create(
            title="งานวันวาน", task_date=yesterday,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        Task.objects.create(
            title="งานวันนี้", task_date=self.today,
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )

        response = self.client.get(
            reverse("reports:csv-export") + f"?date_from={self.today}&date_to={self.today}"
        )
        content = response.content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        # header + 1 row (วันนี้เท่านั้น)
        self.assertEqual(len(rows), 2)
        self.assertIn("งานวันนี้", rows[1])

    def test_csv_export_with_status_filter(self):
        """ทดสอบว่า CSV export กรองตาม status ถูกต้อง"""
        Task.objects.create(
            title="งานเสร็จ", task_date=self.today,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        Task.objects.create(
            title="งานไม่เสร็จ", task_date=self.today,
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )

        response = self.client.get(
            reverse("reports:csv-export") + f"?status={Task.Status.COMPLETED}"
        )
        content = response.content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        self.assertEqual(len(rows), 2)
        self.assertIn("งานเสร็จ", rows[1])

    def test_employee_blocked(self):
        """ทดสอบว่า employee เข้า CSV export ไม่ได้"""
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(reverse("reports:csv-export"))
        self.assertEqual(response.status_code, 403)


class ReportTimezoneTest(TestCase):
    """ทดสอบ timezone ใน reports"""

    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            userid="manager",
            email="manager@test.com",
            password="testpass123",
            is_staff=True,
        )
        EmployeeProfile.objects.create(user=self.manager)
        self.client.login(userid="manager", password="testpass123")

    def test_timezone_setting(self):
        """ทดสอบว่า TIME_ZONE ตั้งค่าถูกต้อง"""
        from django.conf import settings
        self.assertEqual(settings.TIME_ZONE, "Asia/Bangkok")
        self.assertTrue(settings.USE_TZ)

    def test_today_date_uses_bangkok_time(self):
        """ทดสอบว่า today date ใช้ Bangkok time"""
        today = today_local()
        response = self.client.get(reverse("reports:daily"))
        self.assertEqual(response.context["target_date"], today)
