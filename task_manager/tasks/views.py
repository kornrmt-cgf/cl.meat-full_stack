from core.utils import today_local
"""
Views สำหรับ tasks app

จัดการ:
- Today / Tomorrow View
- Task CRUD
- Task Actions (Accept, Start, Complete, Problem, Error)
- HTMX endpoints
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from .forms import TaskCreateForm, TaskUpdateForm
from .models import Task, TaskActivity, TaskAssignment
from .permissions import ManagerRequiredMixin, TaskAccessMixin, can_access_task
from .services import TaskService


class TaskListView(LoginRequiredMixin, ListView):
    """รายการงานทั้งหมดของ user"""

    model = Task
    template_name = "tasks/task_list.html"
    context_object_name = "tasks"
    paginate_by = 20

    def get_queryset(self):
        # Manager ดูได้ทุกงาน, Employee ดูเฉพาะงานของตัวเอง
        from .permissions import is_manager
        if is_manager(self.request.user):
            return Task.objects.filter(
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
        return TaskService.get_all_active_tasks(self.request.user)


class TodayView(LoginRequiredMixin, TemplateView):
    """หน้างานวันนี้ - หน้าหลักของพนักงาน"""

    template_name = "tasks/today.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = today_local()

        context["today"] = today
        tasks = TaskService.get_user_tasks_today(self.request.user)
        context["tasks"] = tasks

        # เรียงตาม start_at แล้ว queue_position
        context["scheduled"] = tasks.filter(status=Task.Status.SCHEDULED).order_by("start_at", "queue_position")
        context["in_progress"] = tasks.filter(status=Task.Status.IN_PROGRESS).order_by("start_at", "queue_position")
        context["problem"] = tasks.filter(status=Task.Status.PROBLEM)
        context["completed"] = tasks.filter(status=Task.Status.COMPLETED)

        # Current / Next task
        from scheduling.services import SchedulingService
        current, next_task = SchedulingService.get_current_and_next_task(self.request.user)
        context["current_task"] = current
        context["next_task"] = next_task

        # Open tasks (marketplace) — แสดงเฉพาะที่ยังไม่มีคนรับ
        context["open_tasks"] = TaskService.get_open_tasks()[:6]

        return context


class TomorrowView(LoginRequiredMixin, TemplateView):
    """หน้างานพรุ่งนี้"""

    template_name = "tasks/tomorrow.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tomorrow = today_local() + timezone.timedelta(days=1)

        context["tomorrow"] = tomorrow
        context["tasks"] = TaskService.get_user_tasks_tomorrow(self.request.user)

        return context


class TaskCreateView(LoginRequiredMixin, ManagerRequiredMixin, CreateView):
    """สร้างงานใหม่ (manager/admin เท่านั้น)"""

    model = Task
    form_class = TaskCreateForm
    template_name = "tasks/task_form.html"
    success_url = reverse_lazy("tasks:today")

    def form_valid(self, form):
        # ตรวจสอบ work_mode
        work_mode = form.cleaned_data.get("work_mode", "assigned")
        is_open = (work_mode == "open")
        reward = form.cleaned_data.get("reward", 0) or 0

        task = TaskService.create_task(
            title=form.cleaned_data["title"],
            created_by=self.request.user,
            description=form.cleaned_data.get("description", ""),
            category=form.cleaned_data.get("category", "other"),
            priority=form.cleaned_data.get("priority", 2),
            task_date=form.cleaned_data.get("task_date"),
            deadline=form.cleaned_data.get("deadline"),
            start_at=form.cleaned_data.get("start_at"),
            prepare_at=form.cleaned_data.get("prepare_at"),
            estimated_minutes=form.cleaned_data.get("estimated_minutes"),
            location=form.cleaned_data.get("location", ""),
            notes=form.cleaned_data.get("notes", ""),
            is_open=is_open,
            reward=reward,
        )

        # มอบหมายงาน (เฉพาะโหมดมอบหมาย)
        if not is_open:
            assigned_profiles = form.cleaned_data.get("assigned_to")
            if assigned_profiles:
                users = [profile.user for profile in assigned_profiles]
                TaskService.assign_task(task, users, self.request.user)

        messages.success(self.request, f"สร้างงาน '{task.title}' สำเร็จ!")
        return redirect(self.success_url)


class TaskDetailView(LoginRequiredMixin, TaskAccessMixin, DetailView):
    """รายละเอียดงาน (manager หรือ assignee เท่านั้น)"""

    model = Task
    template_name = "tasks/task_detail.html"
    context_object_name = "task"

    def get_queryset(self):
        return Task.objects.select_related("team", "created_by").prefetch_related(
            "assignments__assigned_to",
            "activities__user",
            "reports__reported_by",
        )

    def get_object(self, queryset=None):
        return self.get_task_object()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["activities"] = self.object.activities.all()[:20]
        context["reports"] = self.object.reports.all()
        context["assignments"] = self.object.assignments.select_related("assigned_to").all()

        # เพิ่ม available employees สำหรับ manager
        from .permissions import is_manager
        if is_manager(self.request.user):
            from django.contrib.auth import get_user_model
            User = get_user_model()
            assigned_user_ids = self.object.assignments.values_list("assigned_to_id", flat=True)
            context["available_employees"] = User.objects.filter(
                profile__isnull=False,
                profile__status="active",
            ).exclude(pk__in=assigned_user_ids).select_related("profile")

        return context


class TaskUpdateView(LoginRequiredMixin, ManagerRequiredMixin, UpdateView):
    """แก้ไขงาน (manager/admin เท่านั้น)"""

    model = Task
    form_class = TaskUpdateForm
    template_name = "tasks/task_form.html"
    success_url = reverse_lazy("tasks:today")


class TaskAssignView(LoginRequiredMixin, ManagerRequiredMixin, View):
    """มอบหมายงาน (manager/admin เท่านั้น)"""

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        user_ids = request.POST.getlist("employees")
        if user_ids:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            users = User.objects.filter(pk__in=user_ids)
            TaskService.assign_task(task, users, request.user)
            messages.success(request, f"มอบหมายงาน '{task.title}' สำเร็จ!")
        else:
            messages.warning(request, "กรุณาเลือกพนักงานอย่างน้อย 1 คน")
        return redirect("tasks:detail", pk=pk)


# === Task Actions ===


class TaskAcceptView(LoginRequiredMixin, TaskAccessMixin, View):
    """รับงาน"""

    def post(self, request, pk):
        task = self.get_task_object()
        try:
            TaskService.accept_task(task, request.user)
            messages.success(request, f"รับงาน '{task.title}' สำเร็จ!")
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))

        return redirect("tasks:detail", pk=pk)


class TaskStartView(LoginRequiredMixin, TaskAccessMixin, View):
    """เริ่มทำงาน"""

    def post(self, request, pk):
        task = self.get_task_object()
        try:
            TaskService.start_task(task, request.user)
            messages.success(request, f"เริ่มงาน '{task.title}' สำเร็จ!")
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))

        return redirect("tasks:detail", pk=pk)


class TaskCompleteView(LoginRequiredMixin, TaskAccessMixin, View):
    """เสร็จงาน"""

    def post(self, request, pk):
        task = self.get_task_object()
        try:
            TaskService.complete_task(
                task,
                request.user,
                actual_minutes=request.POST.get("actual_minutes"),
                notes=request.POST.get("notes", ""),
            )
            messages.success(request, f"เสร็จงาน '{task.title}' สำเร็จ!")
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))

        return redirect("tasks:detail", pk=pk)


class TaskProblemView(LoginRequiredMixin, TaskAccessMixin, View):
    """รายงานปัญหา"""

    def post(self, request, pk):
        task = self.get_task_object()
        try:
            TaskService.report_problem(
                task,
                request.user,
                title=request.POST.get("title", "ไม่ระบุ"),
                description=request.POST.get("description", ""),
            )
            messages.warning(request, f"รายงานปัญหา '{task.title}' สำเร็จ")
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))

        return redirect("tasks:detail", pk=pk)


class TaskErrorView(LoginRequiredMixin, TaskAccessMixin, View):
    """รายงานข้อผิดพลาด"""

    def post(self, request, pk):
        task = self.get_task_object()
        try:
            TaskService.report_error(
                task,
                request.user,
                title=request.POST.get("title", "ไม่ระบุ"),
                description=request.POST.get("description", ""),
            )
            messages.error(request, f"รายงานข้อผิดพลาด '{task.title}' สำเร็จ")
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))

        return redirect("tasks:detail", pk=pk)


class TaskPostponeView(LoginRequiredMixin, TaskAccessMixin, View):
    """เลื่อนงาน"""

    def post(self, request, pk):
        task = self.get_task_object()
        try:
            TaskService.postpone_task(
                task,
                request.user,
                reason=request.POST.get("reason", ""),
            )
            messages.warning(request, f"เลื่อนงาน '{task.title}' สำเร็จ")
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))

        return redirect("tasks:detail", pk=pk)


class TaskCancelView(LoginRequiredMixin, TaskAccessMixin, View):
    """ยกเลิกงาน"""

    def post(self, request, pk):
        task = self.get_task_object()
        try:
            TaskService.cancel_task(
                task,
                request.user,
                reason=request.POST.get("reason", ""),
            )
            messages.info(request, f"ยกเลิกงาน '{task.title}' สำเร็จ")
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))

        return redirect("tasks:detail", pk=pk)


class OpenTasksView(LoginRequiredMixin, TemplateView):
    """หน้างานเปิดรับ - ให้ employee แย่งงาน"""

    template_name = "tasks/open_tasks.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["open_tasks"] = TaskService.get_open_tasks()
        return context


class TaskClaimView(LoginRequiredMixin, View):
    """แย่งงาน (employee กดปุ่มแย่ง)"""

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        try:
            TaskService.claim_task(task, request.user)
            messages.success(
                request,
                f"🎉 แย่งงาน '{task.title}' สำเร็จ! ค่าตอบแทน ฿{task.reward:,.2f}"
            )
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))
        return redirect("tasks:detail", pk=pk)


# === HTMX Views ===


class TaskListHTMXView(LoginRequiredMixin, ListView):
    """HTMX endpoint สำหรับรายการงาน"""

    model = Task
    template_name = "tasks/partials/task_list.html"
    context_object_name = "tasks"
    paginate_by = 20

    def get_queryset(self):
        return TaskService.get_all_active_tasks(self.request.user)


class TodayHTMXView(LoginRequiredMixin, TemplateView):
    """HTMX endpoint สำหรับหน้างานวันนี้"""

    template_name = "tasks/partials/today_tasks.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tasks"] = TaskService.get_user_tasks_today(self.request.user)
        return context
