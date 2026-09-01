"""
Views สำหรับ Worker Operations — หน้าจอปฏิบัติงานสำหรับพนักงาน

 workflow:
 Login → งานวันนี้ → รายละเอียดงาน → รับงาน → เริ่มงาน → สแกนบาร์โค้ด → เสร็จงาน → แสดงผลลัพธ์

All views call the service layer. ไม่ mutate Package/WorkerTask โดยตรงจาก view.
"""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, TemplateView

from inventory.models import Package, PackageState
from operations.models import (
    TaskEvent,
    TaskStatus,
    TaskType,
    WorkerTask,
)
from operations import services

User = get_user_model()

# ============================================================
# Thai labels for task types / statuses
# ============================================================

TASK_TYPE_LABELS = {
    TaskType.FREEZE_START: 'เริ่มแช่แข็ง',
    TaskType.FREEZE_CHECK: 'ตรวจสอบการแช่แข็ง',
    TaskType.MOVE_TO_THAW_QUEUE: 'ย้ายเข้าคิวละลาย',
    TaskType.THAW_START: 'เริ่มละลาย',
    TaskType.THAW_CHECK: 'ตรวจสอบการละลาย',
    TaskType.THAW_COMPLETE: 'ละลายเสร็จ',
    TaskType.MOVE_TO_DISPLAY: 'ย้ายไปแสดงขาย',
    TaskType.REFREEZE: 'แช่แข็งซ้ำ',
    TaskType.PROCESS: 'แปรรูป',
    TaskType.DISCARD: 'ทิ้ง',
}

TASK_STATUS_LABELS = {
    TaskStatus.PENDING: 'งานที่รอดำเนินการ',
    TaskStatus.CLAIMED: 'กำลังดำเนินการ',
    TaskStatus.IN_PROGRESS: 'กำลังทำงาน',
    TaskStatus.COMPLETED: 'เสร็จสิ้น',
    TaskStatus.CANCELLED: 'ยกเลิก',
    TaskStatus.SKIPPED: 'ไม่สามารถดำเนินการได้',
    TaskStatus.OVERDUE: 'งานหมดเวลา',
}

ACTION_BUTTONS = {
    TaskType.FREEZE_START: ('🧊 เริ่มแช่แข็ง', 'ยืนยันการเริ่มแช่แข็ง'),
    TaskType.FREEZE_CHECK: ('🔍 ตรวจสอบ', 'ยืนยันการตรวจสอบ'),
    TaskType.MOVE_TO_THAW_QUEUE: ('📋 เข้าคิว', 'ยืนยันการเข้าคิวละลาย'),
    TaskType.THAW_START: ('💧 เริ่มละลาย', 'ยืนยันการเริ่มละลาย'),
    TaskType.THAW_CHECK: ('🔍 ตรวจสอบ', 'ยืนยันการตรวจสอบ'),
    TaskType.THAW_COMPLETE: ('✅ ละลายเสร็จ', 'ยืนยันว่าละลายเสร็จแล้ว'),
    TaskType.MOVE_TO_DISPLAY: ('🛒 ย้ายไปแสดง', 'ยืนยันการย้ายไปแสดงขาย'),
    TaskType.REFREEZE: ('🧊 แช่แข็งซ้ำ', 'ยืนยันการแช่แข็งซ้ำ'),
    TaskType.PROCESS: ('🔪 แปรรูป', 'ยืนยันการแปรรูป'),
    TaskType.DISCARD: ('🗑️ ทิ้ง', 'ยืนยันการทิ้ง'),
}

SUPPORTED_TASK_TYPES = set(services.TASK_DISPATCH.keys())

TASK_ORDERING = ['scheduled_at', 'created_at', 'pk']


GENERIC_THAI_ERROR = 'เกิดข้อผิดพลาดในการดำเนินงาน กรุณาลองใหม่อีกครั้ง'


