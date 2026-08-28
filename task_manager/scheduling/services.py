from core.utils import today_local
"""
SchedulingService - Business logic สำหรับการจัดตารางเวลา

จัดการ:
- Schedule validation
- Reschedule task
- Reorder tasks
- Detect conflicts
- Postpone task
- Generate recurring tasks
"""

from datetime import timedelta, datetime, time

from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from tasks.models import Task, TaskActivity, TaskAssignment, TaskTemplate


class SchedulingService:
    """
    จัดการ business logic สำหรับ scheduling
    """

    # === Schedule Validation ===

    @staticmethod
    def validate_schedule(prepare_at=None, start_at=None, deadline=None):
        """
        ตรวจสอบว่าเวลาจัดตารางถูกต้อง: prepare_at <= start_at <= deadline

        Raises:
            ValueError ถ้าเวลาไม่ถูกต้อง
        """
        if prepare_at and start_at:
            if prepare_at > start_at:
                raise ValueError("เวลาเตรียมงานต้องน้อยกว่าหรือเท่ากับเวลาเริ่มงาน")

        if start_at and deadline:
            if start_at > deadline:
                raise ValueError("เวลาเริ่มงานต้องน้อยกว่าหรือเท่ากับกำหนดส่ง")

        if prepare_at and deadline:
            if prepare_at > deadline:
                raise ValueError("เวลาเตรียมงานต้องน้อยกว่าหรือเท่ากับกำหนดส่ง")

    @staticmethod
    def validate_schedule_for_task(task, prepare_at=None, start_at=None, deadline=None):
        """
        ตรวจสอบเวลาจัดตารางสำหรับ task โดยใช้ค่าที่มีอยู่เป็น default
        """
        p = prepare_at if prepare_at is not None else task.prepare_at
        s = start_at if start_at is not None else task.start_at
        d = deadline if deadline is not None else task.deadline

        SchedulingService.validate_schedule(p, s, d)

    # === Reschedule ===

    @staticmethod
    @transaction.atomic
    def reschedule_task(task, user, prepare_at=None, start_at=None, deadline=None, task_date=None):
        """
        เปลี่ยนเวลาจัดตารางงาน

        Args:
            task: Task object
            user: ผู้ทำรายการ
            prepare_at: เวลาเตรียมงานใหม่
            start_at: เวลาเริ่มงานใหม่
            deadline: กำหนดส่งใหม่
            task_date: วันที่งานใหม่

        Returns:
            Task object
        """
        SchedulingService.validate_schedule_for_task(task, prepare_at, start_at, deadline)

        changes = []
        if prepare_at is not None and task.prepare_at != prepare_at:
            old_val = task.prepare_at.strftime("%d/%m %H:%M") if task.prepare_at else "-"
            new_val = prepare_at.strftime("%d/%m %H:%M") if prepare_at else "-"
            changes.append(f"เตรียมงาน: {old_val} → {new_val}")
            task.prepare_at = prepare_at

        if start_at is not None and task.start_at != start_at:
            old_val = task.start_at.strftime("%d/%m %H:%M") if task.start_at else "-"
            new_val = start_at.strftime("%d/%m %H:%M") if start_at else "-"
            changes.append(f"เริ่มงาน: {old_val} → {new_val}")
            task.start_at = start_at

        if deadline is not None and task.deadline != deadline:
            old_val = task.deadline.strftime("%d/%m %H:%M") if task.deadline else "-"
            new_val = deadline.strftime("%d/%m %H:%M") if deadline else "-"
            changes.append(f"กำหนดส่ง: {old_val} → {new_val}")
            task.deadline = deadline

        if task_date is not None and task.task_date != task_date:
            old_val = task.task_date.strftime("%d/%m/%Y") if task.task_date else "-"
            new_val = task_date.strftime("%d/%m/%Y") if task_date else "-"
            changes.append(f"วันที่งาน: {old_val} → {new_val}")
            task.task_date = task_date

        if changes:
            task.save(update_fields=[
                "prepare_at", "start_at", "deadline", "task_date", "updated_at"
            ])
            TaskActivity.objects.create(
                task=task,
                user=user,
                action=TaskActivity.Action.STATUS_CHANGED,
                description=f"เปลี่ยนเวลาจัดตาราง: {'; '.join(changes)}",
            )

            # ส่ง notification ให้ assignees
            try:
                from notifications.services import NotificationService
                changes_desc = "; ".join(changes)
                for assignment in task.assignments.select_related("assigned_to").all():
                    if assignment.assigned_to != user:
                        NotificationService.notify_task_rescheduled(
                            task, assignment.assigned_to, changes_desc
                        )
            except Exception:
                pass

        return task

    # === Queue Reordering ===

    @staticmethod
    def get_user_tasks_for_date(user, target_date):
        """
        ดึงงานของ user สำหรับวันที่กำหนด เรียงตาม queue

        Returns:
            QuerySet
        """
        return Task.objects.filter(
            Q(assignments__assigned_to=user) &
            Q(task_date=target_date) &
            Q(status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
                Task.Status.IN_PROGRESS,
                Task.Status.PROBLEM,
                Task.Status.ERROR,
                Task.Status.POSTPONED,
            ])
        ).select_related("team", "created_by").prefetch_related("assignments").distinct().order_by(
            "start_at", "queue_position", "priority"
        )

    @staticmethod
    @transaction.atomic
    def reorder_task(task, new_position, user):
        """
        เปลี่ยนตำแหน่ง queue ของ task

        Args:
            task: Task object
            new_position: ตำแหน่งใหม่
            user: ผู้ทำรายการ

        Returns:
            Task object
        """
        if not task.task_date:
            raise ValueError("งานนี้ยังไม่มีวันที่กำหนด")

        old_position = task.queue_position
        if old_position == new_position:
            return task

        # ดึงงานทั้งหมดในวันเดียวกันของ user คนเดียวกัน
        assignment = task.assignments.first()
        if not assignment:
            raise ValueError("งานนี้ยังไม่มีการมอบหมาย")

        same_day_tasks = Task.objects.filter(
            task_date=task.task_date,
            assignments__assigned_to=assignment.assigned_to,
            status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
                Task.Status.IN_PROGRESS,
                Task.Status.PROBLEM,
                Task.Status.ERROR,
                Task.Status.POSTPONED,
            ],
        ).exclude(pk=task.pk).order_by("queue_position", "start_at")

        # จัดตำแหน่งใหม่
        if old_position < new_position:
            # เลื่อนลง: ตำแหน่งระหว่าง old+1 ถึง new เลื่อนขึ้น 1
            same_day_tasks.filter(
                queue_position__gt=old_position,
                queue_position__lte=new_position,
            ).update(queue_position=models.F("queue_position") - 1)
        else:
            # เลื่อนขึ้น: ตำแหน่งระหว่าง new ถึง old-1 เลื่อนลง 1
            same_day_tasks.filter(
                queue_position__gte=new_position,
                queue_position__lt=old_position,
            ).update(queue_position=models.F("queue_position") + 1)

        task.queue_position = new_position
        task.save(update_fields=["queue_position", "updated_at"])

        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.STATUS_CHANGED,
            description=f"เปลี่ยนตำแหน่งคิว: {old_position} → {new_position}",
        )

        return task

    @staticmethod
    @transaction.atomic
    def normalize_queue_positions(target_date, assigned_user):
        """
        จัดตำแหน่ง queue ให้เรียงต่อเนื่อง (0, 1, 2, ...)
        ใช้หลังจาก reorder หรือ delete
        """
        tasks = Task.objects.filter(
            task_date=target_date,
            assignments__assigned_to=assigned_user,
            status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
                Task.Status.IN_PROGRESS,
                Task.Status.PROBLEM,
                Task.Status.ERROR,
                Task.Status.POSTPONED,
            ],
        ).order_by("start_at", "queue_position", "priority")

        for idx, task in enumerate(tasks):
            if task.queue_position != idx:
                task.queue_position = idx
                task.save(update_fields=["queue_position"])

    # === Conflict Detection ===

    @staticmethod
    def detect_conflicts(user, start_at, deadline, exclude_task=None):
        """
        ตรวจสอบว่ามี task ที่ทับซ้อนกันหรือไม่

        Args:
            user: User object
            start_at: เวลาเริ่มงานที่ต้องการ
            deadline: กำหนดส่งที่ต้องการ
            exclude_task: Task ที่ต้องการยกเว้น (สำหรับ edit)

        Returns:
            QuerySet ของ tasks ที่ทับซ้อน
        """
        if not start_at or not deadline:
            return Task.objects.none()

        query = Q(
            assignments__assigned_to=user,
            start_at__lt=deadline,
            deadline__gt=start_at,
            status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
                Task.Status.IN_PROGRESS,
            ],
        )

        if exclude_task:
            query = query & ~Q(pk=exclude_task.pk)

        return Task.objects.filter(query).distinct()

    @staticmethod
    def has_conflict(user, start_at, deadline, exclude_task=None):
        """
        ตรวจสอบว่ามี conflict หรือไม่

        Returns:
            bool
        """
        return SchedulingService.detect_conflicts(user, start_at, deadline, exclude_task).exists()

    # === Postpone ===

    @staticmethod
    @transaction.atomic
    def postpone_task_with_time(task, user, new_task_date, new_start_at=None, new_deadline=None, reason=""):
        """
        เลื่อนงานไปวัน/เวลาใหม่

        Args:
            task: Task object
            user: ผู้ทำรายการ
            new_task_date: วันที่ใหม่
            new_start_at: เวลาเริ่มงานใหม่ (optional)
            new_deadline: กำหนดส่งใหม่ (optional)
            reason: เหตุผล

        Returns:
            Task object
        """
        if task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED):
            raise ValueError(f"ไม่สามารถเลื่อนงานได้ สถานะปัจจุบัน: {task.get_status_display()}")

        old_date = task.task_date
        old_start = task.start_at
        old_deadline = task.deadline

        task.task_date = new_task_date
        if new_start_at:
            task.start_at = new_start_at
        if new_deadline:
            task.deadline = new_deadline

        task.status = Task.Status.POSTPONED

        if reason:
            task.notes = f"{task.notes}\n\nเลื่อนงาน: {reason}" if task.notes else f"เลื่อนงาน: {reason}"

        task.save(update_fields=[
            "task_date", "start_at", "deadline", "status", "notes", "updated_at"
        ])

        # Activity log
        old_date_str = old_date.strftime("%d/%m/%Y") if old_date else "-"
        new_date_str = new_task_date.strftime("%d/%m/%Y") if new_task_date else "-"
        desc_parts = [f"วันที่: {old_date_str} → {new_date_str}"]

        if old_start and new_start_at:
            desc_parts.append(f"เริ่ม: {old_start.strftime('%H:%M')} → {new_start_at.strftime('%H:%M')}")

        if reason:
            desc_parts.append(f"เหตุผล: {reason}")

        TaskActivity.objects.create(
            task=task,
            user=user,
            action=TaskActivity.Action.POSTPONED,
            old_status=task.status,
            new_status=Task.Status.POSTPONED,
            description=f"เลื่อนงาน: {'; '.join(desc_parts)}",
        )

        # ส่ง notification ให้ assignees
        try:
            from notifications.services import NotificationService
            for assignment in task.assignments.select_related("assigned_to").all():
                if assignment.assigned_to != user:
                    NotificationService.notify_task_postponed(
                        task, assignment.assigned_to, reason
                    )
        except Exception:
            pass

        return task

    # === Recurring Tasks ===

    @staticmethod
    def generate_recurring_tasks(target_date=None, created_by=None):
        """
        สร้างงานจาก template ที่ตั้ง recurrence ไว้ สำหรับวันที่กำหนด

        Args:
            target_date: วันที่ต้องการสร้าง (default = วันนี้)
            created_by: ผู้สร้าง

        Returns:
            list ของ Task objects ที่สร้างใหม่
        """
        if target_date is None:
            target_date = today_local()

        templates = TaskTemplate.objects.filter(
            is_active=True,
            recurrence_type__isnull=False,
        ).exclude(recurrence_type=TaskTemplate.RecurrenceType.NONE)

        created_tasks = []

        for template in templates:
            should_create = False

            if template.recurrence_type == TaskTemplate.RecurrenceType.DAILY:
                should_create = True

            elif template.recurrence_type == TaskTemplate.RecurrenceType.WEEKDAYS:
                # จันทร์=0 ... ศุกร์=4
                if target_date.weekday() < 5:
                    should_create = True

            elif template.recurrence_type == TaskTemplate.RecurrenceType.WEEKLY:
                # สร้างเฉพาะวันเดียวกับวันที่สร้าง template
                if template.created_at and target_date.weekday() == template.created_at.date().weekday():
                    should_create = True

            if should_create:
                task = template.generate_task(target_date, created_by)
                if task:
                    created_tasks.append(task)

        return created_tasks

    # === Create from Template ===

    @staticmethod
    @transaction.atomic
    def create_task_from_template(template, target_date, created_by, assign_to=None, **overrides):
        """
        สร้าง task จาก template

        Args:
            template: TaskTemplate object
            target_date: วันที่ต้องการ
            created_by: ผู้สร้าง
            assign_to: มอบหมายให้ (list ของ users)
            **overrides: ค่าที่ต้องการ override จาก template

        Returns:
            Task object
        """
        from tasks.services import TaskService

        # คำนวณเวลาจาก template
        start_time = None
        prepare_time = None
        deadline = None
        recurrence_time = overrides.get("recurrence_time", template.recurrence_time)

        if recurrence_time:
            start_time = timezone.make_aware(
                timezone.datetime.combine(target_date, recurrence_time)
            )
            prepare_minutes = overrides.get("prepare_minutes", template.default_prepare_minutes_before)
            duration_minutes = overrides.get("duration_minutes", template.default_duration_minutes)
            prepare_time = start_time - timedelta(minutes=prepare_minutes)
            deadline = start_time + timedelta(minutes=duration_minutes)

        task = TaskService.create_task(
            title=overrides.get("title", template.name),
            created_by=created_by,
            description=overrides.get("description", template.description),
            category=overrides.get("category", template.category),
            priority=overrides.get("priority", template.priority),
            task_date=target_date,
            deadline=deadline or overrides.get("deadline"),
            start_at=start_time or overrides.get("start_at"),
            prepare_at=prepare_time or overrides.get("prepare_at"),
            team=overrides.get("team", template.default_team),
            assign_to=assign_to,
            estimated_minutes=overrides.get("estimated_minutes", template.default_duration_minutes),
            location=overrides.get("location", template.location),
            notes=overrides.get("notes", template.notes),
            is_open=overrides.get("is_open", template.is_open),
            reward=overrides.get("reward", template.reward),
        )

        # บันทึกว่าสร้างจาก template
        task.template = template
        task.save(update_fields=["template"])

        return task

    # === Manager Schedule View Helpers ===

    @staticmethod
    def get_manager_schedule(date_from, date_to, employee=None, team=None):
        """
        ดึงข้อมูลตารางสำหรับ manager view

        Args:
            date_from: วันที่เริ่มต้น
            date_to: วันที่สิ้นสุด
            employee: filter ตาม employee (optional)
            team: filter ตาม team (optional)

        Returns:
            QuerySet ของ Task
        """
        query = Q(
            task_date__gte=date_from,
            task_date__lte=date_to,
            status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
                Task.Status.IN_PROGRESS,
                Task.Status.PROBLEM,
                Task.Status.ERROR,
                Task.Status.POSTPONED,
            ],
        )

        if employee:
            query = query & Q(assignments__assigned_to=employee)

        if team:
            query = query & Q(team=team)

        return Task.objects.filter(query).select_related(
            "team", "created_by"
        ).prefetch_related(
            "assignments__assigned_to"
        ).distinct().order_by("task_date", "start_at", "queue_position")

    @staticmethod
    def get_employee_week_tasks(user, reference_date=None):
        """
        ดึงงานของ employee สำหรับสัปดาห์ที่ reference_date อยู่

        Args:
            user: User object
            reference_date: วันอ้างอิง (default = วันนี้)

        Returns:
            dict {date: QuerySet}
        """
        if reference_date is None:
            reference_date = today_local()

        # หาวันจันทร์ของสัปดาห์นี้
        monday = reference_date - timedelta(days=reference_date.weekday())
        sunday = monday + timedelta(days=6)

        tasks = Task.objects.filter(
            Q(assignments__assigned_to=user) &
            Q(task_date__gte=monday) &
            Q(task_date__lte=sunday) &
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

        # จัดกลุ่มตามวัน
        schedule = {}
        current = monday
        while current <= sunday:
            day_tasks = tasks.filter(task_date=current).order_by("start_at", "queue_position")
            schedule[current] = day_tasks
            current += timedelta(days=1)

        return schedule, monday, sunday

    @staticmethod
    def get_current_and_next_task(user):
        """
        ดึงงานปัจจุบันและงานถัดไปของ employee

        Returns:
            (current_task, next_task) หรือ (None, None)
        """
        now = timezone.now()
        today = now.date()

        active_statuses = [
            Task.Status.ACCEPTED,
            Task.Status.IN_PROGRESS,
        ]

        # งานปัจจุบัน: IN_PROGRESS หรือ ACCEPTED ที่ถึงเวลาแล้ว
        current_task = Task.objects.filter(
            Q(assignments__assigned_to=user) &
            Q(task_date=today) &
            Q(status__in=active_statuses)
        ).order_by("-status", "start_at").first()

        # ถ้าไม่มีงานปัจจุบัน ให้หา SCHEDULED/READY ที่ใกล้ถึงเวลา
        if not current_task:
            current_task = Task.objects.filter(
                Q(assignments__assigned_to=user) &
                Q(task_date=today) &
                Q(status__in=[Task.Status.SCHEDULED, Task.Status.READY]) &
                Q(start_at__lte=now + timedelta(minutes=15))
            ).order_by("start_at").first()

        # งานถัดไป: งานที่ยังไม่เริ่มในวันนี้
        next_task = Task.objects.filter(
            Q(assignments__assigned_to=user) &
            Q(task_date=today) &
            Q(status__in=[Task.Status.SCHEDULED, Task.Status.READY]) &
            (
                Q(start_at__gt=now) if current_task else Q(start_at__gte=now)
            )
        ).exclude(pk=current_task.pk if current_task else None).order_by("start_at").first()

        # ถ้าไม่มีงานวันนี้ ให้ดูวันพรุ่งนี้
        if not current_task and not next_task:
            tomorrow = today + timedelta(days=1)
            next_task = Task.objects.filter(
                Q(assignments__assigned_to=user) &
                Q(task_date=tomorrow) &
                Q(status__in=[Task.Status.SCHEDULED, Task.Status.READY])
            ).order_by("start_at").first()

        return current_task, next_task
