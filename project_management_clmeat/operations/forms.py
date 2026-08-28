"""
Forms for Operations management.
"""
from django import forms
from .models import WorkerTask, TaskEvent


class WorkerTaskForm(forms.ModelForm):
    """Form for editing worker tasks."""
    
    class Meta:
        model = WorkerTask
        fields = ['notes']
        widgets = {
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class TaskCompleteForm(forms.Form):
    """Form for completing a worker task."""
    
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'บันทึกเพิ่มเติม (ถ้ามี)...'})
    )
    completed_by = forms.CharField(
        max_length=100,
        widget=forms.HiddenInput()
    )
    temperature = forms.DecimalField(
        required=False,
        max_digits=5,
        decimal_places=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.1',
            'placeholder': '-18.0',
            'inputmode': 'decimal'
        }),
        label='อุณหภูมิปัจจุบัน (°C)'
    )

    def __init__(self, *args, **kwargs):
        show_temperature = kwargs.pop('show_temperature', False)
        super().__init__(*args, **kwargs)
        if not show_temperature:
            del self.fields['temperature']