def _thai_error(error_msg):
    """Convert backend errors to Thai UX messages.

    Never fall back to raw backend exception text.
    Unknown errors return a generic Thai message.
    """
    mapping = {
        'claimed by different worker': 'งานนี้ถูกพนักงานคนอื่นรับไปแล้ว',
        'status is': 'สถานะงานไม่ถูกต้อง',
        'CLAIMED task has no claimant': 'งานนี้ไม่มีผู้รับงาน',
        'Worker identity is required': 'กรุณาเข้าสู่ระบบ',
        'No handler for task type': 'งานนี้ยังไม่พร้อมใช้งาน',
        'Task is stale': 'งานนี้ไม่สามารถดำเนินการได้ เนื่องจากสถานะแพ็กเกจเปลี่ยนไปแล้ว',
        'Cannot claim task': 'ไม่สามารถรับงานนี้ได้',
        'Cannot start task': 'ไม่สามารถเริ่มงานนี้ได้',
        'Cannot complete task': 'ไม่สามารถเสร็จงานนี้ได้',
        'Cannot cancel task': 'ไม่สามารถยกเลิกงานนี้ได้',
        'cancel only by claimant': 'เฉพาะผู้รับงานเท่านั้นที่สามารถยกเลิกงานนี้ได้',
        'String actor': 'ไม่สามารถใช้ข้อมูลนี้เป็นผู้รับงานได้',
        'must be a saved': 'ผู้รับงานต้องเป็นผู้ใช้ที่บันทึกไว้แล้ว',
        'must be a ': 'ผู้รับงานต้องเป็นผู้ใช้ที่ถูกต้อง',
    }
    msg = str(error_msg)
    for key, thai_msg in mapping.items():
        if key.lower() in msg.lower():
            return thai_msg
    return GENERIC_THAI_ERROR


# ============================================================
# WORKER TASK LIST — งานวันนี้
# ============================================================

class WorkerTaskListView(LoginRequiredMixin, TemplateView):
    """หน้าหลักของพนักงาน — แสดงงานวันนี้"""
    template_name = 'operations/task_list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        bangkok_today = timezone.localtime(now).date()
        start = timezone.make_aware(
            timezone.datetime.combine(bangkok_today, timezone.datetime.min.time())
        )
        end = start + timezone.timedelta(days=1)

        # งานทั้งหมดของวันนี้
        today_tasks = WorkerTask.objects.filter(
            scheduled_at__gte=start, scheduled_at__lt=end,
        ).select_related(
            'package', 'package__product', 'package__product__category',
            'rotation_plan', 'claimed_by', 'completed_by',
        ).order_by(*TASK_ORDERING)

        # แยกตามสถานะ
        ctx['pending'] = today_tasks.filter(status=TaskStatus.PENDING)
        ctx['claimed'] = today_tasks.filter(status=TaskStatus.CLAIMED)
        ctx['in_progress'] = today_tasks.filter(status=TaskStatus.IN_PROGRESS)
        ctx['completed'] = today_tasks.filter(status=TaskStatus.COMPLETED)
        ctx['cancelled'] = today_tasks.filter(
            status__in=[TaskStatus.CANCELLED, TaskStatus.SKIPPED, TaskStatus.OVERDUE]
        )

        ctx['today'] = bangkok_today
        ctx['total'] = today_tasks.count()
        ctx['user'] = self.request.user

        return ctx


# ============================================================
# WORKER TASK DETAIL — รายละเอียดงาน
# ============================================================

class WorkerTaskDetailView(LoginRequiredMixin, TemplateView):
    """รายละเอียดงาน + ปุ่มปฏิบัติงาน"""
    template_name = 'operations/task_detail.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        task = get_object_or_404(
            WorkerTask.objects.select_related(
                'package', 'package__product', 'package__product__category',
                'package__batch', 'package__batch__supplier',
                'package__storage_location',
                'rotation_plan', 'rotation_plan__thaw_profile',
                'claimed_by', 'completed_by',
            ),
            pk=self.kwargs['pk']
        )
        ctx['task'] = task
        ctx['user'] = self.request.user
        ctx['package'] = task.package
        ctx['product'] = task.package.product

        # Thai labels
        ctx['type_label'] = TASK_TYPE_LABELS.get(task.task_type, task.task_type)
        ctx['status_label'] = TASK_STATUS_LABELS.get(task.status, task.status)
        ctx['action_button'] = ACTION_BUTTONS.get(task.task_type)

        # Supported?
        ctx['is_supported'] = task.task_type in SUPPORTED_TASK_TYPES

        # Ownership
        ctx['is_mine'] = (
            task.claimed_by_id == self.request.user.pk
            or task.status == TaskStatus.PENDING
        )
        ctx['is_claimed_by_another'] = (
            task.status in [TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]
            and task.claimed_by_id is not None
            and task.claimed_by_id != self.request.user.pk
        )

        # Task events / audit trail
        ctx['events'] = task.events.order_by('timestamp')[:20]

        # Package state info
        ctx['package_state_display'] = task.package.get_current_state_display()

        return ctx


# ============================================================
# CLAIM TASK — รับงาน
# ============================================================

