"""
Management command to set up groups and permissions.

Usage:
  python manage.py setup_groups
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType


class Command(BaseCommand):
    help = 'Create groups (ADMIN, MANAGER, WORKER, VIEWER) and assign permissions'

    def handle(self, *args, **options):
        self.stdout.write('Setting up groups and permissions...\n')

        # Create groups
        admin_group, _ = Group.objects.get_or_create(name='ADMIN')
        manager_group, _ = Group.objects.get_or_create(name='MANAGER')
        worker_group, _ = Group.objects.get_or_create(name='WORKER')
        viewer_group, _ = Group.objects.get_or_create(name='VIEWER')

        # Get all content types
        from inventory.models import Product, Batch, Package, StorageLocation, TemperatureLog
        from planning.models import FreezeProfile, ThawProfile, RotationPlan, ThawQueueEntry
        from operations.models import WorkerTask, TaskEvent, RotationEvent

        models_to_permissions = [
            (Product, 'inventory'),
            (Batch, 'inventory'),
            (Package, 'inventory'),
            (StorageLocation, 'inventory'),
            (TemperatureLog, 'inventory'),
            (FreezeProfile, 'planning'),
            (ThawProfile, 'planning'),
            (RotationPlan, 'planning'),
            (ThawQueueEntry, 'planning'),
            (WorkerTask, 'operations'),
            (TaskEvent, 'operations'),
            (RotationEvent, 'operations'),
        ]

        for model, app_label in models_to_permissions:
            ct, _ = ContentType.objects.get_or_create(
                app_label=app_label,
                model=model._meta.model_name
            )
            # Ensure permissions exist
            for action in ['add', 'change', 'delete', 'view']:
                codename = f'{action}_{model._meta.model_name}'
                Permission.objects.get_or_create(
                    codename=codename,
                    content_type=ct,
                    defaults={'name': f'Can {action} {model._meta.verbose_name}'}
                )

        # ADMIN — full access
        all_perms = Permission.objects.all()
        admin_group.permissions.set(all_perms)
        self.stdout.write(f'  ADMIN: {all_perms.count()} permissions')

        # MANAGER — add/change/view on inventory + planning, view on operations
        manager_perms = Permission.objects.filter(
            content_type__app_label__in=['inventory', 'planning', 'operations']
        ).exclude(
            codename__startswith='delete_'
        )
        manager_group.permissions.set(manager_perms)
        self.stdout.write(f'  MANAGER: {manager_perms.count()} permissions')

        # WORKER — view on inventory + operations, change on worker task + temperature
        worker_perms = Permission.objects.filter(
            content_type__app_label='inventory',
            codename__startswith='view_'
        ) | Permission.objects.filter(
            content_type__app_label='operations',
            codename__in=['view_workertask', 'change_workertask', 'view_rotationevent',
                          'view_taskevent', 'add_taskevent']
        ) | Permission.objects.filter(
            content_type__app_label='inventory',
            codename__in=['add_temperaturelog', 'view_temperaturelog']
        )
        worker_group.permissions.set(worker_perms)
        self.stdout.write(f'  WORKER: {worker_perms.count()} permissions')

        # VIEWER — view only on everything
        viewer_perms = Permission.objects.filter(codename__startswith='view_')
        viewer_group.permissions.set(viewer_perms)
        self.stdout.write(f'  VIEWER: {viewer_perms.count()} permissions')

        self.stdout.write(self.style.SUCCESS('\n✅ Groups and permissions configured.\n'))
