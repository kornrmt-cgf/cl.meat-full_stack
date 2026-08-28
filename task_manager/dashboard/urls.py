"""
URL configuration สำหรับ dashboard app
"""

from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="home"),
    path("manager/", views.ManagerDashboardView.as_view(), name="manager"),
    path("manager/employee-workload/", views.EmployeeWorkloadView.as_view(), name="employee-workload"),
    path("manager/employee/<int:user_id>/", views.EmployeeDetailView.as_view(), name="employee-detail"),
    path("manager/team-overview/", views.TeamOverviewView.as_view(), name="team-overview"),
]
