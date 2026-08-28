from core.utils import today_local
"""
ReportingService - Business logic สำหรับ dashboard, reports, และ metrics

จัดการ:
- Dashboard statistics
- Task status counts
- Employee workload
- Team overview
- Daily/Employee/Status reports
- Performance metrics
- CSV export data
"""

from datetime import timedelta
from collections import defaultdict

from django.db.models import Q, Count, F, Avg
from django.utils import timezone

from tasks.models import Task, TaskAssignment, TaskActivity, TaskReport


class ReportingService:
    """
    จัดการ reporting logic ทั้งหมด
    คำนวณจากข้อมูล Task ที่มีอยู่จริง ไม่เก็บสถิติซ้ำ
    """

    # === Dashboard Statistics ===

    @staticmethod
    def get_dashboard_stats(target_date=None):
        """
        ดึงสถิติ dashboard สำหรับวันที่กำหนด

        Args:
            target_date: วันที่ต้องการ (default = วันนี้)

        Returns:
            dict ของ统计数据
        """
        if target_date is None:
            target_date = today_local()

        now = timezone.now()

        # งานทั้งหมดในวันนี้ (ทุกสถานะยกเว้น CANCELLED)
        all_tasks = Task.objects.filter(task_date=target_date).exclude(
            status=Task.Status.CANCELLED
        )

        total = all_tasks.count()

        # นับตามสถานะ
        status_counts = {}
        for status_value, status_label in Task.Status.choices:
            if status_value == Task.Status.CANCELLED:
                continue
            status_counts[status_value] = all_tasks.filter(status=status_value).count()

        # งานเกินกำหนด (deadline ผ่านไปแล้วแต่ยังไม่เสร็จ)
        overdue = all_tasks.filter(
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
        ).count()

        return {
            "target_date": target_date,
            "total": total,
            "status_counts": status_counts,
            "overdue": overdue,
        }

    @staticmethod
    def get_date_range_stats(date_from, date_to):
        """
        ดึงสถิติสำหรับช่วงวันที่กำหนด

        Returns:
            dict ของ统计数据
        """
        all_tasks = Task.objects.filter(
            task_date__gte=date_from,
            task_date__lte=date_to,
        ).exclude(status=Task.Status.CANCELLED)

        total = all_tasks.count()

        status_counts = {}
        for status_value, status_label in Task.Status.choices:
            if status_value == Task.Status.CANCELLED:
                continue
            status_counts[status_value] = all_tasks.filter(status=status_value).count()

        # งานเกินกำหนดในช่วงเวลานี้
        overdue = all_tasks.filter(
            deadline__lt=timezone.now(),
            status__in=[
                Task.Status.SCHEDULED,
                Task.Status.READY,
                Task.Status.ACCEPTED,
                Task.Status.IN_PROGRESS,
                Task.Status.PROBLEM,
                Task.Status.ERROR,
                Task.Status.POSTPONED,
            ],
        ).count()

        # จำนวนรายงานปัญหา
        problems = TaskReport.objects.filter(
            task__task_date__gte=date_from,
            task__task_date__lte=date_to,
        ).count()

        return {
            "date_from": date_from,
            "date_to": date_to,
            "total": total,
            "status_counts": status_counts,
            "overdue": overdue,
            "problems": problems,
        }

    # === Employee Workload ===

    @staticmethod
    def get_employee_workload(target_date=None):
        """
        ดึงข้อมูล workload ของพนักงานทุกคนสำหรับวันที่กำหนด

        Returns:
            list ของ dict แต่ละ dict มีข้อมูล workload ของ employee 1 คน
        """
        if target_date is None:
            target_date = today_local()

        now = timezone.now()

        # ดึง assignment ทั้งหมดของวันนี้
        assignments = TaskAssignment.objects.filter(
            task__task_date=target_date,
        ).select_related("assigned_to", "task")

        # จัดกลุ่มตาม employee
        employee_data = defaultdict(lambda: {
            "user": None,
            "assigned": 0,
            "completed": 0,
            "in_progress": 0,
            "problem": 0,
            "error": 0,
            "overdue": 0,
            "scheduled": 0,
        })

        for assignment in assignments:
            user = assignment.assigned_to
            data = employee_data[user.pk]
            data["user"] = user
            data["assigned"] += 1

            task = assignment.task
            status = task.status

            if status == Task.Status.COMPLETED:
                data["completed"] += 1
            elif status == Task.Status.IN_PROGRESS:
                data["in_progress"] += 1
            elif status == Task.Status.PROBLEM:
                data["problem"] += 1
            elif status == Task.Status.ERROR:
                data["error"] += 1
            elif status in (Task.Status.SCHEDULED, Task.Status.READY, Task.Status.ACCEPTED):
                data["scheduled"] += 1

            # ตรวจสอบ overdue
            if (task.deadline and task.deadline < now and
                    status not in (Task.Status.COMPLETED, Task.Status.CANCELLED)):
                data["overdue"] += 1

        return sorted(employee_data.values(), key=lambda x: x["assigned"], reverse=True)

    @staticmethod
    def get_employee_detail(user, date_from=None, date_to=None):
        """
        ดึงข้อมูลรายละเอียด workload ของ employee คนเดียว

        Returns:
            dict ของข้อมูล employee
        """
        if date_from is None:
            date_from = today_local()
        if date_to is None:
            date_to = date_from

        now = timezone.now()

        tasks = Task.objects.filter(
            assignments__assigned_to=user,
            task_date__gte=date_from,
            task_date__lte=date_to,
        ).distinct()

        total = tasks.count()
        completed = tasks.filter(status=Task.Status.COMPLETED).count()
        in_progress = tasks.filter(status=Task.Status.IN_PROGRESS).count()
        problem = tasks.filter(status=Task.Status.PROBLEM).count()
        error = tasks.filter(status=Task.Status.ERROR).count()

        overdue = tasks.filter(
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
        ).count()

        # รายงานปัญหาของ employee
        reports = TaskReport.objects.filter(
            reported_by=user,
            task__task_date__gte=date_from,
            task__task_date__lte=date_to,
        ).select_related("task")

        return {
            "user": user,
            "date_from": date_from,
            "date_to": date_to,
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "problem": problem,
            "error": error,
            "overdue": overdue,
            "reports": reports,
        }

    # === Team Overview ===

    @staticmethod
    def get_team_overview(target_date=None):
        """
        ดึงข้อมูล overview ของทุกทีม

        Returns:
            list ของ dict แต่ละ dict มีข้อมูลทีม 1 ทีม
        """
        if target_date is None:
            target_date = today_local()

        now = timezone.now()

        from accounts.models import Team

        teams = Team.objects.filter(is_active=True)
        result = []

        for team in teams:
            tasks = Task.objects.filter(
                team=team,
                task_date=target_date,
            ).exclude(status=Task.Status.CANCELLED)

            total = tasks.count()
            completed = tasks.filter(status=Task.Status.COMPLETED).count()
            in_progress = tasks.filter(status=Task.Status.IN_PROGRESS).count()
            problem = tasks.filter(status=Task.Status.PROBLEM).count()

            overdue = tasks.filter(
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
            ).count()

            result.append({
                "team": team,
                "total": total,
                "completed": completed,
                "in_progress": in_progress,
                "problem": problem,
                "overdue": overdue,
            })

        return result

    # === Task Monitoring (with filters) ===

    @staticmethod
    def get_filtered_tasks(date_from=None, date_to=None, employee=None,
                           team=None, status=None, priority=None,
                           search=None):
        """
        ดึง tasks ที่กรองตามเงื่อนไขต่างๆ

        Returns:
            QuerySet ของ Task
        """
        queryset = Task.objects.select_related(
            "team", "created_by"
        ).prefetch_related(
            "assignments__assigned_to", "reports"
        )

        # Filter by date range
        if date_from:
            queryset = queryset.filter(task_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(task_date__lte=date_to)

        # Filter by employee
        if employee:
            queryset = queryset.filter(assignments__assigned_to=employee)

        # Filter by team
        if team:
            queryset = queryset.filter(team=team)

        # Filter by status
        if status:
            queryset = queryset.filter(status=status)

        # Filter by priority
        if priority:
            queryset = queryset.filter(priority=priority)

        # Search by title or employee name
        if search:
            search_q = Q(title__icontains=search) | Q(
                assignments__assigned_to__first_name__icontains=search
            ) | Q(
                assignments__assigned_to__last_name__icontains=search
            ) | Q(
                assignments__assigned_to__email__icontains=search
            )
            queryset = queryset.filter(search_q)

        return queryset.distinct().order_by("task_date", "start_at", "queue_position")

    # === Task History ===

    @staticmethod
    def get_task_history(task):
        """
        ดึงประวัติทั้งหมดของ task

        Returns:
            QuerySet ของ TaskActivity
        """
        return TaskActivity.objects.filter(
            task=task
        ).select_related("user").order_by("created_at")

    @staticmethod
    def get_employee_task_history(user, date_from=None, date_to=None,
                                  status=None, priority=None):
        """
        ดึงประวัติงานของ employee

        Returns:
            QuerySet ของ Task
        """
        queryset = Task.objects.filter(
            assignments__assigned_to=user
        ).select_related(
            "team", "created_by"
        ).prefetch_related(
            "assignments__assigned_to", "reports", "activities"
        ).distinct()

        if date_from:
            queryset = queryset.filter(task_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(task_date__lte=date_to)
        if status:
            queryset = queryset.filter(status=status)
        if priority:
            queryset = queryset.filter(priority=priority)

        return queryset.order_by("-task_date", "-start_at")

    # === Reports ===

    @staticmethod
    def get_daily_report(target_date=None):
        """
        รายงานประจำวัน

        Returns:
            dict ของรายงาน
        """
        if target_date is None:
            target_date = today_local()

        now = timezone.now()

        tasks = Task.objects.filter(task_date=target_date).exclude(
            status=Task.Status.CANCELLED
        )

        total = tasks.count()
        completed = tasks.filter(status=Task.Status.COMPLETED).count()
        incomplete = total - completed
        overdue = tasks.filter(
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
        ).count()

        # ปัญหาและข้อผิดพลาด
        problem_tasks = tasks.filter(status=Task.Status.PROBLEM).count()
        error_tasks = tasks.filter(status=Task.Status.ERROR).count()

        # รายงานปัญหาที่เกิดขึ้นวันนี้
        reports = TaskReport.objects.filter(
            task__task_date=target_date
        ).select_related("task", "reported_by")

        return {
            "target_date": target_date,
            "total": total,
            "completed": completed,
            "incomplete": incomplete,
            "overdue": overdue,
            "problem_tasks": problem_tasks,
            "error_tasks": error_tasks,
            "reports": reports,
        }

    @staticmethod
    def get_employee_report(user, date_from=None, date_to=None):
        """
        รายงานของ employee คนเดียว

        Returns:
            dict ของรายงาน
        """
        if date_from is None:
            date_from = today_local()
        if date_to is None:
            date_to = date_from

        now = timezone.now()

        tasks = Task.objects.filter(
            assignments__assigned_to=user,
            task_date__gte=date_from,
            task_date__lte=date_to,
        ).distinct()

        total = tasks.count()
        completed = tasks.filter(status=Task.Status.COMPLETED).count()
        incomplete = total - completed
        overdue = tasks.filter(
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
        ).count()

        # รายงานปัญหา
        problem_reports = TaskReport.objects.filter(
            reported_by=user,
            task__task_date__gte=date_from,
            task__task_date__lte=date_to,
        )

        return {
            "user": user,
            "date_from": date_from,
            "date_to": date_to,
            "total": total,
            "completed": completed,
            "incomplete": incomplete,
            "overdue": overdue,
            "problem_reports": problem_reports,
        }

    @staticmethod
    def get_status_report(date_from=None, date_to=None):
        """
        รายงานสถานะงาน

        Returns:
            dict ของ统计数据
        """
        queryset = Task.objects.all()

        if date_from:
            queryset = queryset.filter(task_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(task_date__lte=date_to)

        # นับตามสถานะ (รวม CANCELLED)
        status_counts = {}
        for status_value, status_label in Task.Status.choices:
            status_counts[status_value] = {
                "label": status_label,
                "count": queryset.filter(status=status_value).count(),
            }

        total = queryset.exclude(status=Task.Status.CANCELLED).count()

        return {
            "date_from": date_from,
            "date_to": date_to,
            "total": total,
            "status_counts": status_counts,
        }

    # === Performance Metrics ===

    @staticmethod
    def get_performance_metrics(date_from=None, date_to=None):
        """
        คำนวณ performance metrics

        Returns:
            dict ของ metrics
        """
        if date_from is None:
            date_from = today_local()
        if date_to is None:
            date_to = date_from

        now = timezone.now()

        completed_tasks = Task.objects.filter(
            status=Task.Status.COMPLETED,
            task_date__gte=date_from,
            task_date__lte=date_to,
        )

        total_completed = completed_tasks.count()

        # Completion Rate: Completed / Total (non-cancelled)
        all_tasks = Task.objects.filter(
            task_date__gte=date_from,
            task_date__lte=date_to,
        ).exclude(status=Task.Status.CANCELLED)
        total_tasks = all_tasks.count()

        completion_rate = 0
        if total_tasks > 0:
            completion_rate = round((total_completed / total_tasks) * 100, 1)

        # On-Time Completion Rate
        on_time_completed = completed_tasks.filter(
            Q(deadline__gte=F("completed_at")) | Q(deadline__isnull=True)
        ).count()

        on_time_rate = 0
        if total_completed > 0:
            on_time_rate = round((on_time_completed / total_completed) * 100, 1)

        # Problem Rate
        total_with_problems = TaskReport.objects.filter(
            task__task_date__gte=date_from,
            task__task_date__lte=date_to,
            report_type=TaskReport.ReportType.PROBLEM,
        ).values("task").distinct().count()

        problem_rate = 0
        if total_tasks > 0:
            problem_rate = round((total_with_problems / total_tasks) * 100, 1)

        # Error Rate
        total_with_errors = TaskReport.objects.filter(
            task__task_date__gte=date_from,
            task__task_date__lte=date_to,
            report_type=TaskReport.ReportType.ERROR,
        ).values("task").distinct().count()

        error_rate = 0
        if total_tasks > 0:
            error_rate = round((total_with_errors / total_tasks) * 100, 1)

        # Average completion time (นาที)
        avg_completion = completed_tasks.filter(
            actual_minutes__isnull=False
        ).aggregate(avg=Avg("actual_minutes"))["avg"]

        return {
            "date_from": date_from,
            "date_to": date_to,
            "total_tasks": total_tasks,
            "total_completed": total_completed,
            "completion_rate": completion_rate,
            "on_time_rate": on_time_rate,
            "problem_rate": problem_rate,
            "error_rate": error_rate,
            "avg_completion_minutes": round(avg_completion, 1) if avg_completion else None,
        }

    # === CSV Export ===

    @staticmethod
    def get_csv_data(date_from=None, date_to=None, employee=None,
                     team=None, status=None):
        """
        ดึงข้อมูลสำหรับ export CSV

        Returns:
            list ของ dict (แต่ละ dict = 1 row)
        """
        tasks = ReportingService.get_filtered_tasks(
            date_from=date_from,
            date_to=date_to,
            employee=employee,
            team=team,
            status=status,
        )

        rows = []
        for task in tasks:
            assignees = ", ".join([
                a.assigned_to.display_name
                for a in task.assignments.all()
            ])

            delay_minutes = None
            if task.completed_at and task.deadline:
                if task.completed_at > task.deadline:
                    delay_minutes = int(
                        (task.completed_at - task.deadline).total_seconds() / 60
                    )

            rows.append({
                "id": task.pk,
                "title": task.title,
                "category": task.get_category_display(),
                "priority": task.get_priority_display(),
                "status": task.get_status_display(),
                "task_date": task.task_date.strftime("%Y-%m-%d") if task.task_date else "",
                "start_at": task.start_at.strftime("%H:%M") if task.start_at else "",
                "deadline": task.deadline.strftime("%H:%M") if task.deadline else "",
                "completed_at": task.completed_at.strftime("%H:%M") if task.completed_at else "",
                "assignees": assignees,
                "team": task.team.name if task.team else "",
                "estimated_minutes": task.estimated_minutes or "",
                "actual_minutes": task.actual_minutes or "",
                "delay_minutes": delay_minutes if delay_minutes is not None else "",
                "is_overdue": "ใช่" if task.is_overdue else "ไม่",
            })

        return rows

    @staticmethod
    def get_csv_headers():
        """_HEADERS สำหรับ CSV export"""
        return [
            "รหัส",
            "ชื่องาน",
            "หมวดหมู่",
            "ความสำคัญ",
            "สถานะ",
            "วันที่",
            "เวลาเริ่ม",
            "กำหนดส่ง",
            "เวลาเสร็จ",
            "ผู้รับผิดชอบ",
            "ทีม",
            "เวลาคาด (นาที)",
            "เวลาจริง (นาที)",
            "ความล่าช้า (นาที)",
            "เกินกำหนด",
        ]
