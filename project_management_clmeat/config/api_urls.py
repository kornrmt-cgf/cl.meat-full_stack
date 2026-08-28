from django.urls import path, include

urlpatterns = [
    path('packages/', include('inventory.api_urls')),
    path('plans/', include('planning.api_urls')),
    path('tasks/', include('operations.api_urls')),
    path('worker/', include('worker.api_urls')),
]
