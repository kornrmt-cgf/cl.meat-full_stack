"""
Task models สำหรับ Freebuff Desktop

จัดการงาน, การมอบหมาย, ประวัติการเปลี่ยนสถานะ, และรายงานปัญหา
"""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Task(models.Model):
    """
    งานหลักในระบบ
    แต่ละงานมีสถานะ, กำหนดเวลา, และระดับความสำคัญ
    """

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "กำหนดไว้"
        READY = "ready", "พร้อมรับงาน"
        ACCEPTED = "accepted", "รับงานแล้ว"
        IN_PROGRESS = "in_progress", "กำลังทำ"
        COMPLETED = "completed", "เสร็จแล้ว"
        PROBLEM = "problem", "มีปัญหา"
        ERROR = "error", "เกิดข้อผิดพลาด"
        POSTPONED = "postponed", "เลื่อน"
        CANCELLED = "cancelled", "ยกเลิก"

    class Priority(models.IntegerChoices):
        LOW = 1, "ต่ำ"
        NORMAL = 2, "ปกติ"
        HIGH = 3, "สูง"
        URGENT = 4, "เร่งด่วน"

    class Category(models.TextChoices):
        PRODUCTION = "production", "การผลิต"
        WAREHOUSE = "warehouse", "คลังสินค้า"
        DELIVERY = "delivery", "จัดส่ง"
        CLEANING = "cleaning", "ทำความสะอาด"
        MAINTENANCE = "maintenance", "ซ่อมบำรุง"
        ADMIN = "admin", "งานธุรการ"
        OTHER = "other", "อื่นๆ"

    # === ข้อมูลหลัก ===
    title = models.CharField("ชื่องาน", max_length=200)
    description = models.TextField("รายละเอียด", blank=True)
    category = models.CharField(
        "หมวดหมู่",
        max_length=20,
        choices=Category.choices,
        default=Category.OTHER,
    )
    priority = models.IntegerField(
        "ความสำคัญ",
        choices=Priority.choices,
        default=Priority.NORMAL,
    )
    status = models.CharField(
        "สถานะ",
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    # === วันที่งาน (ใช้สำหรับ today/tomorrow view) ===
    task_date = models.DateField(
        "วันที่งาน",
        null=True,
        blank=True,
        help_text="วันที่กำหนดให้ทำงาน ใช้สำหรับแสดงใน today/tomorrow view",
    )

    # === กำหนดเวลา ===
    prepare_at = models.DateTimeField(
        "เวลาเตรียมงาน",
        null=True,
        blank=True,
    )
    start_at = models.DateTimeField(
        "เวลาเริ่มงาน",
        null=True,
        blank=True,
    )
    deadline = models.DateTimeField(
        "กำหนดส่ง",
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField(
        "เวลาเสร็จ",
        null=True,
        blank=True,
    )

    # === ความสัมพันธ์ ===
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_tasks",
        verbose_name="สร้างโดย",
    )
    team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="ทีมที่รับผิดชอบ",
    )

    # === ข้อมูลเพิ่มเติม ===
    estimated_minutes = models.PositiveIntegerField(
        "เวลาที่คาดว่าจะใช้ (นาที)",
        null=True,
        blank=True,
    )
    actual_minutes = models.PositiveIntegerField(
        "เวลาที่ใช้จริง (นาที)",
        null=True,
        blank=True,
    )
    location = models.CharField("สถานที่", max_length=200, blank=True)
    notes = models.TextField("หมายเหตุ", blank=True)
    is_recurring = models.BooleanField("งานประจำ", default=False)

    # === Timestamps ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # === Queue ===
    queue_position = models.PositiveIntegerField(
        "ตำแหน่งในคิว",
        default=0,
        help_text="ตำแหน่งเรียงลำดับงานในแต่ละวัน",
    )

    # === Open Task Marketplace ===
    is_open = models.BooleanField(
        "งานเปิดรับ",
        default=False,
        help_text="True = งานเปิดให้ใครก็ได้มาแย่งรับ, False = มอบหมายเฉพาะคน",
    )
    reward = models.DecimalField(
        "ค่าตอบแทน (฿)",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="ค่าตอบแทนสำหรับงานนี้ (บาท)",
    )
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claimed_tasks",
        verbose_name="ผู้รับงาน",
    )
    claimed_at = models.DateTimeField(
        "เวลาที่รับงาน",
        null=True,
        blank=True,
    )

    # === Template / Recurrence ===
    template = models.ForeignKey(
        "TaskTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_tasks",
        verbose_name="แม่แบบที่ใช้สร้าง",
    )
    recurrence_id = models.CharField(
        "รหัส recurrence instance",
        max_length=100,
        blank=True,
        help_text="รหัสสำหรับป้องกัน duplicate recurrence instance",
    )

    class Meta:
        verbose_name = "งาน"
        verbose_name_plural = "งาน"
        ordering = ["task_date", "start_at", "queue_position"]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"

    @property
    def is_overdue(self):
        """ตรวจสอบว่าเกินกำหนดหรือไม่"""
        if self.deadline and self.status not in (
            self.Status.COMPLETED,
            self.Status.CANCELLED,
        ):
            return timezone.now() > self.deadline
        return False

    @property
    def priority_badge_class(self):
        """CSS class สำหรับ badge ความสำคัญ"""
        classes = {
            self.Priority.LOW: "badge-low",
            self.Priority.NORMAL: "badge-normal",
            self.Priority.HIGH: "badge-high",
            self.Priority.URGENT: "badge-urgent",
        }
        return classes.get(self.priority, "badge-normal")

    @property
    def status_color(self):
        """สีของสถานะ"""
        colors = {
            self.Status.SCHEDULED: "#6b7280",
            self.Status.READY: "#3b82f6",
            self.Status.ACCEPTED: "#8b5cf6",
            self.Status.IN_PROGRESS: "#f59e0b",
            self.Status.COMPLETED: "#10b981",
            self.Status.PROBLEM: "#ef4444",
            self.Status.ERROR: "#dc2626",
            self.Status.POSTPONED: "#f97316",
            self.Status.CANCELLED: "#6b7280",
        }
        return colors.get(self.status, "#6b7280")


