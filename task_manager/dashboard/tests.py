"""
Tests สำหรับ dashboard app

ทดสอบ:
- Dashboard view (redirect สำหรับ employee/manager)
- Manager Dashboard: task counts, date filtering, employee workload
- Employee Workload view
- Employee Detail view
- Team Overview view
- Permissions: employee ถูกบล็อก, manager เข้าได้
- Overdue detection
- Timezone correctness
"""

from datetime import timedelta

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from core.utils import today_local

from accounts.models import EmployeeProfile, Team
from tasks.models import Task, TaskAssignment, TaskReport

User = get_user_model()


class DashboardRedirectTest(TestCase):
    """ทดสอบ redirect ของ DashboardView"""

    def setUp(self):
        self.client = Client()
        self.employee = User.objects.create_user(
            userid="employee",
            email="employee@test.com",
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
        EmployeeProfile.objects.create(user=self.employee)
        EmployeeProfile.objects.create(user=self.manager)

    def test_employee_redirects_to_today(self):
        """ทดสอบว่า employee redirect ไป TodayView"""
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("tasks:today"), fetch_redirect_response=False)

    def test_manager_redirects_to_manager_dashboard(self):
        """ทดสอบว่า manager redirect ไป ManagerDashboard"""
        self.client.login(userid="manager", password="testpass123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:manager"), fetch_redirect_response=False)

    def test_unauthenticated_redirects_to_login(self):
        """ทดสอบว่า user ที่ไม่ login redirect ไปหน้า login"""
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)


