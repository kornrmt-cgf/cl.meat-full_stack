from core.utils import today_local
"""
Views สำหรับ reports app

รายงานต่างๆ:
- Daily Report
- Employee Report
- Status Report
- Performance Metrics
- CSV Export
"""

import csv
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, StreamingHttpResponse
from django.views import View
from django.views.generic import TemplateView
from django.utils import timezone

from tasks.permissions import ManagerRequiredMixin
from .services import ReportingService

User = get_user_model()


class DailyReportView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """รายงานประจำวัน"""

    template_name = "reports/daily_report.html"

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

        report = ReportingService.get_daily_report(target_date)
        context.update(report)

        context["prev_date"] = target_date - timedelta(days=1)
        context["next_date"] = target_date + timedelta(days=1)
        context["today"] = today_local()

        return context


class EmployeeReportView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """รายงานของ employee"""

    template_name = "reports/employee_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        user_id = self.request.GET.get("employee")
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

        # รายชื่อ employee ทั้งหมด
        context["employees"] = User.objects.filter(
            profile__isnull=False,
            profile__status="active",
        ).select_related("profile")

        if user_id:
            try:
                user = User.objects.get(pk=user_id)
                report = ReportingService.get_employee_report(user, date_from, date_to)
                context.update(report)
            except User.DoesNotExist:
                pass
        else:
            context["selected_user"] = None

        context["date_from"] = date_from
        context["date_to"] = date_to
        context["today"] = today

        return context


class StatusReportView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """รายงานสถานะงาน"""

    template_name = "reports/status_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        date_from_str = self.request.GET.get("date_from")
        date_to_str = self.request.GET.get("date_to")

        today = today_local()

        date_from = None
        date_to = None

        if date_from_str:
            try:
                date_from = timezone.datetime.strptime(date_from_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        if date_to_str:
            try:
                date_to = timezone.datetime.strptime(date_to_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        report = ReportingService.get_status_report(date_from, date_to)
        context.update(report)

        context["date_from"] = date_from or today
        context["date_to"] = date_to or today
        context["today"] = today

        return context


class PerformanceMetricsView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """Performance metrics"""

    template_name = "reports/performance_metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        date_from_str = self.request.GET.get("date_from")
        date_to_str = self.request.GET.get("date_to")

        today = today_local()

        date_from = None
        date_to = None

        if date_from_str:
            try:
                date_from = timezone.datetime.strptime(date_from_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        if date_to_str:
            try:
                date_to = timezone.datetime.strptime(date_to_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        metrics = ReportingService.get_performance_metrics(date_from, date_to)
        context.update(metrics)

        context["date_from"] = date_from or today
        context["date_to"] = date_to or today
        context["today"] = today

        return context


class CSVExportView(LoginRequiredMixin, ManagerRequiredMixin, View):
    """Export ข้อมูลเป็น CSV"""

    def get(self, request):
        date_from_str = request.GET.get("date_from")
        date_to_str = request.GET.get("date_to")
        employee_id = request.GET.get("employee")
        team_id = request.GET.get("team")
        status = request.GET.get("status")

        today = today_local()

        date_from = None
        date_to = None
        employee = None
        team = None

        if date_from_str:
            try:
                date_from = timezone.datetime.strptime(date_from_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        if date_to_str:
            try:
                date_to = timezone.datetime.strptime(date_to_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        if employee_id:
            try:
                employee = User.objects.get(pk=employee_id)
            except User.DoesNotExist:
                pass

        if team_id:
            from accounts.models import Team
            try:
                team = Team.objects.get(pk=team_id)
            except Team.DoesNotExist:
                pass

        headers = ReportingService.get_csv_headers()
        rows = ReportingService.get_csv_data(
            date_from=date_from,
            date_to=date_to,
            employee=employee,
            team=team,
            status=status,
        )

        # สร้าง CSV response
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="tasks_report_{today}.csv"'

        # เขียน BOM สำหรับ Excel
        response.write("\ufeff")

        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows([
            [row[h] for h in [
                "id", "title", "category", "priority", "status",
                "task_date", "start_at", "deadline", "completed_at",
                "assignees", "team", "estimated_minutes", "actual_minutes",
                "delay_minutes", "is_overdue",
            ]]
            for row in rows
        ])

        return response
