"""
Management command สำหรับสร้างข้อมูลทดสอบ

Usage:
    python manage.py seed_data
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import EmployeeProfile, Role, Team
from tasks.models import Task, TaskActivity, TaskAssignment

User = get_user_model()


class Command(BaseCommand):
    help = "สร้างข้อมูลทดสอบสำหรับ Freebuff Desktop"

    def handle(self, *args, **kwargs):
        self.stdout.write("🌱 กำลังสร้างข้อมูลทดสอบ...")

        # === สร้าง Roles ===
        roles = {
            "manager": Role.objects.create(
                name="Manager",
                slug="manager",
                description="ผู้จัดการ",
                color="#6366f1",
                icon="crown",
            ),
            "staff": Role.objects.create(
                name="Staff",
                slug="staff",
                description="พนักงาน",
                color="#10b981",
                icon="user",
            ),
            "driver": Role.objects.create(
                name="Driver",
                slug="driver",
                description="พนักงานขับรถ",
                color="#f59e0b",
                icon="truck",
            ),
            "warehouse": Role.objects.create(
                name="Warehouse",
                slug="warehouse",
                description="พนักงานคลังสินค้า",
                color="#8b5cf6",
                icon="warehouse",
            ),
        }
        self.stdout.write(f"  ✅ สร้าง Roles: {len(roles)} รายการ")

        # === สร้าง Teams ===
        teams = {
            "production": Team.objects.create(
                name="Production",
                slug="production",
                description="ทีมผลิต",
                color="#10b981",
            ),
            "warehouse": Team.objects.create(
                name="Warehouse",
                slug="warehouse",
                description="ทีมคลังสินค้า",
                color="#8b5cf6",
            ),
            "delivery": Team.objects.create(
                name="Delivery",
                slug="delivery",
                description="ทีมจัดส่ง",
                color="#f59e0b",
            ),
        }
        self.stdout.write(f"  ✅ สร้าง Teams: {len(teams)} รายการ")

        # === สร้าง Admin User ===
        admin = User.objects.create_user(
            email="admin@freebuff.com",
            password="admin1234",
            first_name="Admin",
            last_name="User",
            is_staff=True,
            is_superuser=True,
        )
        EmployeeProfile.objects.create(
            user=admin,
            role=roles["manager"],
            team=teams["production"],
            position="ผู้จัดการ",
        )
        self.stdout.write(f"  ✅ สร้าง Admin: {admin.email}")

        # === สร้าง Staff Users ===
        staff_users = []
        staff_data = [
            ("somchai@freebuff.com", "สมชาย", "ใจดี", "staff", "production", "พนักงานผลิต"),
            ("somying@freebuff.com", "สมหญิง", "รักงาน", "staff", "production", "พนักงานผลิต"),
            ("prawet@freebuff.com", "ประวัติ", "ขับรถ", "driver", "delivery", "พนักงานขับรถ"),
            ("napa@freebuff.com", "นภา", "จัดของ", "warehouse", "warehouse", "พนักงานคลัง"),
        ]

        for email, first, last, role_key, team_key, position in staff_data:
            user = User.objects.create_user(
                email=email,
                password="password123",
                first_name=first,
                last_name=last,
            )
            EmployeeProfile.objects.create(
                user=user,
                role=roles[role_key],
                team=teams[team_key],
                position=position,
            )
            staff_users.append(user)
            self.stdout.write(f"  ✅ สร้าง User: {email}")

        # === สร้าง Tasks ===
        now = timezone.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        tasks_data = [
            {
                "title": "เช็คสต็อกเนื้อแช่แข็ง",
                "description": "ตรวจนับเนื้อแช่แข็งทุกประเภทในห้องเย็น",
                "category": "warehouse",
                "priority": 3,
                "status": Task.Status.IN_PROGRESS,
                "deadline": today + timedelta(hours=10),
                "assign_to": [staff_users[3]],
                "created_by": admin,
            },
            {
                "title": "เตรียมเนื้อสำหรับผลิตวันนี้",
                "description": "นำเนื้อออกจากห้องเย็นเพื่อเตรียมผลิต",
                "category": "production",
                "priority": 4,
                "status": Task.Status.READY,
                "deadline": today + timedelta(hours=8),
                "assign_to": [staff_users[0], staff_users[1]],
                "created_by": admin,
            },
            {
                "title": "จัดส่งออเดอร์ #1234",
                "description": "จัดส่งเนื้อไปที่ร้าน ABC",
                "category": "delivery",
                "priority": 3,
                "status": Task.Status.SCHEDULED,
                "deadline": today + timedelta(hours=14),
                "assign_to": [staff_users[2]],
                "created_by": admin,
            },
            {
                "title": "ทำความสะอาดเครื่องจักร",
                "description": "ล้างทำความสะอาดเครื่องหั่นเนื้อ",
                "category": "cleaning",
                "priority": 2,
                "status": Task.Status.SCHEDULED,
                "deadline": today + timedelta(hours=16),
                "assign_to": [staff_users[0]],
                "created_by": admin,
            },
            {
                "title": "ตรวจอุณหภูมิห้องเย็น",
                "description": "บันทึกอุณหภูมิห้องเย็นทุก 2 ชั่วโมง",
                "category": "warehouse",
                "priority": 2,
                "status": Task.Status.PROBLEM,
                "deadline": today + timedelta(hours=12),
                "assign_to": [staff_users[3]],
                "created_by": admin,
                "notes": "เทอร์โมมิเตอร์ตัวที่ 2 ชำรุด",
            },
            {
                "title": "รายงานยอดขายประจำวัน",
                "description": "สรุปยอดขายส่งผู้จัดการ",
                "category": "admin",
                "priority": 2,
                "status": Task.Status.COMPLETED,
                "deadline": today + timedelta(hours=18),
                "completed_at": now - timedelta(hours=1),
                "assign_to": [staff_users[1]],
                "created_by": admin,
            },
        ]

        for task_data in tasks_data:
            assign_to = task_data.pop("assign_to")
            task = Task.objects.create(**task_data)

            for i, user in enumerate(assign_to):
                TaskAssignment.objects.create(
                    task=task,
                    assigned_to=user,
                    assigned_by=admin,
                    is_primary=(i == 0),
                )

            TaskActivity.objects.create(
                task=task,
                user=admin,
                action=TaskActivity.Action.CREATED,
                new_status=task.status,
                description=f"สร้างงาน: {task.title}",
            )

        self.stdout.write(f"  ✅ สร้าง Tasks: {len(tasks_data)} รายการ")

        # === สร้าง Tomorrow Tasks ===
        tomorrow = today + timedelta(days=1)
        tomorrow_tasks = [
            {
                "title": "ตรวจนับสต็อกปลายเดือน",
                "description": "ตรวจนับสินค้าทั้งหมดในคลัง",
                "category": "warehouse",
                "priority": 3,
                "status": Task.Status.SCHEDULED,
                "deadline": tomorrow + timedelta(hours=10),
                "assign_to": [staff_users[3]],
                "created_by": admin,
            },
            {
                "title": "ฝึกอบรมพนักงานใหม่",
                "description": "อบรมเรื่องความปลอดภัยในการทำงาน",
                "category": "admin",
                "priority": 2,
                "status": Task.Status.SCHEDULED,
                "deadline": tomorrow + timedelta(hours=14),
                "assign_to": [staff_users[0], staff_users[1]],
                "created_by": admin,
            },
        ]

        for task_data in tomorrow_tasks:
            assign_to = task_data.pop("assign_to")
            task = Task.objects.create(**task_data)

            for i, user in enumerate(assign_to):
                TaskAssignment.objects.create(
                    task=task,
                    assigned_to=user,
                    assigned_by=admin,
                    is_primary=(i == 0),
                )

        self.stdout.write(f"  ✅ สร้าง Tomorrow Tasks: {len(tomorrow_tasks)} รายการ")

        self.stdout.write(self.style.SUCCESS("\n🎉 สร้างข้อมูลทดสอบสำเร็จ!"))
        self.stdout.write(f"\n📧 Admin: admin@freebuff.com")
        self.stdout.write(f"🔑 Password: admin1234")
        self.stdout.write(f"\n📧 Staff: somchai@freebuff.com")
        self.stdout.write(f"🔑 Password: password123")
