from core.utils import today_local
"""
Management command สำหรับ Task Automation

ทำงาน:
1. สร้าง recurring tasks
2. สร้าง upcoming reminders
3. ตรวจจับ overdue tasks
4. ตรวจสอบ dependency blocks

ปลอดภัยที่จะรันซ้ำ (idempotent)

Usage:
    python manage.py process_task_automation
    python manage.py process_task_automation --dry-run
"""

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "ประมวลผล task automation: recurring tasks, reminders, overdue detection"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="แสดงผลลัพธ์โดยไม่สร้าง notification จริง",
        )
        parser.add_argument(
            "--skip-recurring",
            action="store_true",
            help="ข้ามการสร้าง recurring tasks",
        )
        parser.add_argument(
            "--skip-reminders",
            action="store_true",
            help="ข้ามการสร้าง upcoming reminders",
        )
        parser.add_argument(
            "--skip-overdue",
            action="store_true",
            help="ข้ามการตรวจจับ overdue tasks",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()

        self.stdout.write(
            self.style.SUCCESS(f"🚀 เริ่มประมวลผล task automation - {now.strftime('%d/%m/%Y %H:%M')}")
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  DRY RUN MODE - ไม่สร้างข้อมูลจริง"))

        results = {
            "recurring_tasks": 0,
            "reminders": 0,
            "overdue_tasks": 0,
            "dependency_blocks": 0,
        }

        # 1. สร้าง recurring tasks
        if not options["skip_recurring"]:
            results["recurring_tasks"] = self._generate_recurring_tasks(dry_run)

        # 2. สร้าง upcoming reminders
        if not options["skip_reminders"]:
            results["reminders"] = self._generate_reminders(dry_run)

        # 3. ตรวจจับ overdue tasks
        if not options["skip_overdue"]:
            results["overdue_tasks"] = self._detect_overdue(dry_run)

        # 4. ตรวจสอบ dependency blocks
        results["dependency_blocks"] = self._check_dependencies()

        # สรุปผล
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("📊 สรุปผลลัพธ์:"))
        self.stdout.write(f"  📋 Recurring tasks สร้างใหม่: {results['recurring_tasks']}")
        self.stdout.write(f"  🔔 Upcoming reminders: {results['reminders']}")
        self.stdout.write(f"  ⚠️  Overdue tasks พบ: {results['overdue_tasks']}")
        self.stdout.write(f"  🔒 Dependency blocks: {results['dependency_blocks']}")
        self.stdout.write(self.style.SUCCESS("✅ เสร็จสิ้นการประมวลผล"))

    def _generate_recurring_tasks(self, dry_run):
        """สร้าง recurring tasks"""
        from scheduling.services import SchedulingService

        today = today_local()

        if dry_run:
            # นับจำนวน templates ที่จะสร้าง
            from tasks.models import TaskTemplate
            templates = TaskTemplate.objects.filter(
                is_active=True,
            ).exclude(recurrence_type=TaskTemplate.RecurrenceType.NONE)

            count = 0
            for template in templates:
                should_create = False
                if template.recurrence_type == TaskTemplate.RecurrenceType.DAILY:
                    should_create = True
                elif template.recurrence_type == TaskTemplate.RecurrenceType.WEEKDAYS:
                    if today.weekday() < 5:
                        should_create = True
                elif template.recurrence_type == TaskTemplate.RecurrenceType.WEEKLY:
                    if template.created_at and today.weekday() == template.created_at.date().weekday():
                        should_create = True

                if should_create:
                    # ตรวจสอบ duplicate
                    recurrence_id = f"{template.pk}_{today.isoformat()}"
                    from tasks.models import Task
                    if not Task.objects.filter(recurrence_id=recurrence_id).exists():
                        count += 1
                        self.stdout.write(f"  📋 จะสร้าง: {template.name} สำหรับ {today}")

            return count
        else:
            tasks = SchedulingService.generate_recurring_tasks(today)
            for task in tasks:
                self.stdout.write(f"  📋 สร้างงาน: {task.title} สำหรับ {today}")
            return len(tasks)

    def _generate_reminders(self, dry_run):
        """สร้าง upcoming reminders"""
        from notifications.services import NotificationService

        if dry_run:
            # นับจำนวน assignments ที่จะได้รับ reminder
            now = timezone.now()
            reminder_window = now + timezone.timedelta(minutes=15)

            from tasks.models import Task
            upcoming = Task.objects.filter(
                start_at__gt=now,
                start_at__lte=reminder_window,
                status__in=[
                    Task.Status.SCHEDULED,
                    Task.Status.READY,
                    Task.Status.ACCEPTED,
                ],
            ).prefetch_related("assignments")

            count = sum(t.assignments.count() for t in upcoming)
            for task in upcoming:
                for assignment in task.assignments.all():
                    self.stdout.write(
                        f"  🔔 จะแจ้งเตือน: {assignment.assigned_to.display_name} - {task.title}"
                    )
            return count
        else:
            notifications = NotificationService.generate_upcoming_reminders()
            for notif in notifications:
                self.stdout.write(f"  🔔 สร้างแจ้งเตือน: {notif.title}")
            return len(notifications)

    def _detect_overdue(self, dry_run):
        """ตรวจจับ overdue tasks"""
        from notifications.services import NotificationService

        if dry_run:
            from tasks.models import Task
            now = timezone.now()
            today = now.date()

            overdue = Task.objects.filter(
                deadline__lt=now,
                status__in=[
                    Task.Status.SCHEDULED,
                    Task.Status.READY,
                    Task.Status.ACCEPTED,
                    Task.Status.IN_PROGRESS,
                    Task.Status.PROBLEM,
                    Task.Status.ERROR,
                    Task.Status.POSTPONED,
                ],
            ).exclude(
                notifications__notification_type="task_overdue",
                notifications__created_at__date=today,
            ).distinct()

            count = overdue.count()
            for task in overdue:
                self.stdout.write(f"  ⚠️  เกินกำหนด: {task.title}")
            return count
        else:
            tasks = NotificationService.detect_overdue_tasks()
            for task in tasks:
                self.stdout.write(f"  ⚠️  แจ้งเตือน overdue: {task.title}")
            return len(tasks)

    def _check_dependencies(self):
        """ตรวจสอบ dependency blocks"""
        from notifications.services import NotificationService

        blocked = NotificationService.check_dependency_blocks()
        for item in blocked:
            self.stdout.write(
                f"  🔒 {item['task'].title} รอ {item['blocked_by'].title}"
            )
        return len(blocked)
