from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='auth/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('users/', include('users.urls')),
    path('', include('dashboard.urls')),
    path('inventory/', include('inventory.urls')),
    path('planning/', include('planning.urls')),
    path('operations/', include('operations.urls')),
    path('worker/', include('worker.urls')),
    path('api/', include('config.api_urls')),
]
