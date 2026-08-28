"""
URL configuration สำหรับ Freebuff Desktop
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("dashboard.urls")),
    path("accounts/", include("accounts.urls")),
    path("tasks/", include("tasks.urls")),
    path("schedule/", include("scheduling.urls")),
    path("reports/", include("reports.urls")),
    path("notifications/", include("notifications.urls")),
]

# Serve media files ใน development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
