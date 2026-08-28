from django.urls import path
from . import api

app_name = 'operations_api'

urlpatterns = [
    path('today/', api.tasks_today_api, name='tasks_today'),
    path('all/', api.all_tasks_api, name='all_tasks'),
    path('<int:pk>/', api.task_detail_api, name='task_detail'),
    path('<int:pk>/complete/', api.task_complete_api, name='task_complete'),
    path('freeze/start/', api.freeze_start_api, name='freeze_start'),
    path('freeze/complete/', api.freeze_complete_api, name='freeze_complete'),
    path('thaw/start/', api.thaw_start_api, name='thaw_start'),
    path('thaw/complete/', api.thaw_complete_api, name='thaw_complete'),
    path('display/start/', api.display_start_api, name='display_start'),
    path('display/refreeze/', api.display_refreeze_api, name='display_refreeze'),
]
