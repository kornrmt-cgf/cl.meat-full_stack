"""
User Management Views — Admin only.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django.contrib import messages
from django import forms


def is_admin(user):
    return user.is_authenticated and user.is_superuser


class AdminUserCreationForm(forms.ModelForm):
    """Admin form for creating users with role assignment."""
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        label='รหัสผ่าน'
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        label='ยืนยันรหัสผ่าน'
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('password') != cleaned_data.get('password_confirm'):
            raise forms.ValidationError('รหัสผ่านไม่ตรงกัน')
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    """Admin form for editing users."""
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active', 'is_superuser']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_superuser': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class SetPasswordForm(forms.Form):
    """Admin form for resetting a user's password."""
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        label='รหัสผ่านใหม่'
    )
    new_password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        label='ยืนยันรหัสผ่านใหม่'
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('new_password') != cleaned_data.get('new_password_confirm'):
            raise forms.ValidationError('รหัสผ่านไม่ตรงกัน')
        return cleaned_data


@login_required
@user_passes_test(is_admin)
def user_list(request):
    """List all users — admin only."""
    users = User.objects.all().order_by('-date_joined')
    groups = Group.objects.all()

    context = {
        'users': users,
        'groups': groups,
    }
    return render(request, 'users/user_list.html', context)


@login_required
@user_passes_test(is_admin)
def user_create(request):
    """Create a new user — admin only."""
    if request.method == 'POST':
        form = AdminUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Assign group if provided
            group_name = request.POST.get('group', '')
            if group_name:
                try:
                    group = Group.objects.get(name=group_name)
                    user.groups.add(group)
                except Group.DoesNotExist:
                    pass
            messages.success(request, f'สร้างผู้ใช้ {user.username} สำเร็จ')
            return redirect('users:user_list')
    else:
        form = AdminUserCreationForm()

    groups = Group.objects.all()
    context = {
        'form': form,
        'groups': groups,
        'title': 'สร้างผู้ใช้ใหม่',
    }
    return render(request, 'users/user_form.html', context)


@login_required
@user_passes_test(is_admin)
def user_edit(request, pk):
    """Edit a user — admin only."""
    user_obj = get_object_or_404(User, pk=pk)

    if request.method == 'POST':
        form = UserEditForm(request.POST, instance=user_obj)
        if form.is_valid():
            form.save()
            # Update group
            group_name = request.POST.get('group', '')
            user_obj.groups.clear()
            if group_name:
                try:
                    group = Group.objects.get(name=group_name)
                    user_obj.groups.add(group)
                except Group.DoesNotExist:
                    pass
            messages.success(request, f'แก้ไขผู้ใช้ {user_obj.username} สำเร็จ')
            return redirect('users:user_list')
    else:
        form = UserEditForm(instance=user_obj)

    current_group = user_obj.groups.first()
    groups = Group.objects.all()
    context = {
        'form': form,
        'user_obj': user_obj,
        'groups': groups,
        'current_group': current_group,
        'title': f'แก้ไขผู้ใช้ {user_obj.username}',
    }
    return render(request, 'users/user_form.html', context)


@login_required
@user_passes_test(is_admin)
def user_reset_password(request, pk):
    """Reset a user's password — admin only."""
    user_obj = get_object_or_404(User, pk=pk)

    if request.method == 'POST':
        form = SetPasswordForm(request.POST)
        if form.is_valid():
            user_obj.set_password(form.cleaned_data['new_password'])
            user_obj.save()
            messages.success(request, f'เปลี่ยนรหัสผ่านของ {user_obj.username} สำเร็จ')
            return redirect('users:user_list')
    else:
        form = SetPasswordForm()

    context = {
        'form': form,
        'user_obj': user_obj,
        'title': f'เปลี่ยนรหัสผ่าน {user_obj.username}',
    }
    return render(request, 'users/user_reset_password.html', context)


@login_required
@user_passes_test(is_admin)
def user_toggle_active(request, pk):
    """Toggle user active status — admin only."""
    user_obj = get_object_or_404(User, pk=pk)
    if user_obj == request.user:
        messages.error(request, 'ไม่สามารถปิดการใช้งานตัวเองได้')
        return redirect('users:user_list')

    user_obj.is_active = not user_obj.is_active
    user_obj.save(update_fields=['is_active'])
    status = 'เปิดใช้งาน' if user_obj.is_active else 'ปิดการใช้งาน'
    messages.success(request, f'{status}ผู้ใช้ {user_obj.username} สำเร็จ')
    return redirect('users:user_list')
