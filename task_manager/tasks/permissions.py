"""
Permissions สำหรับ tasks app

จัดการสิทธิ์การเข้าถึง:
- Manager: สร้าง แก้ไข มอบหมายงาน ดูทุกงาน
- Employee: ดูเฉพาะงานที่มอบหมาย, รับ/เริ่ม/เสร็จ งานของตัวเอง
"""

from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Task


def is_manager(user):
    """ตรวจสอบว่าเป็น manager หรือ admin หรือไม่"""
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    profile = getattr(user, "profile", None)
    if profile and profile.role:
        role_slug = profile.role.slug.lower()
        if role_slug in ("manager", "admin"):
            return True
    return False


def is_task_assignee(user, task):
    """ตรวจสอบว่าเป็นผู้ได้รับมอบหมายงานนี้หรือไม่"""
    if not user.is_authenticated:
        return False
    return task.assignments.filter(assigned_to=user).exists()


def can_access_task(user, task):
    """ตรวจสอบสิทธิ์เข้าถึงงาน"""
    if is_manager(user):
        return True
    return is_task_assignee(user, task)


def can_manage_task(user):
    """ตรวจสอบสิทธิ์จัดการงาน (สร้าง/แก้ไข/มอบหมาย)"""
    return is_manager(user)


class ManagerRequiredMixin(UserPassesTestMixin):
    """
    Mixin สำหรับ views ที่ต้องใช้สิทธิ์ manager

    ใช้สำหรับ:
    - สร้างงาน
    - แก้ไขงาน
    - มอบหมายงาน
    - ดูงานของพนักงานคนอื่น
    """

    def test_func(self):
        return can_manage_task(self.request.user)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            from django.contrib import messages
            messages.error(self.request, "คุณไม่มีสิทธิ์เข้าถึงหน้านี้")
        return super().handle_no_permission()


class TaskAccessMixin:
    """
    Mixin สำหรับ views ที่เกี่ยวข้องกับงานเฉพาะ

    ตรวจสอบว่า user สามารถเข้าถึงงานนี้ได้หรือไม่
    - Manager: เข้าถึงได้ทุกงาน
    - Employee: เข้าถึงได้เฉพาะงานที่มอบหมาย
    """

    def get_task_object(self):
        """ดึง object งานพร้อมตรวจสอบสิทธิ์"""
        task = get_object_or_404(Task, pk=self.kwargs["pk"])
        if not can_access_task(self.request.user, task):
            raise Http404("ไม่พบงานนี้")
        return task