class WorkerClaimTaskView(LoginRequiredMixin, View):
    """รับงาน — POST request"""

    def post(self, request, pk):
        task = get_object_or_404(WorkerTask, pk=pk)
        try:
            services.claim_task(task, request.user)
            messages.success(request, f'✅ รับงานสำเร็จ: {TASK_TYPE_LABELS.get(task.task_type, task.task_type)}')
            return redirect('operations:task-detail', pk=task.pk)
        except (ValueError, PermissionError) as e:
            messages.error(request, f'❌ {_thai_error(e)}')
            return redirect('operations:task-detail', pk=task.pk)


# ============================================================
# START TASK — เริ่มงาน
# ============================================================

class WorkerStartTaskView(LoginRequiredMixin, View):
    """เริ่มงาน — POST request"""

    def post(self, request, pk):
        task = get_object_or_404(WorkerTask, pk=pk)
        try:
            services.start_task(task, request.user)
            messages.success(request, f'▶️ เริ่มงานสำเร็จ: {TASK_TYPE_LABELS.get(task.task_type, task.task_type)}')
            return redirect('operations:task-detail', pk=task.pk)
        except (ValueError, PermissionError) as e:
            messages.error(request, f'❌ {_thai_error(e)}')
            return redirect('operations:task-detail', pk=task.pk)


# ============================================================
# COMPLETE TASK — เสร็จงาน
# ============================================================

class WorkerCompleteTaskView(LoginRequiredMixin, View):
    """เสร็จงาน — POST request with optional barcode + notes"""

    def post(self, request, pk):
        task = get_object_or_404(WorkerTask, pk=pk)
        notes = request.POST.get('notes', '')
        barcode = request.POST.get('barcode', '').strip()

        # Barcode validation: if provided, must match task's package
        if barcode:
            try:
                scanned = Package.objects.get(barcode=barcode)
            except Package.DoesNotExist:
                messages.error(request, '❌ ไม่พบบาร์โค้ดนี้ในระบบ')
                return redirect('operations:task-detail', pk=task.pk)

            if scanned.pk != task.package_id:
                messages.error(request, '❌ บาร์โค้ดไม่ตรงกับรายการงาน')
                return redirect('operations:task-detail', pk=task.pk)

        try:
            result = services.complete_task(
                task, worker=request.user, notes=notes
            )
            transitions = result.get('transitions', [])
            if transitions:
                msg = f'✅ เสร็จงานสำเร็จ: {" → ".join(t[1] for t in transitions)}'
            else:
                msg = '✅ เสร็จงานสำเร็จ'
            messages.success(request, msg)
            return redirect('operations:task-detail', pk=task.pk)
        except (ValueError, PermissionError) as e:
            messages.error(request, f'❌ {_thai_error(e)}')
            return redirect('operations:task-detail', pk=task.pk)


# ============================================================
# CANCEL TASK — ยกเลิกงาน
# ============================================================

class WorkerCancelTaskView(LoginRequiredMixin, View):
    """ยกเลิกงาน — POST request

    PENDING tasks: anyone may cancel.
    CLAIMED / IN_PROGRESS tasks: only the claimant may cancel.
    """

    def post(self, request, pk):
        task = get_object_or_404(WorkerTask, pk=pk)
        reason = request.POST.get('reason', '')

        # Ownership check for CLAIMED / IN_PROGRESS tasks
        if task.status in [TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]:
            if task.claimed_by_id != request.user.pk:
                messages.error(
                    request,
                    '❌ เฉพาะผู้รับงานเท่านั้นที่สามารถยกเลิกงานนี้ได้'
                )
                return redirect('operations:task-detail', pk=task.pk)

        try:
            services.cancel_task(task, actor=request.user, reason=reason)
            messages.info(request, 'ℹ️ ยกเลิกงานแล้ว')
            return redirect('operations:task-list')
        except (ValueError, PermissionError) as e:
            messages.error(request, f'❌ {_thai_error(e)}')
            return redirect('operations:task-detail', pk=task.pk)


# ============================================================
# TASK HISTORY — ประวัติงาน
# ============================================================

class WorkerTaskHistoryView(LoginRequiredMixin, ListView):
    """ประวัติงานของพนักงานคนนี้"""
    template_name = 'operations/task_history.html'
    context_object_name = 'tasks'
    paginate_by = 25

    def get_queryset(self):
        return WorkerTask.objects.filter(
            claimed_by=self.request.user,
            status__in=[
                TaskStatus.COMPLETED, TaskStatus.CANCELLED,
                TaskStatus.SKIPPED, TaskStatus.OVERDUE
            ]
        ).select_related(
            'package', 'package__product', 'package__product__category',
            'rotation_plan', 'completed_by',
        ).order_by('-completed_at', '-cancelled_at')


