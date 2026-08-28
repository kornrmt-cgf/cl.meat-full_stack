"""
NotificationService - Business logic สำหรับการจัดการแจ้งเตือน

จัดการ:
- สร้างแจ้งเตือน
- ตรวจสอบ duplicate
- ส่งแจ้งเตือนตาม event ต่างๆ
- Notification preferences
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Notification


class NotificationService:
    """
    จัดการ business logic ของ Notification
    ทุก method ป้องกัน duplicate อัตโนมัติ
    """

    # === Create Notification ===

    @staticmethod
    @transaction.atomic
    def create_notification(
        user,
        notification_type,
        title,
        message,
        task=None,
        check_duplicate=True,
    ):
        """
        สร้างแจ้งเตือนใหม่

        Args:
            user: ผู้รับ (User object)
            notification_type: ประเภทแจ้งเตือน
            title: หัวข้อ
            message: ข้อความ
            task: งานที่เกี่ยวข้อง (optional)
            check_duplicate: ตรวจสอบ duplicate ก่อนสร้าง

        Returns:
            Notification object หรือ None ถ้า duplicate
        """
        # ตรวจสอบ duplicate: type + task + user (ป้องกัน notification ซ้ำสำหรับ event เดียวกัน)
        if check_duplicate and task:
            existing = Notification.objects.filter(
                user=user,
                notification_type=notification_type,
                task=task,
            ).exists()
            if existing:
                return None

        # ตรวจสอบ preferences
        if not NotificationService._is_notification_enabled(user, notification_type):
            return None

        notification = Notification.objects.create(
            user=user,
            notification_type=notification_type,
            title=title,
            message=message,
            task=task,
        )

        return notification

    # === Task Event Notifications ===

    @staticmethod
    def notify_task_assigned(task, assigned_user, assigned_by=None):
        """
        แจ้งเตือนเมื่อมอบหมายงาน

        Args:
            task: Task object
            assigned_user: ผู้ได้รับมอบหมาย
            assigned_by: ผู้มอบหมาย
        """
        assigner_name = assigned_by.display_name if assigned_by else "ระบบ"
        schedule_info = ""
        if task.start_at:
            schedule_info = f"\n⏱ เริ่มงาน: {task.start_at.strftime('%H:%M')}"
        if task.deadline:
            schedule_info += f"\n⏰ กำหนดส่ง: {task.deadline.strftime('%H:%M')}"

        priority_text = task.get_priority_display()

        return NotificationService.create_notification(
            user=assigned_user,
            notification_type=Notification.Type.TASK_ASSIGNED,
            title=f"📋 ได้รับมอบหมายงานใหม่: {task.title}",
            message=(
                f"คุณได้รับมอบหมายงานใหม่จาก {assigner_name}\n"
                f"📌 {task.title}\n"
                f"⭐ ความสำคัญ: {priority_text}"
                f"{schedule_info}"
            ),
            task=task,
        )

    @staticmethod
    def notify_task_starting(task, user):
        """
        แจ้งเตือนเมื่องานกำลังจะเริ่ม (15 นาทีก่อน)

        Args:
            task: Task object
            user: ผู้รับ
        """
        start_time = task.start_at.strftime("%H:%M") if task.start_at else "ไม่ระบุ"

        return NotificationService.create_notification(
            user=user,
            notification_type=Notification.Type.TASK_STARTING,
            title=f"🔔 งาน '{task.title}' จะเริ่มในอีก 15 นาที",
            message=(
                f"⏰ งาน '{task.title}' จะเริ่มเวลา {start_time}\n"
                f"📌 กรุณาเตรียมตัว"
            ),
            task=task,
        )

    @staticmethod
    def notify_task_overdue(task, users=None):
        """
        แจ้งเตือนเมื่องานเกินกำหนด

        Args:
            task: Task object
            users: list ของ users ที่ต้องการแจ้ง (default = assignees + managers)
        """
        if users is None:
            users = []
            # ดึง assignees
            for assignment in task.assignments.select_related("assigned_to").all():
                users.append(assignment.assigned_to)
            # ดึง managers
            from tasks.permissions import is_manager
            from django.contrib.auth import get_user_model
            User = get_user_model()
            managers = User.objects.filter(is_staff=True).exclude(pk__in=[u.pk for u in users])
            users.extend(managers)

        deadline_str = task.deadline.strftime("%d/%m %H:%M") if task.deadline else "ไม่ระบุ"

        for user in users:
            NotificationService.create_notification(
                user=user,
                notification_type=Notification.Type.TASK_OVERDUE,
                title=f"⚠️ งานเกินกำหนด: {task.title}",
                message=(
                    f"งาน '{task.title}' เกินกำหนดส่งแล้ว\n"
                    f"⏰ กำหนดส่ง: {deadline_str}\n"
                    f"📊 สถานะ: {task.get_status_display()}"
                ),
                task=task,
            )

    @staticmethod
    def notify_task_rescheduled(task, user, changes_desc):
        """
        แจ้งเตือนเมื่อเปลี่ยนเวลางาน

        Args:
            task: Task object
            user: ผู้รับ
            changes_desc: รายละเอียดการเปลี่ยนแปลง
        """
        return NotificationService.create_notification(
            user=user,
            notification_type=Notification.Type.TASK_RESCHEDULED,
            title=f"📅 เปลี่ยนเวลางาน: {task.title}",
            message=(
                f"เวลาจัดตารางของงาน '{task.title}' ถูกเปลี่ยน\n"
                f"{changes_desc}"
            ),
            task=task,
        )

    @staticmethod
    def notify_task_postponed(task, user, reason=""):
        """
        แจ้งเตือนเมื่อเลื่อนงาน

        Args:
            task: Task object
            user: ผู้รับ
            reason: เหตุผล
        """
        new_date = task.task_date.strftime("%d/%m/%Y") if task.task_date else "ไม่ระบุ"
        reason_text = f"\n📝 เหตุผล: {reason}" if reason else ""

        return NotificationService.create_notification(
            user=user,
            notification_type=Notification.Type.TASK_POSTPONED,
            title=f"📆 เลื่อนงาน: {task.title}",
            message=(
                f"งาน '{task.title}' ถูกเลื่อน\n"
                f"📅 วันที่ใหม่: {new_date}"
                f"{reason_text}"
            ),
            task=task,
        )

    @staticmethod
    def notify_task_problem(task, reporter, manager=None):
        """
        แจ้งเตือนเมื่อมีการรายงานปัญหา

        Args:
            task: Task object
            reporter: ผู้รายงาน
            manager: หัวหน้า (ถ้าไม่ระบุ จะหาจาก task.created_by)
        """
        if manager is None:
            manager = task.created_by

        if manager and manager != reporter:
            NotificationService.create_notification(
                user=manager,
                notification_type=Notification.Type.TASK_PROBLEM,
                title=f"🔴 ปัญหาในงาน: {task.title}",
                message=(
                    f"มีการรายงานปัญหาในงาน '{task.title}'\n"
                    f"👤 ผู้รายงาน: {reporter.display_name}\n"
                    f"📊 สถานะ: {task.get_status_display()}"
                ),
                task=task,
            )

    @staticmethod
    def notify_task_error(task, reporter, manager=None):
        """
        แจ้งเตือนเมื่อมีการรายงานข้อผิดพลาด

        Args:
            task: Task object
            reporter: ผู้รายงาน
            manager: หัวหน้า (ถ้าไม่ระบุ จะหาจาก task.created_by)
        """
        if manager is None:
            manager = task.created_by

        if manager and manager != reporter:
            NotificationService.create_notification(
                user=manager,
                notification_type=Notification.Type.TASK_ERROR,
                title=f"❌ ข้อผิดพลาดในงาน: {task.title}",
                message=(
                    f"มีการรายงานข้อผิดพลาดในงาน '{task.title}'\n"
                    f"👤 ผู้รายงาน: {reporter.display_name}\n"
                    f"📊 สถานะ: {task.get_status_display()}"
                ),
                task=task,
            )

    @staticmethod
    def notify_new_open_task(task, employee):
        """
        แจ้งเตือนเมื่อมีงานเปิดรับใหม่

        Args:
            task: Task object (is_open=True)
            employee: ผู้รับแจ้งเตือน
        """
        reward_text = f"\n💰 ค่าตอบแทน: ฿{task.reward:,.2f}" if task.reward else ""
        schedule_info = ""
        if task.start_at:
            schedule_info = f"\n⏱ เริ่มงาน: {task.start_at.strftime('%H:%M')}"
        if task.deadline:
            schedule_info += f"\n⏰ กำหนดส่ง: {task.deadline.strftime('%H:%M')}"

        return NotificationService.create_notification(
            user=employee,
            notification_type=Notification.Type.TASK_OPEN,
            title=f"📢 งานเปิดรับใหม่: {task.title}",
            message=(
                f"มีงานเปิดรับใหม่!\n"
                f"📌 {task.title}\n"
                f"⭐ ความสำคัญ: {task.get_priority_display()}"
                f"{reward_text}"
                f"{schedule_info}\n"
                f"💡 กดแย่งงานได้เลย!"
            ),
            task=task,
        )

    @staticmethod
    def notify_task_claimed(task, claimed_by, manager=None):
        """
        แจ้งเตือนเมื่อมีคนแย่งงานสำเร็จ

        Args:
            task: Task object
            claimed_by: คนที่แย่งงานได้
            manager: หัวหน้า (default = task.created_by)
        """
        if manager is None:
            manager = task.created_by

        if manager and manager != claimed_by:
            return NotificationService.create_notification(
                user=manager,
                notification_type=Notification.Type.TASK_CLAIMED,
                title=f"🎉 มีคนรับงาน: {task.title}",
                message=(
                    (f"พนักงาน {claimed_by.display_name} แย่งงาน '{task.title}' สำเร็จ\n"
                     f"💰 ค่าตอบแทน: ฿{task.reward:,.2f}")
                    if task.reward
                    else f"พนักงาน {claimed_by.display_name} แย่งงาน '{task.title}' สำเร็จ"
                ),
                task=task,
            )
        return None

    @staticmethod
    def dismiss_open_task_notifications(task):
        """
        ลบ/ทำเครื่องหมายว่าอ่าน notification งานเปิดรับ เมื่อมีคนรับงานแล้ว

        Args:
            task: Task object (ที่เพิ่งมีคนรับ)
        """
        from django.utils import timezone as tz
        Notification.objects.filter(
            task=task,
            notification_type=Notification.Type.TASK_OPEN,
            is_read=False,
        ).update(is_read=True, read_at=tz.now())

    @staticmethod
    def notify_task_completed(task, user):
        """
        แจ้งเตือนเมื่องานเสร็จ

        Args:
            task: Task object
            user: ผู้รับ (manager)
        """
        return NotificationService.create_notification(
            user=user,
            notification_type=Notification.Type.TASK_COMPLETED,
            title=f"✅ งานเสร็จแล้ว: {task.title}",
            message=(
                f"งาน '{task.title}' เสร็จเรียบร้อย\n"
                f"👤 ผู้ทำ: {task.assignments.first().assigned_to.display_name if task.assignments.first() else 'N/A'}"
            ),
            task=task,
        )

    # === Query Methods ===

    @staticmethod
    def get_unread_count(user):
        """
        นับจำนวนแจ้งเตือนที่ยังไม่อ่าน

        Args:
            user: User object

        Returns:
            int
        """
        return Notification.objects.filter(
            user=user,
            is_read=False,
        ).count()

    @staticmethod
    def get_user_notifications(user, unread_only=False):
        """
        ดึงแจ้งเตือนของ user

        Args:
            user: User object
            unread_only: แสดงเฉพาะที่ยังไม่อ่าน

        Returns:
            QuerySet
        """
        queryset = Notification.objects.filter(user=user)
        if unread_only:
            queryset = queryset.filter(is_read=False)
        return queryset.select_related("task").order_by("-created_at")

    @staticmethod
    @transaction.atomic
    def mark_as_read(notification_id, user):
        """
        ทำเครื่องหมายว่าอ่านแล้ว

        Args:
            notification_id: รหัสแจ้งเตือน
            user: User object (ตรวจสอบ ownership)

        Returns:
            bool
        """
        try:
            notification = Notification.objects.get(
                pk=notification_id,
                user=user,
            )
            notification.mark_as_read()
            return True
        except Notification.DoesNotExist:
            return False

    @staticmethod
    @transaction.atomic
    def mark_all_as_read(user):
        """
        ทำเครื่องหมายว่าอ่านทั้งหมด

        Args:
            user: User object

        Returns:
            int (จำนวนที่ทำเครื่องหมาย)
        """
        now = timezone.now()
        count = Notification.objects.filter(
            user=user,
            is_read=False,
        ).update(is_read=True, read_at=now)
        return count

    # === Preferences ===

    @staticmethod
    def _is_notification_enabled(user, notification_type):
        """
        ตรวจสอบว่า user เปิดรับ notification ประเภทนี้หรือไม่

        Args:
            user: User object
            notification_type: ประเภทแจ้งเตือน

        Returns:
            bool
        """
        profile = getattr(user, "profile", None)
        if profile and not profile.notification_enabled:
            return False

        if profile:
            type_preferences = {
                Notification.Type.TASK_ASSIGNED: profile.notify_task_assigned,
                Notification.Type.TASK_STARTING: profile.notify_task_starting,
                Notification.Type.TASK_OVERDUE: profile.notify_task_overdue,
                Notification.Type.TASK_RESCHEDULED: profile.notify_task_rescheduled,
                Notification.Type.TASK_POSTPONED: profile.notify_task_rescheduled,
                Notification.Type.TASK_PROBLEM: profile.notify_task_problem,
                Notification.Type.TASK_ERROR: profile.notify_task_error,
                Notification.Type.TASK_COMPLETED: True,  # เสมอ
                Notification.Type.TASK_UPDATED: True,  # เสมอ
                Notification.Type.TASK_OPEN: True,  # งานเปิดรับ - ส่งเสมอ
                Notification.Type.TASK_CLAIMED: True,  # มีคนรับงาน - ส่งเสมอ
                Notification.Type.PROBLEM_REPORTED: profile.notify_task_problem,
                Notification.Type.SYSTEM: True,  # เสมอ
            }
            if notification_type in type_preferences:
                return type_preferences[notification_type]

        return True

    # === Automation Helpers ===

    @staticmethod
    def generate_upcoming_reminders():
        """
        สร้างแจ้งเตือนเตือนงานที่กำลังจะเริ่ม (15 นาทีก่อน)

        ต้อง:
        - start_at อยู่ในช่วง 15 นาทีข้างหน้า
        - สถานะ SCHEDULED หรือ READY หรือ ACCEPTED
        - ยังไม่มี notification ประเภท TASK_STARTING ในวันนี้

        Returns:
            list ของ Notification objects ที่สร้างใหม่
        """
        now = timezone.now()
        reminder_window = now + timezone.timedelta(minutes=15)

        # ดึง assignments ที่มีงานเริ่มใน 15 นาทีข้างหน้า
        from tasks.models import Task, TaskAssignment

        upcoming_tasks = Task.objects.filter(
            start_at__gt=now,
            start_at__lte=reminder_window,
            status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
            ],
        ).prefetch_related("assignments__assigned_to")

        created = []
        for task in upcoming_tasks:
            for assignment in task.assignments.select_related("assigned_to").all():
                notif = NotificationService.notify_task_starting(
                    task, assignment.assigned_to
                )
                if notif:
                    created.append(notif)

        return created

    @staticmethod
    def detect_overdue_tasks():
        """
        ตรวจจับงานที่เกินกำหนดและสร้างแจ้งเตือน

        Rules:
        - deadline < now
        - status ไม่ใช่ COMPLETED หรือ CANCELLED
        - ยังไม่มี notification TASK_OVERDUE สำหรับ task นี้ในวันนี้

        Returns:
            list ของ tasks ที่เกินกำหนด
        """
        now = timezone.now()
        today = now.date()

        from tasks.models import Task

        overdue_tasks = Task.objects.filter(
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
            # ยกเว้น task ที่มี notification แล้ววันนี้
            notifications__notification_type=Notification.Type.TASK_OVERDUE,
            notifications__created_at__date=today,
        ).distinct()

        notified_tasks = []
        for task in overdue_tasks:
            NotificationService.notify_task_overdue(task)
            notified_tasks.append(task)

        return notified_tasks

    @staticmethod
    def check_dependency_blocks():
        """
        ตรวจสอบงานที่ถูก block โดย dependency

        Returns:
            list ของ dicts {task, blocked_by}
        """
        from tasks.models import Task, TaskDependency

        blocked = []
        active_statuses = [
            Task.Status.SCHEDULED,
            Task.Status.READY,
            Task.Status.ACCEPTED,
            Task.Status.IN_PROGRESS,
        ]

        # หา tasks ที่มี dependency ที่ยังไม่เสร็จ
        dependencies = TaskDependency.objects.filter(
            task__status__in=active_statuses,
        ).select_related("task", "depends_on")

        for dep in dependencies:
            if dep.depends_on.status != Task.Status.COMPLETED:
                blocked.append({
                    "task": dep.task,
                    "blocked_by": dep.depends_on,
                })

        return blocked
