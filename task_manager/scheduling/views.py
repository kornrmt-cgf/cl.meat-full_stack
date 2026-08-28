from core.utils import today_local
"""
Views สำหรับ scheduling app

จัดการ:
- Manager schedule view
- Employee week view
- Drag-and-drop reordering
- Reschedule endpoint
- TaskTemplate CRUD
- Conflict detection
"""

import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    TemplateView,
    UpdateView,
)

from tasks.models import Task, TaskAssignment, TaskTemplate
from tasks.permissions import ManagerRequiredMixin

from .forms import TaskTemplateForm
from .services import SchedulingService


# === Employee Views ===


class WeekView(LoginRequiredMixin, TemplateView):
    """หน้าสัปดาห์ของ employee"""

    template_name = "scheduling/week.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # รับวันที่จาก query param
        date_str = self.request.GET.get("date")
        if date_str:
            try:
                reference_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                reference_date = today_local()
        else:
            reference_date = today_local()

        schedule, monday, sunday = SchedulingService.get_employee_week_tasks(
            self.request.user, reference_date
        )

        context["schedule"] = schedule
        context["monday"] = monday
        context["sunday"] = sunday
        context["reference_date"] = reference_date
        context["today"] = today_local()

        # ข้อมูลสัปดาห์ก่อน/ถัดไป
        context["prev_week"] = monday - timedelta(days=7)
        context["next_week"] = sunday + timedelta(days=1)

        # Current/Next task
        current, next_task = SchedulingService.get_current_and_next_task(self.request.user)
        context["current_task"] = current
        context["next_task"] = next_task

        return context


# === Manager Views ===


class ManagerScheduleView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """หน้าตารางจัดงานสำหรับ manager"""

    template_name = "scheduling/manager_schedule.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # รับวันที่จาก query param
        date_str = self.request.GET.get("date")
        if date_str:
            try:
                reference_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                reference_date = today_local()
        else:
            reference_date = today_local()

        # Filter employee
        employee_id = self.request.GET.get("employee")
        employee = None
        if employee_id:
            from accounts.models import User
            employee = get_object_or_404(User, pk=employee_id)

        # Filter team
        team_id = self.request.GET.get("team")
        team = None
        if team_id:
            from accounts.models import Team
            team = get_object_or_404(Team, pk=team_id)

        tasks = SchedulingService.get_manager_schedule(
            reference_date, reference_date, employee=employee, team=team
        )

        # ข้อมูลวันก่อน/ถัดไป
        context["tasks"] = tasks
        context["reference_date"] = reference_date
        context["prev_date"] = reference_date - timedelta(days=1)
        context["next_date"] = reference_date + timedelta(days=1)
        context["today"] = today_local()
        context["selected_employee"] = employee
        context["selected_team"] = team

        # รายชื่อ employee ทั้งหมดสำหรับ filter
        from accounts.models import EmployeeProfile, Team as TeamModel, User as UserModel
        context["employees"] = UserModel.objects.filter(
            profile__isnull=False,
            profile__status="active",
        ).select_related("profile")
        context["teams"] = TeamModel.objects.filter(is_active=True)

        return context


class ManagerWeekView(LoginRequiredMixin, ManagerRequiredMixin, TemplateView):
    """หน้าสัปดาห์สำหรับ manager"""

    template_name = "scheduling/manager_week.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        date_str = self.request.GET.get("date")
        if date_str:
            try:
                reference_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                reference_date = today_local()
        else:
            reference_date = today_local()

        employee_id = self.request.GET.get("employee")
        employee = None
        if employee_id:
            from accounts.models import User
            employee = get_object_or_404(User, pk=employee_id)

        team_id = self.request.GET.get("team")
        team = None
        if team_id:
            from accounts.models import Team
            team = get_object_or_404(Team, pk=team_id)

        # หาวันจันทร์-อาทิตย์
        monday = reference_date - timedelta(days=reference_date.weekday())
        sunday = monday + timedelta(days=6)

        tasks = SchedulingService.get_manager_schedule(monday, sunday, employee=employee, team=team)

        # จัดกลุ่มตามวัน
        schedule = {}
        current = monday
        while current <= sunday:
            schedule[current] = tasks.filter(task_date=current)
            current += timedelta(days=1)

        context["schedule"] = schedule
        context["monday"] = monday
        context["sunday"] = sunday
        context["reference_date"] = reference_date
        context["today"] = today_local()
        context["prev_week"] = monday - timedelta(days=7)
        context["next_week"] = sunday + timedelta(days=1)
        context["selected_employee"] = employee
        context["selected_team"] = team

        from accounts.models import EmployeeProfile, Team as TeamModel, User
        context["employees"] = User.objects.filter(
            profile__isnull=False,
            profile__status="active",
        ).select_related("profile")
        context["teams"] = TeamModel.objects.filter(is_active=True)

        return context


# === HTMX Endpoints ===


class ReorderHTMXView(LoginRequiredMixin, ManagerRequiredMixin, View):
    """HTMX endpoint สำหรับ drag-and-drop reorder"""

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        try:
            data = json.loads(request.body) if request.body else {}
            new_position = int(data.get("position", 0))

            SchedulingService.reorder_task(task, new_position, request.user)

            return JsonResponse({"status": "ok", "position": new_position})
        except (ValueError, Exception) as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)


