from core.utils import today_local
"""
Views สำหรับ dashboard app

หน้า Dashboard สำหรับ manager/admin:
- Dashboard overview (task counts, status breakdown)
- Employee workload
- Team overview
- Quick links
"""

import csv
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from tasks.permissions import ManagerRequiredMixin
from reports.services import ReportingService

User = get_user_model()


class DashboardView(LoginRequiredMixin, View):
    """
    หน้าแรก redirect ไป Today View สำหรับ employee
    หรือ Management Dashboard สำหรับ manager
    """
    def get(self, request):
        from tasks.permissions import is_manager
        if is_manager(request.user):
            return redirect("dashboard:manager")
        return redirect("tasks:today")


class ManagerDashboardView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """
    Management Dashboard - หน้าภาพรวมสำหรับ manager/admin

    แสดง:
    - สถิติงานวันนี้ (task counts ตามสถานะ)
    - งานเกินกำหนด
    - Employee workload
    - Team overview
    - Quick links
    """

    template_name = "dashboard/manager_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # รับวันที่จาก query param
        date_str = self.request.GET.get("date")
        if date_str:
            try:
                target_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = today_local()
        else:
            target_date = today_local()

        # สถิติ dashboard
        stats = ReportingService.get_dashboard_stats(target_date)
        context.update(stats)

        # Employee workload
        context["employee_workload"] = ReportingService.get_employee_workload(target_date)

        # Team overview
        context["team_overview"] = ReportingService.get_team_overview(target_date)

        # ข้อมูลวันก่อน/ถัดไป
        context["prev_date"] = target_date - timedelta(days=1)
        context["next_date"] = target_date + timedelta(days=1)
        context["today"] = today_local()

        return context


class EmployeeWorkloadView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """
    หน้า workload ของ employee ทุกคน
    """

    template_name = "dashboard/employee_workload.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        date_str = self.request.GET.get("date")
        if date_str:
            try:
                target_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = today_local()
        else:
            target_date = today_local()

        context["employee_workload"] = ReportingService.get_employee_workload(target_date)
        context["target_date"] = target_date
        context["today"] = today_local()

        return context


class EmployeeDetailView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """
    หน้ารายละเอียด workload ของ employee คนเดียว
    """

    template_name = "dashboard/employee_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        user_id = self.kwargs["user_id"]
        user = User.objects.get(pk=user_id)

        # รับ date range
        date_from_str = self.request.GET.get("date_from")
        date_to_str = self.request.GET.get("date_to")

        today = today_local()

        if date_from_str:
            try:
                date_from = timezone.datetime.strptime(date_from_str, "%Y-%m-%d").date()
            except ValueError:
                date_from = today
        else:
            date_from = today

        if date_to_str:
            try:
                date_to = timezone.datetime.strptime(date_to_str, "%Y-%m-%d").date()
            except ValueError:
                date_to = today
        else:
            date_to = today

        # ข้อมูล workload
        detail = ReportingService.get_employee_detail(user, date_from, date_to)
        context.update(detail)

        # ประวัติงาน
        context["task_history"] = ReportingService.get_employee_task_history(
            user, date_from, date_to
        )[:50]

        context["today"] = today

        return context


class TeamOverviewView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """
    หน้า overview ของทุกทีม
    """

    template_name = "dashboard/team_overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        date_str = self.request.GET.get("date")
        if date_str:
            try:
                target_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = today_local()
        else:
            target_date = today_local()

        context["team_overview"] = ReportingService.get_team_overview(target_date)
        context["target_date"] = target_date
        context["today"] = today_local()

        return context
