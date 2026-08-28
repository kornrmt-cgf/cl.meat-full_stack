"""
Views สำหรับ accounts app

จัดการ:
- Login / Logout / Register
- Profile management
- Theme toggle
"""

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView, TemplateView, UpdateView

from .forms import LoginForm, ProfileForm, RegisterForm
from .models import EmployeeProfile, User


class LoginView(FormView):
    """หน้าเข้าสู่ระบบ"""

    template_name = "accounts/login.html"
    form_class = LoginForm

    def form_valid(self, form):
        user = form.get_user()
        login(self.request, user)
        messages.success(self.request, f"ยินดีต้อนรับ {user.display_name}!")
        return redirect("tasks:today")

    def dispatch(self, request, *args, **kwargs):
        # ถ้า login อยู่แล้ว redirect ไปหน้า tasks
        if request.user.is_authenticated:
            return redirect("tasks:today")
        return super().dispatch(request, *args, **kwargs)


class RegisterView(FormView):
    """หน้าสมัครพนักงานใหม่"""

    template_name = "accounts/register.html"
    form_class = RegisterForm

    def form_valid(self, form):
        user = form.save()
        # สร้าง EmployeeProfile อัตโนมัติ
        EmployeeProfile.objects.get_or_create(
            user=user,
            defaults={"status": "active"},
        )
        login(self.request, user)
        messages.success(
            self.request,
            f"สมัครสำเร็จ! ยินดีต้อนรับ {user.display_name} 🎉",
        )
        return redirect("tasks:today")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("tasks:today")
        return super().dispatch(request, *args, **kwargs)


class LogoutView(View):
    """ออกจากระบบ"""

    def get(self, request):
        logout(request)
        messages.info(request, "ออกจากระบบแล้ว")
        return redirect("accounts:login")

    def post(self, request):
        logout(request)
        messages.info(request, "ออกจากระบบแล้ว")
        return redirect("accounts:login")


class ProfileView(LoginRequiredMixin, TemplateView):
    """หน้าโปรไฟล์"""

    template_name = "accounts/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["profile"] = getattr(self.request.user, "profile", None)
        return context


class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    """แก้ไขโปรไฟล์"""

    model = User
    form_class = ProfileForm
    template_name = "accounts/profile_edit.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        response = super().form_valid(form)

        # อัพเดท profile ถ้ามี
        profile = getattr(self.request.user, "profile", None)
        if profile:
            profile.theme = form.cleaned_data.get("theme", profile.theme)
            profile.save(update_fields=["theme"])

        messages.success(self.request, "อัพเดทโปรไฟล์สำเร็จ!")
        return response


class ThemeToggleView(LoginRequiredMixin, View):
    """สลับธีม (HTMX endpoint)"""

    def post(self, request):
        profile = getattr(request.user, "profile", None)
        if profile:
            current = profile.theme
            new_theme = "light" if current == "dark" else "dark"
            profile.theme = new_theme
            profile.save(update_fields=["theme"])
            return JsonResponse({"theme": new_theme})

        # ถ้าไม่มี profile ใช้ session
        current = request.session.get("theme", "dark")
        new_theme = "light" if current == "dark" else "dark"
        request.session["theme"] = new_theme
        return JsonResponse({"theme": new_theme})
