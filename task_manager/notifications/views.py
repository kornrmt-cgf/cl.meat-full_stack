"""
Views สำหรับ notifications app

จัดการ:
- Notification Center (list)
- Unread count (HTMX)
- Mark as read
- Mark all as read
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from .services import NotificationService


class NotificationListView(LoginRequiredMixin, TemplateView):
    """Notification Center - ดูแจ้งเตือนทั้งหมด"""

    template_name = "notifications/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["notifications"] = NotificationService.get_user_notifications(
            self.request.user
        )[:50]
        context["unread_count"] = NotificationService.get_unread_count(
            self.request.user
        )
        return context


class UnreadCountHTMXView(LoginRequiredMixin, View):
    """HTMX endpoint สำหรับ unread count badge"""

    def get(self, request):
        count = NotificationService.get_unread_count(request.user)
        from django.shortcuts import render

        return render(
            request,
            "notifications/partials/badge.html",
            {"unread_count": count},
        )


class MarkAsReadView(LoginRequiredMixin, View):
    """ทำเครื่องหมายว่าอ่านแล้ว (HTMX endpoint)"""

    def post(self, request, pk):
        success = NotificationService.mark_as_read(pk, request.user)
        if request.headers.get("HX-Request"):
            from django.shortcuts import render

            return render(
                request,
                "notifications/partials/notification_item_read.html",
                {"notification_id": pk},
            )
        return JsonResponse({"success": success})


class MarkAllAsReadView(LoginRequiredMixin, View):
    """ทำเครื่องหมายว่าอ่านทั้งหมด (HTMX endpoint)"""

    def post(self, request):
        count = NotificationService.mark_all_as_read(request.user)
        if request.headers.get("HX-Request"):
            from django.shortcuts import render

            return render(
                request,
                "notifications/partials/badge.html",
                {"unread_count": 0},
            )
        return JsonResponse({"marked": count})


class NotificationPopupHTMXView(LoginRequiredMixin, View):
    """HTMX endpoint สำหรับ notification popup"""

    def get(self, request):
        from django.shortcuts import render

        notifications = NotificationService.get_user_notifications(request.user)[:10]
        unread_count = NotificationService.get_unread_count(request.user)

        return render(
            request,
            "notifications/partials/popup.html",
            {
                "notifications": notifications,
                "unread_count": unread_count,
            },
        )