class RescheduleHTMXView(LoginRequiredMixin, ManagerRequiredMixin, View):
    """HTMX endpoint สำหรับ reschedule"""

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        try:
            prepare_at = request.POST.get("prepare_at")
            start_at = request.POST.get("start_at")
            deadline = request.POST.get("deadline")
            task_date = request.POST.get("task_date")

            # แปลง datetime strings
            from django.utils.dateparse import parse_datetime, parse_date

            prepare_at = parse_datetime(prepare_at) if prepare_at else None
            start_at = parse_datetime(start_at) if start_at else None
            deadline = parse_datetime(deadline) if deadline else None
            task_date = parse_date(task_date) if task_date else None

            # ทำให้ timezone-aware
            if prepare_at and timezone.is_naive(prepare_at):
                prepare_at = timezone.make_aware(prepare_at)
            if start_at and timezone.is_naive(start_at):
                start_at = timezone.make_aware(start_at)
            if deadline and timezone.is_naive(deadline):
                deadline = timezone.make_aware(deadline)

            SchedulingService.reschedule_task(
                task, request.user,
                prepare_at=prepare_at,
                start_at=start_at,
                deadline=deadline,
                task_date=task_date,
            )

            return JsonResponse({"status": "ok"})
        except ValueError as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)


class ConflictCheckHTMXView(LoginRequiredMixin, ManagerRequiredMixin, View):
    """HTMX endpoint สำหรับ check conflict"""

    def post(self, request):
        try:
            data = json.loads(request.body) if request.body else {}
            employee_id = data.get("employee_id")
            start_at_str = data.get("start_at")
            deadline_str = data.get("deadline")
            exclude_task_id = data.get("exclude_task_id")

            from django.utils.dateparse import parse_datetime
            from accounts.models import User

            employee = get_object_or_404(User, pk=employee_id)
            start_at = parse_datetime(start_at_str) if start_at_str else None
            deadline = parse_datetime(deadline_str) if deadline_str else None

            if start_at and timezone.is_naive(start_at):
                start_at = timezone.make_aware(start_at)
            if deadline and timezone.is_naive(deadline):
                deadline = timezone.make_aware(deadline)

            exclude_task = None
            if exclude_task_id:
                exclude_task = Task.objects.filter(pk=exclude_task_id).first()

            conflicts = SchedulingService.detect_conflicts(
                employee, start_at, deadline, exclude_task
            )

            conflict_list = []
            for t in conflicts[:5]:
                conflict_list.append({
                    "id": t.pk,
                    "title": t.title,
                    "start_at": t.start_at.strftime("%H:%M") if t.start_at else None,
                    "deadline": t.deadline.strftime("%H:%M") if t.deadline else None,
                })

            return JsonResponse({
                "has_conflict": conflicts.exists(),
                "conflicts": conflict_list,
            })
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)


class GenerateRecurringView(LoginRequiredMixin, ManagerRequiredMixin, View):
    """สร้าง recurring tasks สำหรับวันที่กำหนด"""

    def post(self, request):
        date_str = request.POST.get("date")
        if date_str:
            try:
                target_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = today_local()
        else:
            target_date = today_local()

        tasks = SchedulingService.generate_recurring_tasks(target_date, request.user)
        messages.success(request, f"สร้างงานประจำ {len(tasks)} รายการ สำหรับวันที่ {target_date}")
        return redirect(request.META.get("HTTP_REFERER", reverse_lazy("scheduling:manager-schedule")))


# === TaskTemplate Views ===


class TaskTemplateListView(LoginRequiredMixin, ManagerRequiredMixin, ListView):
    """รายการแม่แบบงาน"""

    model = TaskTemplate
    template_name = "scheduling/template_list.html"
    context_object_name = "templates"
    paginate_by = 20

    def get_queryset(self):
        return TaskTemplate.objects.filter(is_active=True).select_related("default_team", "created_by")


class TaskTemplateCreateView(LoginRequiredMixin, ManagerRequiredMixin, CreateView):
    """สร้างแม่แบบงานใหม่"""

    model = TaskTemplate
    form_class = TaskTemplateForm
    template_name = "scheduling/template_form.html"
    success_url = reverse_lazy("scheduling:template-list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, f"สร้างแม่แบบ '{form.instance.name}' สำเร็จ!")
        return super().form_valid(form)


class TaskTemplateUpdateView(LoginRequiredMixin, ManagerRequiredMixin, UpdateView):
    """แก้ไขแม่แบบงาน"""

    model = TaskTemplate
    form_class = TaskTemplateForm
    template_name = "scheduling/template_form.html"
    success_url = reverse_lazy("scheduling:template-list")

    def form_valid(self, form):
        messages.success(self.request, f"แก้ไขแม่แบบ '{form.instance.name}' สำเร็จ!")
        return super().form_valid(form)


class TaskTemplateDeleteView(LoginRequiredMixin, ManagerRequiredMixin, DeleteView):
    """ลบแม่แบบงาน (soft delete)"""

    model = TaskTemplate
    success_url = reverse_lazy("scheduling:template-list")

    def form_valid(self, form):
        self.object.is_active = False
        self.object.save(update_fields=["is_active"])
        messages.success(self.request, f"ลบแม่แบบ '{self.object.name}' สำเร็จ!")
        return redirect(self.success_url)


class TaskTemplateCreateTaskView(LoginRequiredMixin, ManagerRequiredMixin, View):
    """สร้าง task จาก template"""

    def post(self, request, pk):
        template = get_object_or_404(TaskTemplate, pk=pk, is_active=True)
        date_str = request.POST.get("date")
        employee_id = request.POST.get("employee")

        if date_str:
            try:
                target_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = today_local()
        else:
            target_date = today_local()

        assign_to = None
        if employee_id:
            from accounts.models import User
            assign_to = [get_object_or_404(User, pk=employee_id)]

        task = SchedulingService.create_task_from_template(
            template, target_date, request.user, assign_to=assign_to
        )

        messages.success(request, f"สร้างงาน '{task.title}' สำเร็จ!")
        return redirect("tasks:detail", pk=task.pk)
