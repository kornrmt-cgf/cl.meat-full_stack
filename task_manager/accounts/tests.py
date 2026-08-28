"""
Tests สำหรับ accounts app

ทดสอบ:
- User model (create, email, display_name, initials)
- Role model
- Team model
- EmployeeProfile model
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import EmployeeProfile, Role, Team

User = get_user_model()


class UserModelTest(TestCase):
    """ทดสอบ Custom User model"""

    def setUp(self):
        self.user = User.objects.create_user(
            userid="test",
            email="test@example.com",
            password="testpass123",
            first_name="สมชาย",
            last_name="ใจดี",
        )

    def test_create_user(self):
        """ทดสอบสร้าง user"""
        self.assertEqual(self.user.email, "test@example.com")
        self.assertEqual(self.user.first_name, "สมชาย")
        self.assertEqual(self.user.last_name, "ใจดี")
        self.assertTrue(self.user.check_password("testpass123"))
        self.assertTrue(self.user.is_active)
        self.assertFalse(self.user.is_staff)

    def test_create_superuser(self):
        """ทดสอบสร้าง superuser"""
        admin = User.objects.create_superuser(
            email="admin@example.com",
            password="adminpass123",
        )
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_active)

    def test_user_str(self):
        """ทดสอบ __str__"""
        self.assertEqual(str(self.user), "สมชาย ใจดี")

    def test_user_str_no_name(self):
        """ทดสอบ __str__ เมื่อไม่มีชื่อ"""
        user = User.objects.create_user(
            userid="noname",
            email="noname@example.com",
            password="testpass123",
        )
        self.assertEqual(str(user), "noname")

    def test_user_display_name(self):
        """ทดสอบ display_name property"""
        self.assertEqual(self.user.display_name, "สมชาย ใจดี")

    def test_user_initials(self):
        """ทดสอบ initials property (ตัวอักษรย่อจาก first_name + last_name)"""
        self.assertEqual(self.user.initials, "สใ")  # สมชาย -> ส + ใจ -> ใ (char at index 0)

    def test_user_initials_full_name(self):
        """ทดสอบ initials property เมื่อมีชื่อเต็ม"""
        user = User.objects.create_user(
            userid="test2",
            email="test2@example.com",
            password="testpass123",
            first_name="สมหญิง",
            last_name="รักงาน",
        )
        self.assertEqual(user.initials, "สร")

    def test_email_required(self):
        """ทดสอบว่า email จำเป็นต้องมี"""
        with self.assertRaises(ValueError):
            User.objects.create_user(email=None, password="testpass123")

    def test_unique_email(self):
        """ทดสอบ email ต้องไม่ซ้ำ"""
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
            userid="test",
            email="test@example.com",  # ซ้ำ
                password="testpass123",
            )

    def test_username_not_used(self):
        """ทดสอบว่าไม่ใช้ username"""
        self.assertEqual(User.USERNAME_FIELD, "userid")

    def test_user_ordering(self):
        """ทดสอบการจัดเรียง user"""
        user2 = User.objects.create_user(
            userid="test2",
            email="test2@example.com",
            password="testpass123",
        )
        users = list(User.objects.all())
        # เรียงตาม date_joined ล่าสุดก่อน
        self.assertEqual(users[0], user2)
        self.assertEqual(users[1], self.user)


class RoleModelTest(TestCase):
    """ทดสอบ Role model"""

    def setUp(self):
        self.role = Role.objects.create(
            name="Manager",
            slug="manager",
            description="ผู้จัดการ",
            color="#6366f1",
        )

    def test_create_role(self):
        """ทดสอบสร้าง role"""
        self.assertEqual(self.role.name, "Manager")
        self.assertEqual(self.role.slug, "manager")
        self.assertEqual(self.role.color, "#6366f1")
        self.assertTrue(self.role.is_active)

    def test_role_str(self):
        """ทดสอบ __str__"""
        self.assertEqual(str(self.role), "Manager")

    def test_unique_role_name(self):
        """ทดสอบชื่อ role ต้องไม่ซ้ำ"""
        with self.assertRaises(IntegrityError):
            Role.objects.create(
                name="Manager",  # ซ้ำ
                slug="manager2",
            )


class TeamModelTest(TestCase):
    """ทดสอบ Team model"""

    def setUp(self):
        self.leader = User.objects.create_user(
            userid="leader",
            email="leader@example.com",
            password="testpass123",
        )
        self.team = Team.objects.create(
            name="Production",
            slug="production",
            description="ทีมผลิต",
            leader=self.leader,
        )

    def test_create_team(self):
        """ทดสอบสร้าง team"""
        self.assertEqual(self.team.name, "Production")
        self.assertEqual(self.team.leader, self.leader)
        self.assertTrue(self.team.is_active)

    def test_team_str(self):
        """ทดสอบ __str__"""
        self.assertEqual(str(self.team), "Production")

    def test_team_leader_optional(self):
        """ทดสอบ leader ไม่บังคับ"""
        team = Team.objects.create(
            name="Warehouse",
            slug="warehouse",
        )
        self.assertIsNone(team.leader)


class EmployeeProfileTest(TestCase):
    """ทดสอบ EmployeeProfile model"""

    def setUp(self):
        self.user = User.objects.create_user(
            userid="emp",
            email="emp@example.com",
            password="testpass123",
            first_name="สมชาย",
        )
        self.role = Role.objects.create(
            name="Staff",
            slug="staff",
        )
        self.team = Team.objects.create(
            name="Production",
            slug="production",
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            role=self.role,
            team=self.team,
            position="พนักงานผลิต",
        )

    def test_create_profile(self):
        """ทดสอบสร้าง profile"""
        self.assertEqual(self.profile.user, self.user)
        self.assertEqual(self.profile.role, self.role)
        self.assertEqual(self.profile.team, self.team)
        self.assertEqual(self.profile.status, EmployeeProfile.Status.ACTIVE)

    def test_auto_employee_id(self):
        """ทดสอบ auto-generate employee_id"""
        self.assertIsNotNone(self.profile.employee_id)
        self.assertTrue(self.profile.employee_id.startswith("EMP"))

    def test_profile_str(self):
        """ทดสอบ __str__"""
        self.assertEqual(str(self.profile), f"สมชาย ({self.profile.employee_id})")

    def test_default_theme(self):
        """ทดสอบค่า theme เริ่มต้น"""
        self.assertEqual(self.profile.theme, "dark")
        self.assertTrue(self.profile.notification_enabled)
        self.assertTrue(self.profile.sound_enabled)


class AuthenticationTest(TestCase):
    """ทดสอบ Authentication"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            userid="test",
            email="test@example.com",
            password="testpass123",
        )

    def test_login_page(self):
        """ทดสอบหน้า login"""
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)

    def test_login_success(self):
        """ทดสอบ login สำเร็จ"""
        response = self.client.post(
            reverse("accounts:login"),
            {
                "userid": "test",
                "password": "testpass123",
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_login_wrong_password(self):
        """ทดสอบ login รหัสผ่านผิด"""
        response = self.client.post(
            reverse("accounts:login"),
            {
                "userid": "test",
                "password": "wrongpass",
            },
        )
        self.assertEqual(response.status_code, 200)  # แสดง form ใหม่

    def test_logout(self):
        """ทดสอบ logout"""
        self.client.login(userid="test", password="testpass123")
        response = self.client.get(reverse("accounts:logout"))
        self.assertEqual(response.status_code, 302)

    def test_profile_view(self):
        """ทดสอบหน้าโปรไฟล์"""
        self.client.login(userid="test", password="testpass123")
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 200)
