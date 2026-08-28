"""
Notification models สำหรับ Freebuff Desktop

จัดการแจ้งเตือนในระบบ
"""

from django.conf import settings
from django.db import models


class Notification(models.Model):
    """
    แจ้งเตือนในระบบ
    """

    class Type(models.TextChoices):
        TASK_ASSIGNED = "task_assigned", "ได้รับมอบหมายงาน"
        TASK_OPEN = "task_open", "งานเปิดรับ"
        TASK_CLAIMED = "task_claimed", "มีคนรับงาน"
        TASK_STARTING = "task_starting", "งานกำลังจะเริ่ม"
        TASK_COMPLETED = "task_completed", "งานเสร็จแล้ว"
        TASK_OVERDUE = "task_overdue", "งานเกินกำหนด"
        TASK_RESCHEDULED = "task_rescheduled", "เปลี่ยนเวลางาน"
        TASK_POSTPONED = "task_postponed", "เลื่อนงาน"
        TASK_PROBLEM = "task_problem", "รายงานปัญหา"
        TASK_ERROR = "task_error", "รายงานข้อผิดพลาด"
        TASK_UPDATED = "task_updated", "งานถูกอัพเดท"
        PROBLEM_REPORTED = "problem_reported", "มีการรายงานปัญหา"
        SYSTEM = "system", "ระบบ"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="ผู้รับ",
    )
    notification_type = models.CharField(
        "ประเภท",
        max_length=20,
        choices=Type.choices,
    )
    title = models.CharField("หัวข้อ", max_length=200)
    message = models.TextField("ข้อความ")
    task = models.ForeignKey(
        "tasks.Task",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name="งานที่เกี่ยวข้อง",
    )
    is_read = models.BooleanField("อ่านแล้ว", default=False)
    read_at = models.DateTimeField("เวลาอ่าน", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "แจ้งเตือน"
        verbose_name_plural = "แจ้งเตือน"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_notification_type_display()}] {self.title}"

    def mark_as_read(self):
        """ทำเครื่องหมายว่าอ่านแล้ว"""
        from django.utils import timezone

        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])
