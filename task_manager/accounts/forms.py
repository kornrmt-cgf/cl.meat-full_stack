"""
Forms สำหรับ accounts app
"""

from django import forms
from django.contrib.auth import authenticate

from .models import User


class LoginForm(forms.Form):
    """ฟอร์มเข้าสู่ระบบ — userid + password"""

    userid = forms.CharField(
        label="รหัสผู้ใช้",
        widget=forms.TextInput(
            attrs={
                "class": "form-input",
                "placeholder": "รหัสผู้ใช้",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label="รหัสผ่าน",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-input",
                "placeholder": "รหัสผ่าน",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        userid = cleaned_data.get("userid")
        password = cleaned_data.get("password")

        if userid and password:
            self.user = authenticate(userid=userid, password=password)
            if self.user is None:
                raise forms.ValidationError("รหัสผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
        return cleaned_data

    def get_user(self):
        return getattr(self, "user", None)


class RegisterForm(forms.ModelForm):
    """ฟอร์มสมัครพนักงานใหม่"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = False
        self.fields["phone"].required = False

    password = forms.CharField(
        label="รหัสผ่าน",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-input",
                "placeholder": "รหัสผ่าน (อย่างน้อย 8 ตัวอักษร)",
            }
        ),
        min_length=8,
    )
    password_confirm = forms.CharField(
        label="ยืนยันรหัสผ่าน",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-input",
                "placeholder": "ยืนยันรหัสผ่าน",
            }
        ),
    )

    class Meta:
        model = User
        fields = ["userid", "first_name", "last_name", "email", "phone"]
        widgets = {
            "userid": forms.TextInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "ตั้งรหัสผู้ใช้ (เช่น somchai)",
                }
            ),
            "first_name": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "ชื่อ"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "นามสกุล"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "form-input", "placeholder": "อีเมล (ไม่บังคับ)", "required": False}
            ),
            "phone": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "เบอร์โทรศัพท์ (ไม่บังคับ)", "required": False}
            ),
        }

    def clean_userid(self):
        userid = self.cleaned_data.get("userid", "").strip()
        if not userid:
            raise forms.ValidationError("กรุณากรอกรหัสผู้ใช้")
        if len(userid) < 3:
            raise forms.ValidationError("รหัสผู้ใช้ต้องมีอย่างน้อย 3 ตัวอักษร")
        if User.objects.filter(userid=userid).exists():
            raise forms.ValidationError("รหัสผู้ใช้นี้ถูกใช้แล้ว")
        return userid

    def clean_password_confirm(self):
        password = self.cleaned_data.get("password")
        confirm = self.cleaned_data.get("password_confirm")
        if password and confirm and password != confirm:
            raise forms.ValidationError("รหัสผ่านไม่ตรงกัน")
        return confirm

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    """ฟอร์มแก้ไขโปรไฟล์"""

    THEME_CHOICES = [
        ("dark", "🌙 มืด"),
        ("light", "☀️ สว่าง"),
        ("system", "💻 ตามระบบ"),
    ]

    theme = forms.ChoiceField(
        choices=THEME_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "theme-radio"}),
        required=False,
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-input", "placeholder": "ชื่อ"}),
            "last_name": forms.TextInput(attrs={"class": "form-input", "placeholder": "นามสกุล"}),
            "phone": forms.TextInput(attrs={"class": "form-input", "placeholder": "เบอร์โทรศัพท์"}),
            "email": forms.EmailInput(attrs={"class": "form-input", "placeholder": "อีเมล"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and hasattr(self.instance, "profile"):
            self.fields["theme"].initial = self.instance.profile.theme
