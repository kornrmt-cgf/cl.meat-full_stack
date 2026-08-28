"""
Planning Template Views.
"""
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import datetime
from .models import RotationPlan, ThawQueueEntry, FreezeProfile, ThawProfile
from .forms import RotationPlanForm, RotationPlanEditForm, ThawQueueForm
from .selectors import get_all_plans, get_queue, get_available_packages, get_active_profiles


@login_required
def plan_list(request):
    """List all rotation plans with filters."""
    status_filter = request.GET.get('status', '')
    product_filter = request.GET.get('product', '')
    package_state_filter = request.GET.get('package_state', '')

    plans = get_all_plans()
    if status_filter:
        plans = plans.filter(status=status_filter)
    if product_filter:
        plans = plans.filter(package__product_id=product_filter)
    if package_state_filter:
        plans = plans.filter(package__current_state=package_state_filter)

    from inventory.models import Product, PackageState
    products = Product.objects.filter(active=True).order_by('name')
    package_states = [c for c in PackageState.choices]

    context = {
        'plans': plans,
        'current_status_filter': status_filter,
        'current_product_filter': product_filter,
        'current_package_state_filter': package_state_filter,
        'products': products,
        'package_states': package_states,
        'can_edit': request.user.has_perm('planning.change_rotationplan'),
    }
    return render(request, 'planning/plan_list.html', context)


@login_required
def plan_detail(request, pk):
    """Plan detail view."""
    plan = get_object_or_404(
        RotationPlan.objects.select_related('package', 'package__product', 'freeze_profile', 'thaw_profile'),
        pk=pk
    )

    # Get queue entry if exists
    queue_entry = ThawQueueEntry.objects.filter(
        package=plan.package
    ).first()

    # Check data integrity
    from inventory.models import PackageState
    integrity_warning = None
    if plan.package.current_state == PackageState.THAW_QUEUED:
        if not queue_entry or queue_entry.status not in ['QUEUED', 'READY_TO_START', 'STARTED']:
            integrity_warning = '⚠️ DATA INTEGRITY: Package is THAW_QUEUED but has no active queue entry.'
    elif plan.package.current_state == PackageState.THAWING:
        if not RotationPlan.objects.filter(package=plan.package).exists():
            integrity_warning = '⚠️ DATA INTEGRITY: Package is THAWING but has no RotationPlan.'

    context = {
        'plan': plan,
        'queue_entry': queue_entry,
        'integrity_warning': integrity_warning,
    }
    return render(request, 'planning/plan_detail.html', context)


@login_required
def plan_create(request):
    """Create a new rotation plan."""
    if not request.user.has_perm('planning.add_rotationplan'):
        messages.error(request, 'คุณไม่มีสิทธิ์สร้างแผนงาน')
        return redirect('planning:plan_list')

    if request.method == 'POST':
        try:
            from .services import create_rotation_plan, calculate_rotation_plan
            from inventory.models import Package, PackageState
            from .stock_service import check_plan_conflicts, get_barcode_package_eligibility

            # Get form fields from POST
            package_id = request.POST.get('package')
            freeze_profile_id = request.POST.get('freeze_profile')
            thaw_profile_id = request.POST.get('thaw_profile')
            target_date_str = request.POST.get('target_ready_date', '')
            target_time_str = request.POST.get('target_ready_time', '08:00')

            # Validate all fields present
            if not all([package_id, freeze_profile_id, thaw_profile_id, target_date_str]):
                messages.error(request, 'กรุณากรอกข้อมูลให้ครบทุกช่อง')
                return _render_create_form(request)

            # Validate package
            try:
                package = Package.objects.select_related('product').get(pk=package_id)
            except Package.DoesNotExist:
                messages.error(request, 'ไม่พบแพ็กเกจที่เลือก')
                return _render_create_form(request)

            if package.current_state != PackageState.FROZEN:
                messages.error(request, f'สถานะสินค้าไม่ถูกต้อง (ต้องเป็น FROZEN, ปัจจุบัน: {package.get_current_state_display()})')
                return _render_create_form(request)

            if RotationPlan.objects.filter(package=package).exists():
                messages.error(request, 'สินค้าชิ้นนี้มีแผนงานอยู่แล้ว')
                return _render_create_form(request)

            # Validate profiles
            try:
                freeze_profile = FreezeProfile.objects.get(pk=freeze_profile_id, active=True)
            except FreezeProfile.DoesNotExist:
                messages.error(request, 'ไม่พบโปรไฟล์แช่แข็ง')
                return _render_create_form(request)

            try:
                thaw_profile = ThawProfile.objects.get(pk=thaw_profile_id, active=True)
            except ThawProfile.DoesNotExist:
                messages.error(request, 'ไม่พบโปรไฟล์ละลาย')
                return _render_create_form(request)

            # Parse target date/time
            try:
                target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
                target_time = datetime.strptime(target_time_str, '%H:%M').time()
                target_ready_at = timezone.make_aware(datetime.combine(target_date, target_time))
            except (ValueError, TypeError):
                messages.error(request, 'รูปแบบวันที่หรือเวลาไม่ถูกต้อง')
                return _render_create_form(request)

            # Check conflicts
            warnings = check_plan_conflicts(package.product, target_ready_at)

            # Create plan
            plan = create_rotation_plan(
                package=package,
                target_ready_at=target_ready_at,
                freeze_profile=freeze_profile,
                thaw_profile=thaw_profile,
                actor=request.user.get_full_name() or request.user.username
            )

            messages.success(request, f'สร้างแผนงาน #{plan.id} สำเร็จ ({package.display_name} พร้อม {target_ready_at.strftime("%d/%m/%Y %H:%M")})')
            return redirect('planning:plan_detail', pk=plan.pk)

        except Exception as e:
            messages.error(request, f'เกิดข้อผิดพลาด: {str(e)}')
            return _render_create_form(request)
    else:
        preselected_product_id = request.GET.get('product_id')
        return _render_create_form(request, preselected_product_id)


