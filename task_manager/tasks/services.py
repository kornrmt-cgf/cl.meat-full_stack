from core.utils import today_local
"""
TaskService - Business logic สำหรับการจัดการงาน

แยก logic ออกจาก views เพื่อ:
- ทดสอบได้ง่าย
- ใช้ซ้ำได้
- maintain ง่าย
"""

from django.db import transaction
from django.utils import timezone

from .models import Task, TaskActivity, TaskAssignment, TaskReport


class TaskService:
    """
    จัดการ business logic ของ Task
    ทุก method จะ log activity อัตโนมัติ
    """

    @staticmethod
    @transaction.atomic
    def create_task(
        title: str,
        created_by,
        description: str = "",
        category: str = "other",
        priority: int = 2,
        task_date=None,
        deadline=None,
        start_at=None,
        prepare_at=None,
        team=None,
        assign_to=None,
        estimated_minutes=None,
        location: str = "",
        notes: str = "",
        is_open: bool = False,
        reward: float = 0,
    ) -> Task:
        """
        สร้างงานใหม่

        Args:
            title: ชื่องาน
            created_by: ผู้สร้างงาน (User object)
            description: รายละเอียด
            category: หมวดหมู่
            priority: ความสำคัญ (1-5)
            deadline: กำหนดส่ง
            start_at: เวลาเริ่มงาน
            prepare_at: เวลาเตรียมงาน
            team: ทีมที่รับผิดชอบ
            assign_to: มอบหมายให้ (list ของ User objects)
            estimated_minutes: เวลาที่คาดว่าจะใช้
            location: สถานที่
            notes: หมายเหตุ

        Returns:
            Task object
        """
        # ถ้าไม่มี task_date ให้ใช้ date จาก deadline
        if not task_date and deadline:
            task_date = deadline.date() if hasattr(deadline, 'date') else deadline

        task = Task.objects.create(
            title=title,
            description=description,
            category=category,
            priority=priority,
            status=Task.Status.SCHEDULED,
            task_date=task_date,
            deadline=deadline,
            start_at=start_at,
            prepare_at=prepare_at,
            team=team,
            created_by=created_by,
            estimated_minutes=estimated_minutes,
            location=location,
            notes=notes,
            is_open=is_open,
            reward=reward,
        )

        # สร้าง activity log
        TaskActivity.objects.create(
            task=task,
            user=created_by,
            action=TaskActivity.Action.CREATED,
            new_status=task.status,
            description=f"สร้างงาน: {title}",
        )

        # มอบหมายงานถ้ามี
        if assign_to:
            TaskService.assign_task(task, assign_to, created_by)

        # แจ้งเตือนพนักงานทุกคนถ้าเป็นงานเปิดรับ
        if is_open:
            try:
                TaskService.notify_all_employees_open_task(task)
            except Exception:
                pass

        return task

    @staticmethod
    @transaction.atomic
    def assign_task(task: Task, users, assigned_by=None) -> list:
        """
        มอบหมายงานให้ผู้ใช้หลายคน

        Args:
            task: Task object
            users: list ของ User objects
            assigned_by: ผู้มอบหมาย

        Returns:
            list ของ TaskAssignment objects
        """
        assignments = []
        for user in users:
            assignment, created = TaskAssignment.objects.get_or_create(
                task=task,
                assigned_to=user,
                defaults={
                    "assigned_by": assigned_by,
                    "is_primary": len(assignments) == 0,  # คนแรกเป็น primary
                },
            )
            if created:
                TaskActivity.objects.create(
                    task=task,
                    user=assigned_by,
                    action=TaskActivity.Action.ASSIGNED,
                    description=f"มอบหมายงานให้ {user.display_name}",
                )
                # ส่ง notification
                try:
                    from notifications.services import NotificationService
                    NotificationService.notify_task_assigned(task, user, assigned_by)
                except Exception:
                    pass  # ไม่ให้ notification หยุด task operation
            assignments.append(assignment)
        return assignments

    @staticmethod
    @transaction.atomic
    def accept_task(task: Task, user) -> Task:
        """
        รับงาน

        Rules:
        - สถานะต้องเป็น READY หรือ SCHEDULED
        - ผู้รับต้องถูกมอบหมายงานนี้
        """
        if task.status not in (Task.Status.READY, Task.Status.SCHEDULED):
            raise ValueError(
                f"ไม่สามารถรับงานได้ สถานะปัจจุบัน: {task.get_status_display()}"
            )

        # ตรวจสอบการมอบหมาย
        assignment = TaskAssignment.objects.filter(
            task=task,
            assigned_to=user,
        ).first()

        if not assignment:
            raise PermissionError("คุณไม่ได้รับมอบหมายงานนี้")

        old_status = task.status
        task.status = Task.Status.ACCEPTED
        task.save(update_fields=["status", "updated_at"])

        # อัพเดท assignment
        assignment.accepted_at = timezone.now()
        assignment.save(update_fields=["accepted_at"])

        # สร้าง activity log
        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.ACCEPTED,
            old_status=old_status,
            new_status=task.status,
            description=f"{user.display_name} รับงาน",
        )

        return task

    @staticmethod
    @transaction.atomic
    def start_task(task: Task, user) -> Task:
        """
        เริ่มทำงาน

        Rules:
        - สถานะต้องเป็น ACCEPTED
        - ผู้เริ่มต้องถูกมอบหมายงานนี้
        """
        if task.status != Task.Status.ACCEPTED:
            raise ValueError(
                f"ไม่สามารถเริ่มงานได้ สถานะปัจจุบัน: {task.get_status_display()}"
            )

        # ตรวจสอบการมอบหมาย
        assignment = TaskAssignment.objects.filter(
            task=task,
            assigned_to=user,
        ).first()

        if not assignment:
            raise PermissionError("คุณไม่ได้รับมอบหมายงานนี้")

        old_status = task.status
        task.status = Task.Status.IN_PROGRESS
        task.save(update_fields=["status", "updated_at"])

        # อัพเดท assignment
        assignment.started_at = timezone.now()
        assignment.save(update_fields=["started_at"])

        # สร้าง activity log
        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.STARTED,
            old_status=old_status,
            new_status=task.status,
            description=f"{user.display_name} เริ่มทำงาน",
        )

        return task

    @staticmethod
    @transaction.atomic
    def complete_task(task: Task, user, actual_minutes: int = None, notes: str = "") -> Task:
        """
        เสร็จงาน

        Rules:
        - สถานะต้องเป็น IN_PROGRESS
        - ผู้เสร็จต้องถูกมอบหมายงานนี้
        """
        if task.status != Task.Status.IN_PROGRESS:
            raise ValueError(
                f"ไม่สามารถเสร็จงานได้ สถานะปัจจุบัน: {task.get_status_display()}"
            )

        # ตรวจสอบการมอบหมาย
        assignment = TaskAssignment.objects.filter(
            task=task,
            assigned_to=user,
        ).first()

        if not assignment:
            raise PermissionError("คุณไม่ได้รับมอบหมายงานนี้")

        old_status = task.status
        task.status = Task.Status.COMPLETED
        task.completed_at = timezone.now()
        if actual_minutes:
            task.actual_minutes = actual_minutes
        if notes:
            task.notes = notes
        task.save(update_fields=["status", "completed_at", "actual_minutes", "notes", "updated_at"])

        # อัพเดท assignment
        assignment.completed_at = timezone.now()
        assignment.save(update_fields=["completed_at"])

        # สร้าง activity log
        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.COMPLETED,
            old_status=old_status,
            new_status=task.status,
            description=f"{user.display_name} เสร็จงาน",
        )

        # ส่ง notification ให้ manager
        try:
            from notifications.services import NotificationService
            if task.created_by and task.created_by != user:
                NotificationService.notify_task_completed(task, task.created_by)
        except Exception:
            pass

        return task

    @staticmethod
    @transaction.atomic
    def report_problem(task: Task, user, title: str, description: str) -> Task:
        """
        รายงานปัญหา

        Rules:
        - สถานะต้องเป็น IN_PROGRESS
        """
        if task.status != Task.Status.IN_PROGRESS:
            raise ValueError(
                f"ไม่สามารถรายงานปัญหาได้ สถานะปัจจุบัน: {task.get_status_display()}"
            )

        old_status = task.status
        task.status = Task.Status.PROBLEM
        task.save(update_fields=["status", "updated_at"])

        # สร้างรายงาน
        TaskReport.objects.create(
            task=task,
            reported_by=user,
            report_type=TaskReport.ReportType.PROBLEM,
            title=title,
            description=description,
        )

        # สร้าง activity log
        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.PROBLEM_REPORTED,
            old_status=old_status,
            new_status=task.status,
            description=f"{user.display_name} รายงานปัญหา: {title}",
        )

        # ส่ง notification ให้ manager
        try:
            from notifications.services import NotificationService
            NotificationService.notify_task_problem(task, user)
        except Exception:
            pass

        return task

    @staticmethod
    @transaction.atomic
    def report_error(task: Task, user, title: str, description: str) -> Task:
        """
        รายงานข้อผิดพลาด

        Rules:
        - สถานะต้องเป็น IN_PROGRESS หรือ ACCEPTED
        """
        if task.status not in (Task.Status.IN_PROGRESS, Task.Status.ACCEPTED):
            raise ValueError(
                f"ไม่สามารถรายงานข้อผิดพลาดได้ สถานะปัจจุบัน: {task.get_status_display()}"
            )

        old_status = task.status
        task.status = Task.Status.ERROR
        task.save(update_fields=["status", "updated_at"])

        # สร้างรายงาน
        TaskReport.objects.create(
            task=task,
            reported_by=user,
            report_type=TaskReport.ReportType.ERROR,
            title=title,
            description=description,
        )

        # สร้าง activity log
        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.ERROR_REPORTED,
            old_status=old_status,
            new_status=task.status,
            description=f"{user.display_name} รายงานข้อผิดพลาด: {title}",
        )

        # ส่ง notification ให้ manager
        try:
            from notifications.services import NotificationService
            NotificationService.notify_task_error(task, user)
        except Exception:
            pass

        return task

    @staticmethod
    @transaction.atomic
    def postpone_task(task: Task, user, reason: str = "") -> Task:
        """
        เลื่อนงาน

        Rules:
        - สถานะต้องไม่เป็น COMPLETED หรือ CANCELLED
        """
        if task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED):
            raise ValueError(
                f"ไม่สามารถเลื่อนงานได้ สถานะปัจจุบัน: {task.get_status_display()}"
            )

        old_status = task.status
        task.status = Task.Status.POSTPONED
        if reason:
            task.notes = f"{task.notes}\n\nเลื่อนงาน: {reason}" if task.notes else f"เลื่อนงาน: {reason}"
        task.save(update_fields=["status", "notes", "updated_at"])

        # สร้าง activity log
        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.POSTPONED,
            old_status=old_status,
            new_status=task.status,
            description=f"{user.display_name} เลื่อนงาน" + (f": {reason}" if reason else ""),
        )

        return task

    @staticmethod
    @transaction.atomic
    def cancel_task(task: Task, user, reason: str = "") -> Task:
        """
        ยกเลิกงาน

        Rules:
        - สถานะต้องไม่เป็น COMPLETED
        """
        if task.status == Task.Status.COMPLETED:
            raise ValueError("ไม่สามารถยกเลิกงานที่เสร็จแล้ว")

        old_status = task.status
        task.status = Task.Status.CANCELLED
        if reason:
            task.notes = f"{task.notes}\n\nยกเลิกงาน: {reason}" if task.notes else f"ยกเลิกงาน: {reason}"
        task.save(update_fields=["status", "notes", "updated_at"])

        # สร้าง activity log
        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.CANCELLED,
            old_status=old_status,
            new_status=task.status,
            description=f"{user.display_name} ยกเลิกงาน" + (f": {reason}" if reason else ""),
        )

        return task

    @staticmethod
    @transaction.atomic
    def claim_task(task, user):
        """
        แย่งงาน (employee กดรับงานเปิด)

        Eligibility Rules:
        - user ต้องเป็น authenticated
        - user ต้องมี EmployeeProfile
        - EmployeeProfile.status ต้องเป็น "active"
        - task.is_open ต้องเป็น True
        - task.claimed_by ต้องเป็น None (ยังไม่มีคนรับ)
        - สถานะต้องเป็น SCHEDULED หรือ READY

        Returns:
            Task object
        """
        from django.utils import timezone as tz
        from accounts.models import EmployeeProfile

        # ตรวจสอบสิทธิ์ผู้ใช้
        if not user.is_authenticated:
            raise PermissionError("กรุณาเข้าสู่ระบบก่อนรับงาน")

        # ตรวจสอบ EmployeeProfile
        try:
            profile = user.profile
        except EmployeeProfile.DoesNotExist:
            raise PermissionError("คุณไม่มีสิทธิ์รับงาน (ไม่มีข้อมูลพนักงาน)")

        if profile.status != "active":
            raise PermissionError("คุณไม่สามารถรับงานได้ (สถานะพนักงานไม่ active)")

        # Refresh task from DB with row lock to prevent race condition
        task = Task.objects.select_for_update().get(pk=task.pk)

        if not task.is_open:
            raise ValueError("งานนี้ไม่ใช่งานเปิดรับ")

        if task.claimed_by is not None:
            raise ValueError("งานนี้ถูกแย่งไปแล้ว!")

        if task.status not in (Task.Status.SCHEDULED, Task.Status.READY):
            raise ValueError(
                f"ไม่สามารถรับงานได้ สถานะปัจจุบัน: {task.get_status_display()}"
            )

        old_status = task.status
        task.claimed_by = user
        task.claimed_at = tz.now()
        task.is_open = False  # ปิดไม่ให้คนอื่นแย่ง
        task.status = Task.Status.ACCEPTED
        task.save(update_fields=[
            "claimed_by", "claimed_at", "is_open", "status", "updated_at"
        ])

        # สร้าง TaskAssignment อัตโนมัติ
        TaskAssignment.objects.get_or_create(
            task=task,
            assigned_to=user,
            defaults={
                "assigned_by": task.created_by,
                "is_primary": True,
                "accepted_at": tz.now(),
            },
        )

        # สร้าง activity log
        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.ACCEPTED,
            old_status=old_status,
            new_status=task.status,
            description=f"{user.display_name} แย่งงานสำเร็จ",
        )

        # แจ้งเตือน manager ว่ามีคนรับงานแล้ว + ลบ notification งานเปิดรับ
        try:
            from notifications.services import NotificationService
            NotificationService.dismiss_open_task_notifications(task)
            if task.created_by and task.created_by != user:
                NotificationService.notify_task_claimed(task, user)
        except Exception:
            pass

        return task

    @staticmethod
    def notify_all_employees_open_task(task):
        """
        แจ้งเตือนพนักงานทุกคนเมื่อมีงานเปิดรับใหม่

        Args:
            task: Task object (is_open=True)
        """
        from django.contrib.auth import get_user_model
        from notifications.services import NotificationService

        User = get_user_model()
        employees = User.objects.filter(
            profile__isnull=False,
            profile__status="active",
        ).exclude(pk=task.created_by_id if task.created_by else None)

        for employee in employees:
            NotificationService.notify_new_open_task(task, employee)

    @staticmethod
    def get_open_tasks(user=None):
        """
        ดึงงานที่เปิดรับ (is_open=True, ยังไม่มีคนรับ)

        Args:
            user: ถ้าระบุ จะแยกงานที่ user แย่งได้ vs แย่งไม่ได้

        Returns:
            QuerySet ของ Task
        """
        return Task.objects.filter(
            is_open=True,
            claimed_by__isnull=True,
            status__in=[Task.Status.SCHEDULED, Task.Status.READY],
        ).select_related("team", "created_by").order_by(
            "-priority", "start_at"
        )

    @staticmethod
    def get_user_tasks_today(user):
        """
        ดึงงานของ user วันนี้

        ใช้ task_date เป็นหลัก ถ้าไม่มี task_date ให้ fallback ไปใช้ deadline

        Returns:
            QuerySet ของ Task ที่ assign ให้ user และ task_date เป็นวันนี้
        """
        today = today_local()

        from django.db.models import Q

        return Task.objects.filter(
            Q(assignments__assigned_to=user) &
            (Q(task_date=today) | (
                Q(task_date__isnull=True) &
                Q(deadline__date=today)
            )) &
            Q(status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
                Task.Status.IN_PROGRESS,
                Task.Status.PROBLEM,
                Task.Status.ERROR,
                Task.Status.POSTPONED,
            ])
        ).select_related("team", "created_by").prefetch_related("assignments").distinct()

    @staticmethod
    def get_user_tasks_tomorrow(user):
        """
        ดึงงานของ user พรุ่งนี้

        ใช้ task_date เป็นหลัก ถ้าไม่มี task_date ให้ fallback ไปใช้ deadline

        Returns:
            QuerySet ของ Task ที่ assign ให้ user และ task_date เป็นวันพรุ่งนี้
        """
        tomorrow = today_local() + timezone.timedelta(days=1)

        from django.db.models import Q

        return Task.objects.filter(
            Q(assignments__assigned_to=user) &
            (Q(task_date=tomorrow) | (
                Q(task_date__isnull=True) &
                Q(deadline__date=tomorrow)
            )) &
            Q(status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
                Task.Status.IN_PROGRESS,
            ])
        ).select_related("team", "created_by").prefetch_related("assignments").distinct()

    @staticmethod
    def get_all_active_tasks(user):
        """
        ดึงงานที่ยังไม่เสร็จทั้งหมดของ user

        Returns:
            QuerySet ของ Task ที่ status ไม่ใช่ COMPLETED หรือ CANCELLED
        """
        return Task.objects.filter(
            assignments__assigned_to=user,
            status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
                Task.Status.IN_PROGRESS,
                Task.Status.PROBLEM,
                Task.Status.ERROR,
                Task.Status.POSTPONED,
            ],
        ).select_related("team", "created_by").prefetch_related("assignments")