class TaskAssignment(models.Model):
    """
    การมอบหมายงาน
    งานหนึ่งสามารถมอบหมายให้หลายคนได้
    """

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name="งาน",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="task_assignments",
        verbose_name="มอบหมายให้",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="assigned_tasks",
        verbose_name="มอบหมายโดย",
    )
    accepted_at = models.DateTimeField("เวลารับงาน", null=True, blank=True)
    started_at = models.DateTimeField("เวลาเริ่มทำ", null=True, blank=True)
    completed_at = models.DateTimeField("เวลาเสร็จ", null=True, blank=True)
    is_primary = models.BooleanField("ผู้รับผิดชอบหลัก", default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "การมอบหมายงาน"
        verbose_name_plural = "การมอบหมายงาน"
        unique_together = ["task", "assigned_to"]

    def __str__(self):
        return f"{self.assigned_to.display_name} <- {self.task.title}"


class TaskActivity(models.Model):
    """
    ประวัติการเปลี่ยนสถานะของงาน
    บันทึกทุก action ที่เกิดขึ้นกับงาน
    """

    class Action(models.TextChoices):
        CREATED = "created", "สร้างงาน"
        ASSIGNED = "assigned", "มอบหมายงาน"
        ACCEPTED = "accepted", "รับงาน"
        STARTED = "started", "เริ่มงาน"
        COMPLETED = "completed", "เสร็จงาน"
        PROBLEM_REPORTED = "problem_reported", "รายงานปัญหา"
        ERROR_REPORTED = "error_reported", "รายงานข้อผิดพลาด"
        POSTPONED = "postponed", "เลื่อนงาน"
        CANCELLED = "cancelled", "ยกเลิกงาน"
        STATUS_CHANGED = "status_changed", "เปลี่ยนสถานะ"
        NOTE_ADDED = "note_added", "เพิ่มหมายเหตุ"

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="activities",
        verbose_name="งาน",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="task_activities",
        verbose_name="ผู้ทำรายการ",
    )
    action = models.CharField(
        "รายการ",
        max_length=20,
        choices=Action.choices,
    )
    old_status = models.CharField("สถานะเดิม", max_length=20, blank=True)
    new_status = models.CharField("สถานะใหม่", max_length=20, blank=True)
    description = models.TextField("รายละเอียด", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ประวัติงาน"
        verbose_name_plural = "ประวัติงาน"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.task.title} - {self.get_action_display()}"


class TaskReport(models.Model):
    """
    รายงานปัญหา/ข้อผิดพลาดของงาน
    """

    class ReportType(models.TextChoices):
        PROBLEM = "problem", "ปัญหา"
        ERROR = "error", "ข้อผิดพลาด"
        DELAY = "delay", "ความล่าช้า"
        QUALITY = "quality", "คุณภาพ"
        SAFETY = "safety", "ความปลอดภัย"
        OTHER = "other", "อื่นๆ"

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="reports",
        verbose_name="งาน",
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="task_reports",
        verbose_name="ผู้รายงาน",
    )
    report_type = models.CharField(
        "ประเภทรายงาน",
        max_length=20,
        choices=ReportType.choices,
    )
    title = models.CharField("หัวข้อ", max_length=200)
    description = models.TextField("รายละเอียด")
    resolution = models.TextField("วิธีแก้ไข", blank=True)
    resolved = models.BooleanField("แก้ไขแล้ว", default=False)
    resolved_at = models.DateTimeField("เวลาแก้ไข", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "รายงานปัญหา"
        verbose_name_plural = "รายงานปัญหา"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_report_type_display()}] {self.title}"