# ============================================================
# BARCODE SCAN — AJAX endpoint
# ============================================================

class BarcodeScanView(LoginRequiredMixin, View):
    """AJAX endpoint สำหรับสแกนบาร์โค้ด

    Security:
    - task_id is required (never expose arbitrary package data)
    - Only IN_PROGRESS tasks may be scanned
    - Only the claimant may scan
    - Barcode must match task.package_id
    """

    def post(self, request):
        barcode = request.POST.get('barcode', '').strip()
        task_id = request.POST.get('task_id')

        # task_id is required — never allow arbitrary package lookup
        if not task_id:
            return JsonResponse({
                'success': False,
                'error': 'กรุณาระบุรายการงาน'
            }, status=400)

        if not barcode:
            return JsonResponse({
                'success': False, 'error': 'กรุณากรอกบาร์โค้ด'
            }, status=400)

        # Load and validate the task
        try:
            task = WorkerTask.objects.select_related(
                'package', 'claimed_by',
            ).get(pk=task_id)
        except WorkerTask.DoesNotExist:
            return JsonResponse({
                'success': False, 'error': 'ไม่พบงานนี้'
            }, status=404)

        # Only IN_PROGRESS tasks may be scanned
        if task.status != TaskStatus.IN_PROGRESS:
            return JsonResponse({
                'success': False,
                'error': 'ไม่สามารถสแกนบาร์โค้ดได้ เนื่องจากงานยังไม่ได้เริ่มทำงาน'
            }, status=400)

        # Only the claimant may scan
        if task.claimed_by_id != request.user.pk:
            return JsonResponse({
                'success': False,
                'error': 'เฉพาะผู้รับงานเท่านั้นที่สามารถสแกนบาร์โค้ดได้'
            }, status=403)

        # Barcode must match the task's package
        if task.package.barcode != barcode:
            return JsonResponse({
                'success': False,
                'error': 'บาร์โค้ดไม่ตรงกับรายการงาน'
            }, status=400)

        # Return minimal package data (no arbitrary lookup)
        pkg = task.package
        return JsonResponse({
            'success': True,
            'task_match': True,
            'package': {
                'id': pkg.pk,
                'barcode': pkg.barcode,
                'product_name': pkg.product.display_name,
                'product_sku': pkg.product.sku,
                'category': pkg.product.category.name if pkg.product.category else '',
                'weight': str(pkg.weight),
                'selling_price': str(pkg.selling_price),
                'state': pkg.get_current_state_display(),
                'state_code': pkg.current_state,
                'storage_location': str(pkg.storage_location) if pkg.storage_location else '',
            },
        })


# ============================================================
# AJAX ENDPOINTS — สำหรับ HTMX / polling
# ============================================================

class TaskStatusAJAXView(LoginRequiredMixin, View):
    """AJAX endpoint สำหรับดึงสถานะงาน (for polling / HTMX)"""

    def get(self, request, pk):
        try:
            task = WorkerTask.objects.select_related(
                'package', 'claimed_by',
            ).get(pk=pk)
        except WorkerTask.DoesNotExist:
            return JsonResponse({'error': 'ไม่พบงาน'}, status=404)

        return JsonResponse({
            'task_id': task.pk,
            'status': task.status,
            'status_label': TASK_STATUS_LABELS.get(task.status, task.status),
            'claimed_by': str(task.claimed_by) if task.claimed_by else None,
            'claimed_by_id': task.claimed_by_id,
            'package_state': task.package.current_state,
            'is_overdue': task.is_overdue,
        })


class TaskListAJAXView(LoginRequiredMixin, View):
    """AJAX endpoint สำหรับ task list counts"""

    def get(self, request):
        now = timezone.now()
        bangkok_today = timezone.localtime(now).date()
        start = timezone.make_aware(
            timezone.datetime.combine(bangkok_today, timezone.datetime.min.time())
        )
        end = start + timezone.timedelta(days=1)

        today_tasks = WorkerTask.objects.filter(
            scheduled_at__gte=start, scheduled_at__lt=end,
        )

        return JsonResponse({
            'pending': today_tasks.filter(status=TaskStatus.PENDING).count(),
            'claimed': today_tasks.filter(status=TaskStatus.CLAIMED).count(),
            'in_progress': today_tasks.filter(status=TaskStatus.IN_PROGRESS).count(),
            'completed': today_tasks.filter(status=TaskStatus.COMPLETED).count(),
            'total': today_tasks.count(),
        })
