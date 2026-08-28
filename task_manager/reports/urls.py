"""
URL configuration สำหรับ reports app
"""

from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("daily/", views.DailyReportView.as_view(), name="daily"),
    path("employee/", views.EmployeeReportView.as_view(), name="employee"),
    path("status/", views.StatusReportView.as_view(), name="status"),
    path("performance/", views.PerformanceMetricsView.as_view(), name="performance"),
    path("export/csv/", views.CSVExportView.as_view(), name="csv-export"),
]
