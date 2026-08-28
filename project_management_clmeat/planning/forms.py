"""
Forms for Planning management.
"""
from django import forms
from .models import FreezeProfile, ThawProfile, RotationPlan, ThawQueueEntry
from inventory.models import Package, PackageState


class FreezeProfileForm(forms.ModelForm):
    """Form for creating/editing freeze profiles."""

    class Meta:
        model = FreezeProfile
        fields = ['name', 'target_temperature', 'minimum_duration', 'default_duration', 'buffer_duration', 'active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'target_temperature': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'minimum_duration': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Hours'}),
            'default_duration': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Hours'}),
            'buffer_duration': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Hours'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ThawProfileForm(forms.ModelForm):
    """Form for creating/editing thaw profiles."""

    class Meta:
        model = ThawProfile
        fields = ['name', 'default_duration', 'minimum_duration', 'buffer_duration', 'active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'default_duration': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Hours'}),
            'minimum_duration': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Hours'}),
            'buffer_duration': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Hours'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


def _get_available_package_choices():
    """
    Get packages available for planning: FROZEN state + no existing RotationPlan.
    Returns formatted choices for select widgets.
    """
    packages = Package.objects.filter(
        current_state=PackageState.FROZEN
    ).exclude(
        rotation_plan__isnull=False
    ).select_related('product', 'batch').order_by('product__name', 'weight')

    choices = [('', '--- เลือกสินค้า ---')]
    for pkg in packages:
        barcode_info = f' | Barcode: {pkg.barcode}' if pkg.barcode else ''
        batch_info = f' | ล็อต: {pkg.batch.batch_number}' if pkg.batch else ''
        label = f"{pkg.product.name} | {pkg.weight} กก.{barcode_info}{batch_info} | FROZEN"
        choices.append((pkg.pk, label))
    return choices


class RotationPlanForm(forms.ModelForm):
    """Form for creating rotation plans."""

    target_ready_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    target_ready_time = forms.TimeField(
        widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'})
    )

    class Meta:
        model = RotationPlan
        fields = ['package', 'freeze_profile', 'thaw_profile']
        widgets = {
            'package': forms.Select(attrs={'class': 'form-select'}),
            'freeze_profile': forms.Select(attrs={'class': 'form-select'}),
            'thaw_profile': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['package'].choices = _get_available_package_choices()

    def clean_package(self):
        package = self.cleaned_data['package']
        # Server-side validation: must be FROZEN
        if package.current_state != PackageState.FROZEN:
            raise forms.ValidationError(
                f'Stํกต้องเป็น FROZEN เพื่อสร้างแผนงาน สถานะปัจจุบัน: {package.get_current_state_display()}'
            )
        # Server-side validation: must not already have a plan
        if RotationPlan.objects.filter(package=package).exists():
            raise forms.ValidationError(
                'สินค้าชิ้นนี้มีแผนงานอยู่แล้ว กรุณายกเลิกแผนเดิมก่อน'
            )
        return package


class RotationPlanEditForm(forms.ModelForm):
    """Form for editing rotation plans — only target_ready_at, profiles, status."""

    target_ready_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    target_ready_time = forms.TimeField(
        widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'})
    )

    class Meta:
        model = RotationPlan
        fields = ['freeze_profile', 'thaw_profile', 'status']
        widgets = {
            'freeze_profile': forms.Select(attrs={'class': 'form-select'}),
            'thaw_profile': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }


class ThawQueueForm(forms.Form):
    """Form for adding to thaw queue."""

    package = forms.ModelChoiceField(
        queryset=None,  # Set in __init__
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    target_ready_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    target_ready_time = forms.TimeField(
        widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['package'].queryset = Package.objects.filter(
            current_state=PackageState.FROZEN
        ).exclude(rotation_plan__isnull=False)