def _render_create_form(request, preselected_product_id=None):
    """Helper: render the demand-first planning form."""
    from inventory.models import Product
    products = Product.objects.filter(active=True).order_by('name')
    freeze_profiles = FreezeProfile.objects.filter(active=True).order_by('name')
    thaw_profiles = ThawProfile.objects.filter(active=True).order_by('name')
    context = {
        'title': 'สร้างแผนงานหมุนเวียน',
        'products': products,
        'freeze_profiles': freeze_profiles,
        'thaw_profiles': thaw_profiles,
        'preselected_product_id': preselected_product_id,
    }
    return render(request, 'planning/plan_form.html', context)


@login_required
def plan_edit(request, pk):
    """Edit a rotation plan — recalculate timestamps on target_ready_at change."""
    if not request.user.has_perm('planning.change_rotationplan'):
        messages.error(request, 'คุณไม่มีสิทธิ์แก้ไขแผนงาน')
        return redirect('planning:plan_list')

    plan = get_object_or_404(RotationPlan.objects.select_related(
        'package', 'package__product', 'freeze_profile', 'thaw_profile'
    ), pk=pk)

    # Protect active operations — warn but allow admin override
    from inventory.models import PackageState
    if plan.package.current_state in [PackageState.THAWING, PackageState.THAW_QUEUED]:
        if not request.user.has_perm('planning.delete_rotationplan'):  # Admin only
            messages.error(
                request,
                'ไม่สามารถแก้ไขแผนงานได้: สินค้ากำลังอยู่ในกระบวนการละลายน้ำแข็ง '
                '(ต้องมีสิทธิ์ Admin เพื่อแก้ไขแผนงานที่กำลังดำเนินการอยู่)'
            )
            return redirect('planning:plan_detail', pk=plan.pk)

    if request.method == 'POST':
        form = RotationPlanEditForm(request.POST, instance=plan)
        if form.is_valid():
            try:
                from .services import calculate_rotation_plan
                from django.db import transaction

                with transaction.atomic():
                    # Save old values for audit
                    old_target = plan.target_ready_at
                    old_freeze_profile = plan.freeze_profile
                    old_thaw_profile = plan.thaw_profile

                    # Update fields
                    plan.freeze_profile = form.cleaned_data['freeze_profile']
                    plan.thaw_profile = form.cleaned_data['thaw_profile']
                    plan.status = form.cleaned_data['status']

                    # Recalculate from new target_ready_at
                    target_date = form.cleaned_data['target_ready_date']
                    target_time = form.cleaned_data['target_ready_time']
                    new_target = datetime.combine(target_date, target_time)
                    new_target = timezone.make_aware(new_target)
                    plan.target_ready_at = new_target

                    # Recalculate all dependent timestamps
                    plan_data = calculate_rotation_plan(
                        plan.package,
                        plan.target_ready_at,
                        plan.freeze_profile,
                        plan.thaw_profile
                    )
                    plan.planned_thaw_start_at = plan_data['planned_thaw_start_at']
                    plan.planned_thaw_queue_at = plan_data['planned_thaw_queue_at']
                    plan.planned_freeze_start_at = plan_data['planned_freeze_start_at']
                    plan.planned_freeze_end_at = plan_data['planned_freeze_end_at']
                    plan.freeze_duration = plan_data['freeze_duration']
                    plan.thaw_duration = plan_data['thaw_duration']

                    plan.save()

                    # Audit event
                    from operations.models import RotationEvent
                    changes = []
                    if old_target != plan.target_ready_at:
                        changes.append(f'target_ready: {old_target} → {plan.target_ready_at}')
                    if old_freeze_profile != plan.freeze_profile:
                        changes.append(f'freeze_profile: {old_freeze_profile} → {plan.freeze_profile}')
                    if old_thaw_profile != plan.thaw_profile:
                        changes.append(f'thaw_profile: {old_thaw_profile} → {plan.thaw_profile}')

                    RotationEvent.objects.create(
                        package=plan.package,
                        event_type='PLAN_EDITED',
                        from_state=plan.package.current_state,
                        to_state=plan.package.current_state,
                        timestamp=timezone.now(),
                        actor=request.user.get_full_name() or request.user.username,
                        reason=f'แก้ไขแผนงาน: {"; ".join(changes) if changes else "status change"}',
                    )

                messages.success(request, f'แก้ไขแผนงาน #{plan.id} สำเร็จ — คำนวณเวลาใหม่แล้ว')
                return redirect('planning:plan_detail', pk=plan.pk)

            except Exception as e:
                messages.error(request, f'เกิดข้อผิดพลาด: {str(e)}')
    else:
        form = RotationPlanEditForm(instance=plan, initial={
            'target_ready_date': plan.target_ready_at.date(),
            'target_ready_time': plan.target_ready_at.time(),
        })

    context = {
        'form': form,
        'plan': plan,
        'title': f'แก้ไขแผนงาน #{plan.id}',
    }
    return render(request, 'planning/plan_edit.html', context)


