"""
Operations Template Views.
"""
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import WorkerTask, TaskStatus
from .services import get_todays_tasks, get_task_history, complete_task
from .forms import TaskCompleteForm


@login_required
def today_view(request):
    """Today's tasks dashboard."""
    tasks = get_todays_tasks()
    
    # Categorize tasks
    upcoming = []
    due = []
    overdue = []
    
    from django.utils import timezone
    now = timezone.now()
    
    for task in tasks:
        if task.status == TaskStatus.OVERDUE:
            overdue.append(task)
        elif task.scheduled_at <= now:
            due.append(task)
        else:
            upcoming.append(task)
    
    context = {
        'upcoming': upcoming,
        'due': due,
        'overdue': overdue,
        'total_tasks': tasks.count(),
    }
    return render(request, 'operations/today.html', context)


@login_required
def history_view(request):
    """Task history view."""
    days = int(request.GET.get('days', 7))
    tasks = get_task_history(days=days)
    
    context = {
        'tasks': tasks,
        'days': days,
    }
    return render(request, 'operations/history.html', context)


@login_required
def task_detail(request, pk):
    """Task detail view."""
    task = get_object_or_404(
        WorkerTask.objects.select_related(
            'package', 'package__product', 'rotation_plan'
        ),
        pk=pk
    )
    
    # Get task events
    from .models import TaskEvent
    events = TaskEvent.objects.filter(task=task).order_by('timestamp')
    
    context = {
        'task': task,
        'events': events,
    }
    return render(request, 'operations/task_detail.html', context)


@login_required
def task_complete(request, pk):
    """Complete a worker task."""
    task = get_object_or_404(WorkerTask, pk=pk)
    
    show_temperature = task.task_type == 'FREEZE_CHECK'
    worker_name = request.user.get_full_name() or request.user.username
    
    if request.method == 'POST':
        form = TaskCompleteForm(request.POST, show_temperature=show_temperature)
        if form.is_valid():
            try:
                notes = form.cleaned_data.get('notes', '')
                temperature = form.cleaned_data.get('temperature')
                
                # Build notes with temperature if provided
                if temperature is not None:
                    temp_str = f"{temperature}°C"
                    # Validate against freeze profile target
                    plan = task.rotation_plan
                    if plan and plan.freeze_profile:
                        target = plan.freeze_profile.target_temperature
                        if temperature <= target:
                            temp_status = '✅ ผ่าน'
                        else:
                            temp_status = '⚠️ สูงกว่าเป้าหมาย'
                        temp_note = f"ตรวจอุณหภูมิ: {temp_str} (เป้าหมาย: {target}°C) {temp_status}"
                    else:
                        temp_note = f"ตรวจอุณหภูมิ: {temp_str}"
                    
                    if notes:
                        notes = f"{temp_note}\n\n{notes}"
                    else:
                        notes = temp_note
                    
                    # Also log temperature to TemperatureLog if package has storage location
                    if task.package.storage_location:
                        from inventory.models import TemperatureLog
                        plan_temp = plan.freeze_profile.target_temperature if plan and plan.freeze_profile else -18.0
                        status = 'OK'
                        if temperature > plan_temp + 2:
                            status = 'WARNING'
                        if temperature > plan_temp + 5:
                            status = 'CRITICAL'
                        TemperatureLog.objects.create(
                            location=task.package.storage_location,
                            actual_temperature=temperature,
                            target_temperature=plan_temp,
                            status=status,
                            source='MANUAL',
                            recorded_by=worker_name,
                        )
                
                result = complete_task(
                    task=task,
                    actor=worker_name,
                    notes=notes
                )
                transitions = result.get('transitions', [])
                if transitions:
                    transition_msgs = [f"{f} → {t}" for f, t in transitions]
                    messages.success(
                        request,
                        f'งานเสร็จสิ้น + อัพเดทสถานะ: {" → ".join(transition_msgs)}'
                    )
                else:
                    messages.success(request, 'งานเสร็จสิ้น')
                return redirect('operations:today')
            except ValueError as e:
                messages.error(request, str(e))
    else:
        form = TaskCompleteForm(
            initial={'completed_by': worker_name},
            show_temperature=show_temperature
        )
    
    context = {
        'task': task,
        'form': form,
        'worker_name': worker_name,
        'show_temperature': show_temperature,
        'freeze_target': task.rotation_plan.freeze_profile.target_temperature if task.rotation_plan and task.rotation_plan.freeze_profile else None,
    }
    return render(request, 'operations/task_complete.html', context)
