"""
URL configuration สำหรับ notifications app
"""

from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="list"),
    path("unread-count/", views.UnreadCountHTMXView.as_view(), name="unread-count"),
    path("popup/", views.NotificationPopupHTMXView.as_view(), name="popup"),
    path("<int:pk>/read/", views.MarkAsReadView.as_view(), name="mark-read"),
    path("read-all/", views.MarkAllAsReadView.as_view(), name="mark-all-read"),
]
