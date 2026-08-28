"""
Forms สำหรับ scheduling app
"""

from django import forms

from tasks.models import TaskTemplate


class TaskTemplateForm(forms.ModelForm):
    """ฟอร์มแม่แบบงาน"""

    class Meta:
        model = TaskTemplate
        fields = [
            "name",
            "description",
            "category",
            "priority",
            "estimated_minutes",
            "location",
            "notes",
            "is_open",
            "reward",
            "default_prepare_minutes_before",
            "default_duration_minutes",
            "recurrence_type",
            "recurrence_time",
            "default_team",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "ชื่อแม่แบบ"}),
            "description": forms.Textarea(attrs={"class": "form-input", "rows": 3, "placeholder": "รายละเอียด"}),
            "category": forms.Select(attrs={"class": "form-input"}),
            "priority": forms.Select(attrs={"class": "form-input"}),
            "estimated_minutes": forms.NumberInput(attrs={"class": "form-input", "min": "1"}),
            "location": forms.TextInput(attrs={"class": "form-input", "placeholder": "สถานที่"}),
            "notes": forms.Textarea(attrs={"class": "form-input", "rows": 2}),
            "default_prepare_minutes_before": forms.NumberInput(attrs={"class": "form-input", "min": "0"}),
            "default_duration_minutes": forms.NumberInput(attrs={"class": "form-input", "min": "1"}),
            "recurrence_type": forms.Select(attrs={"class": "form-input"}),
            "recurrence_time": forms.TimeInput(attrs={"class": "form-input", "type": "time"}),
            "is_open": forms.CheckboxInput(attrs={"class": "employee-checkbox"}),
            "reward": forms.NumberInput(attrs={"class": "form-input", "min": "0", "step": "0.01", "placeholder": "0.00"}),
            "default_team": forms.Select(attrs={"class": "form-input"}),
        }