class ManagerDashboardViewTest(TestCase):
    """ทดสอบ ManagerDashboardView"""

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
        self.team = Team.objects.create(name="ทีม A")
        EmployeeProfile.objects.create(user=self.manager)
        EmployeeProfile.objects.create(user=self.employee, team=self.team)

        self.client.login(userid="manager", password="testpass123")
        self.today = today_local()

    def test_manager_dashboard_view(self):
        """ทดสอบว่า manager เข้า dashboard ได้"""
        response = self.client.get(reverse("dashboard:manager"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("total", response.context)
        self.assertIn("status_counts", response.context)

    def test_dashboard_task_counts(self):
        """ทดสอบว่า dashboard นับ task ถูกต้อง"""
        # สร้าง tasks
        Task.objects.create(
            title="งาน 1",
            task_date=self.today,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        Task.objects.create(
            title="งาน 2",
            task_date=self.today,
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        Task.objects.create(
            title="งาน 3",
            task_date=self.today,
            status=Task.Status.PROBLEM,
            created_by=self.manager,
        )

        response = self.client.get(reverse("dashboard:manager"))
        self.assertEqual(response.context["total"], 3)
        self.assertEqual(response.context["status_counts"][Task.Status.COMPLETED], 1)
        self.assertEqual(response.context["status_counts"][Task.Status.IN_PROGRESS], 1)
        self.assertEqual(response.context["status_counts"][Task.Status.PROBLEM], 1)

    def test_dashboard_date_filtering(self):
        """ทดสอบว่า dashboard กรองตามวันที่ถูกต้อง"""
        yesterday = self.today - timedelta(days=1)
        tomorrow = self.today + timedelta(days=1)

        Task.objects.create(
            title="งานวันวาน",
            task_date=yesterday,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        Task.objects.create(
            title="งานวันนี้",
            task_date=self.today,
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        Task.objects.create(
            title="งานพรุ่งนี้",
            task_date=tomorrow,
            status=Task.Status.SCHEDULED,
            created_by=self.manager,
        )

        # default = วันนี้
        response = self.client.get(reverse("dashboard:manager"))
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["target_date"], self.today)

        # เลือกวันวาน
        response = self.client.get(reverse("dashboard:manager") + f"?date={yesterday}")
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["target_date"], yesterday)

    def test_dashboard_overdue_detection(self):
        """ทดสอบว่า dashboard ตรวจจับ overdue ได้ถูกต้อง"""
        deadline = timezone.now() - timedelta(hours=1)

        # overdue task
        Task.objects.create(
            title="งานเกินกำหนด",
            task_date=self.today,
            deadline=deadline,
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        # completed task (ไม่ considered overdue)
        Task.objects.create(
            title="งานเสร็จแล้ว",
            task_date=self.today,
            deadline=deadline,
            status=Task.Status.COMPLETED,
            completed_at=deadline + timedelta(hours=2),
            created_by=self.manager,
        )

        response = self.client.get(reverse("dashboard:manager"))
        self.assertEqual(response.context["overdue"], 1)

    def test_dashboard_employee_workload(self):
        """ทดสอบว่า dashboard มีข้อมูล workload ของ employee"""
        # มอบหมายงาน
        task = Task.objects.create(
            title="งาน A",
            task_date=self.today,
            status=Task.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.employee,
            assigned_by=self.manager,
        )

        response = self.client.get(reverse("dashboard:manager"))
        workload = response.context["employee_workload"]
        self.assertTrue(len(workload) > 0)
        self.assertEqual(workload[0]["user"], self.employee)
        self.assertEqual(workload[0]["assigned"], 1)

    def test_dashboard_team_overview(self):
        """ทดสอบว่า dashboard มีข้อมูล team overview"""
        task = Task.objects.create(
            title="งานทีม",
            task_date=self.today,
            status=Task.Status.IN_PROGRESS,
            team=self.team,
            created_by=self.manager,
        )

        response = self.client.get(reverse("dashboard:manager"))
        team_overview = response.context["team_overview"]
        self.assertTrue(len(team_overview) > 0)
        team_a = [t for t in team_overview if t["team"] == self.team]
        self.assertTrue(len(team_a) > 0)

    def test_dashboard_date_navigation(self):
        """ทดสอบว่า dashboard มีลิงก์ prev/next date"""
        response = self.client.get(reverse("dashboard:manager"))
        self.assertIn("prev_date", response.context)
        self.assertIn("next_date", response.context)
        self.assertEqual(response.context["prev_date"], self.today - timedelta(days=1))
        self.assertEqual(response.context["next_date"], self.today + timedelta(days=1))

    def test_employee_blocked_from_dashboard(self):
        """ทดสอบว่า employee เข้า management dashboard ไม่ได้"""
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(reverse("dashboard:manager"))
        self.assertEqual(response.status_code, 403)


class EmployeeWorkloadViewTest(TestCase):
    """ทดสอบ EmployeeWorkloadView"""

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
        self.today = today_local()

    def test_workload_view(self):
        """ทดสอบว่า employee workload view ทำงานได้"""
        response = self.client.get(reverse("dashboard:employee-workload"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("employee_workload", response.context)

    def test_employee_blocked(self):
        """ทดสอบว่า employee เข้า workload view ไม่ได้"""
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(reverse("dashboard:employee-workload"))
        self.assertEqual(response.status_code, 403)


class EmployeeDetailViewTest(TestCase):
    """ทดสอบ EmployeeDetailView"""

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
        self.today = today_local()

    def test_employee_detail_view(self):
        """ทดสอบว่า employee detail view ทำงานได้"""
        response = self.client.get(
            reverse("dashboard:employee-detail", args=[self.employee.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("total", response.context)
        self.assertIn("completed", response.context)

    def test_employee_detail_with_tasks(self):
        """ทดสอบ employee detail พร้อม tasks"""
        task = Task.objects.create(
            title="งาน A",
            task_date=self.today,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.employee,
            assigned_by=self.manager,
        )

        response = self.client.get(
            reverse("dashboard:employee-detail", args=[self.employee.pk])
        )
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["completed"], 1)

    def test_employee_detail_with_date_range(self):
        """ทดสอบ employee detail พร้อม date range"""
        yesterday = self.today - timedelta(days=1)

        task = Task.objects.create(
            title="งานวันวาน",
            task_date=yesterday,
            status=Task.Status.COMPLETED,
            created_by=self.manager,
        )
        TaskAssignment.objects.create(
            task=task,
            assigned_to=self.employee,
            assigned_by=self.manager,
        )

        response = self.client.get(
            reverse("dashboard:employee-detail", args=[self.employee.pk])
            + f"?date_from={yesterday}&date_to={yesterday}"
        )
        self.assertEqual(response.context["total"], 1)

    def test_employee_blocked(self):
        """ทดสอบว่า employee เข้า employee detail ไม่ได้"""
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(
            reverse("dashboard:employee-detail", args=[self.employee.pk])
        )
        self.assertEqual(response.status_code, 403)


class TeamOverviewViewTest(TestCase):
    """ทดสอบ TeamOverviewView"""

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
        self.team = Team.objects.create(name="ทีม A")
        EmployeeProfile.objects.create(user=self.manager)

        self.client.login(userid="manager", password="testpass123")
        self.today = today_local()

    def test_team_overview_view(self):
        """ทดสอบว่า team overview view ทำงานได้"""
        response = self.client.get(reverse("dashboard:team-overview"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("team_overview", response.context)

    def test_team_overview_with_tasks(self):
        """ทดสอบ team overview พร้อม tasks"""
        Task.objects.create(
            title="งานทีม",
            task_date=self.today,
            status=Task.Status.IN_PROGRESS,
            team=self.team,
            created_by=self.manager,
        )

        response = self.client.get(reverse("dashboard:team-overview"))
        overview = response.context["team_overview"]
        team_data = [t for t in overview if t["team"] == self.team]
        self.assertTrue(len(team_data) > 0)
        self.assertEqual(team_data[0]["in_progress"], 1)

    def test_employee_blocked(self):
        """ทดสอบว่า employee เข้า team overview ไม่ได้"""
        employee = User.objects.create_user(
            userid="employee",
            email="employee@test.com",
            password="testpass123",
        )
        EmployeeProfile.objects.create(user=employee)
        self.client.login(userid="employee", password="testpass123")
        response = self.client.get(reverse("dashboard:team-overview"))
        self.assertEqual(response.status_code, 403)


class EmployeeWorkloadCountTest(TestCase):
    """ทดสอบ accuracy ของ workload counts"""

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
        self.employee_a = User.objects.create_user(
            userid="employee_a",
            email="employee_a@test.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="A",
        )
        self.employee_b = User.objects.create_user(
            userid="employee_b",
            email="employee_b@test.com",
            password="testpass123",
            first_name="สมหญิง",
            last_name="B",
        )
        EmployeeProfile.objects.create(user=self.manager)
        EmployeeProfile.objects.create(user=self.employee_a)
        EmployeeProfile.objects.create(user=self.employee_b)

        self.client.login(userid="manager", password="testpass123")
        self.today = today_local()

    def test_employee_workload_correct_counts(self):
        """ทดสอบว่า workload counts ถูกต้อง"""
        # employee_a: 3 tasks
        for i, status in enumerate([
            Task.Status.COMPLETED,
            Task.Status.IN_PROGRESS,
            Task.Status.PROBLEM,
        ]):
            task = Task.objects.create(
                title=f"งาน A-{i}",
                task_date=self.today,
                status=status,
                created_by=self.manager,
            )
            TaskAssignment.objects.create(
                task=task,
                assigned_to=self.employee_a,
                assigned_by=self.manager,
            )

        # employee_b: 2 tasks
        for i, status in enumerate([
            Task.Status.COMPLETED,
            Task.Status.SCHEDULED,
        ]):
            task = Task.objects.create(
                title=f"งาน B-{i}",
                task_date=self.today,
                status=status,
                created_by=self.manager,
            )
            TaskAssignment.objects.create(
                task=task,
                assigned_to=self.employee_b,
                assigned_by=self.manager,
            )

        response = self.client.get(reverse("dashboard:employee-workload"))
        workload = response.context["employee_workload"]

        # หาข้อมูล employee_a
        emp_a_data = next(w for w in workload if w["user"] == self.employee_a)
        self.assertEqual(emp_a_data["assigned"], 3)
        self.assertEqual(emp_a_data["completed"], 1)
        self.assertEqual(emp_a_data["in_progress"], 1)
        self.assertEqual(emp_a_data["problem"], 1)

        # หาข้อมูล employee_b
        emp_b_data = next(w for w in workload if w["user"] == self.employee_b)
        self.assertEqual(emp_b_data["assigned"], 2)
        self.assertEqual(emp_b_data["completed"], 1)