@login_required
def monthly_planner(request):
    """Monthly planner view."""
    year = int(request.GET.get('year', timezone.now().year))
    month = int(request.GET.get('month', timezone.now().month))

    from .selectors import get_calendar_data

    calendar_data = get_calendar_data(year, month)

    context = {
        'year': year,
        'month': month,
        'calendar_data': calendar_data,
    }
    return render(request, 'planning/monthly_planner.html', context)


@login_required
def queue_view(request):
    """Thaw queue management view."""
    queue_entries = get_queue()
    context = {
        'queue_entries': queue_entries,
    }
    return render(request, 'planning/queue.html', context)


@login_required
def queue_detail(request, pk):
    """Thaw queue detail view."""
    entry = get_object_or_404(
        ThawQueueEntry.objects.select_related(
            'package', 'package__product', 'rotation_plan'
        ), pk=pk
    )
    context = {
        'entry': entry,
        'can_edit': request.user.is_superuser or request.user.has_perm('planning.change_thawqueueentry'),
    }
    return render(request, 'planning/queue_detail.html', context)


@login_required
def queue_edit(request, pk):
    """Edit thaw queue entry — only scheduling info, not operational state."""
    if not (request.user.is_superuser or request.user.has_perm('planning.change_thawqueueentry')):
        messages.error(request, 'คุณไม่มีสิทธิ์แก้ไขคิวละลาย')
        return redirect('planning:queue')

    entry = get_object_or_404(ThawQueueEntry.objects.select_related(
        'package', 'package__product', 'rotation_plan'
    ), pk=pk)

    # Cannot edit completed/cancelled entries
    if entry.status in ['COMPLETED', 'CANCELLED']:
        messages.error(request, 'ไม่สามารถแก้ไขรายการที่เสร็จสิ้นหรือยกเลิกแล้ว')
        return redirect('planning:queue_detail', pk=pk)

    if request.method == 'POST':
        try:
            from django.utils.dateparse import parse_datetime
            new_start = parse_datetime(request.POST.get('planned_start_at', ''))
            new_target = parse_datetime(request.POST.get('target_ready_at', ''))
            if new_start:
                entry.planned_start_at = new_start
            if new_target:
                entry.target_ready_at = new_target
            entry.save()
            messages.success(request, f'แก้ไขคิว #{entry.queue_position} สำเร็จ')
            return redirect('planning:queue_detail', pk=pk)
        except Exception as e:
            messages.error(request, f'เกิดข้อผิดพลาด: {e}')

    context = {
        'entry': entry,
    }
    return render(request, 'planning/queue_edit.html', context)