class TaskTemplate(models.Model):
    """
    แม่แบบงาน
    ใช้สร้างงานซ้ำๆ โดยไม่ต้องกรอกข้อมูลใหม่ทุกครั้ง
    """

    class RecurrenceType(models.TextChoices):
        NONE = "none", "ไม่ประจำ"
        DAILY = "daily", "ทุกวัน"
        WEEKDAYS = "weekdays", "จันทร์-ศุกร์"
        WEEKLY = "weekly", "ทุกสัปดาห์"

    name = models.CharField("ชื่อแม่แบบ", max_length=200)
    description = models.TextField("รายละเอียด", blank=True)
    category = models.CharField(
        "หมวดหมู่",
        max_length=20,
        choices=Task.Category.choices,
        default=Task.Category.OTHER,
    )
    priority = models.IntegerField(
        "ความสำคัญ",
        choices=Task.Priority.choices,
        default=Task.Priority.NORMAL,
    )
    estimated_minutes = models.PositiveIntegerField(
        "เวลาที่คาดว่าจะใช้ (นาที)",
        null=True,
        blank=True,
    )
    location = models.CharField("สถานที่", max_length=200, blank=True)
    notes = models.TextField("หมายเหตุ", blank=True)

    # === Default schedule ===
    default_prepare_minutes_before = models.PositiveIntegerField(
        "เตรียมงานล่วงหน้า (นาที)",
        default=0,
        help_text="จำนวนนาทีก่อน start_at ที่ควรเริ่มเตรียมงาน",
    )
    default_duration_minutes = models.PositiveIntegerField(
        "ระยะเวลาทำงาน (นาที)",
        default=60,
        help_text="ระยะเวลาที่คาดว่าจะใช้ทำงาน",
    )

    # === Recurrence ===
    recurrence_type = models.CharField(
        "ประเภทการประจำ",
        max_length=20,
        choices=RecurrenceType.choices,
        default=RecurrenceType.NONE,
    )
    recurrence_time = models.TimeField(
        "เวลาเริ่มงานประจำ",
        null=True,
        blank=True,
        help_text="เวลาเริ่มงานสำหรับงานที่ทำประจำ",
    )
    default_team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="templates",
        verbose_name="ทีมเริ่มต้น",
    )

    # === Open Task Marketplace ===
    is_open = models.BooleanField(
        "งานเปิดรับ",
        default=False,
        help_text="True = สร้างเป็นงานเปิดให้แย่ง, False = มอบหมายเฉพาะคน",
    )
    reward = models.DecimalField(
        "ค่าตอบแทน (฿)",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="ค่าตอบแทนสำหรับงานนี้ (บาท)",
    )

    is_active = models.BooleanField("ใช้งาน", default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_templates",
        verbose_name="สร้างโดย",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "แม่แบบงาน"
        verbose_name_plural = "แม่แบบงาน"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} [{self.get_recurrence_type_display()}]"

    def generate_task(self, target_date, created_by=None):
        """
        สร้าง Task จาก template สำหรับวันที่กำหนด

        Returns:
            Task object หรือ None ถ้า duplicate
        """
        recurrence_id = f"{self.pk}_{target_date.isoformat()}"

        # ป้องกัน duplicate
        if Task.objects.filter(recurrence_id=recurrence_id).exists():
            return None

        # คำนวณเวลา
        start_time = None
        prepare_time = None
        deadline = None

        if self.recurrence_time:
            from django.utils import timezone as tz
            start_time = tz.make_aware(
                tz.datetime.combine(target_date, self.recurrence_time)
            )
            prepare_time = start_time - timedelta(minutes=self.default_prepare_minutes_before)
            deadline = start_time + timedelta(minutes=self.default_duration_minutes)

        task = Task.objects.create(
            title=self.name,
            description=self.description,
            category=self.category,
            priority=self.priority,
            status=Task.Status.SCHEDULED,
            task_date=target_date,
            prepare_at=prepare_time,
            start_at=start_time,
            deadline=deadline,
            estimated_minutes=self.default_duration_minutes,
            location=self.location,
            notes=self.notes,
            team=self.default_team,
            created_by=created_by,
            template=self,
            recurrence_id=recurrence_id,
            is_recurring=True,
            is_open=self.is_open,
            reward=self.reward,
        )

        # Activity log
        TaskActivity.objects.create(
            task=task,
            user=created_by,
            action=TaskActivity.Action.CREATED,
            new_status=task.status,
            description=f"สร้างจากแม่แบบ: {self.name}",
        )

        return task


class TaskDependency(models.Model):
    """
    ความสัมพันธ์ระหว่างงาน (blocker)
    งาน B รอให้งาน A เสร็จก่อน
    """

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="dependencies",
        verbose_name="งานที่รอ",
    )
    depends_on = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="blocks",
        verbose_name="งานที่ต้องเสร็จก่อน",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ความสัมพันธ์งาน"
        verbose_name_plural = "ความสัมพันธ์งาน"
        unique_together = ["task", "depends_on"]

    def __str__(self):
        return f"{self.task.title} รอ {self.depends_on.title}"
