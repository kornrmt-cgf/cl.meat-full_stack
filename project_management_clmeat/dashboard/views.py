"""
Dashboard Views.
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import timedelta
from inventory.selectors import get_package_stats
from planning.selectors import get_plan_stats
from operations.services import get_todays_tasks, get_overdue_tasks


@login_required
def index(request):
    """Main dashboard view."""
    package_stats = get_package_stats()
    plan_stats = get_plan_stats()
    todays_tasks = get_todays_tasks()
    overdue_tasks = get_overdue_tasks()
    
    context = {
        'package_stats': package_stats,
        'plan_stats': plan_stats,
        'todays_tasks': todays_tasks[:10],  # Show first 10
        'overdue_tasks': overdue_tasks[:5],  # Show first 5 overdue
        'now': timezone.now(),
    }
    return render(request, 'dashboard/index.html', context)
