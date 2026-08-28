"""
Management command to clear demo data.
"""
from django.core.management.base import BaseCommand
from inventory.models import Product, Batch, Package, StorageLocation
from planning.models import FreezeProfile, ThawProfile, RotationPlan, ThawQueueEntry
from operations.models import WorkerTask, TaskEvent, RotationEvent


class Command(BaseCommand):
    help = 'Clear all demo data from Fresh Meat Rotation Planner'
    
    def handle(self, *args, **options):
        self.stdout.write('Clearing demo data...')
        
        # Delete in correct order to avoid foreign key issues
        TaskEvent.objects.all().delete()
        self.stdout.write('  Cleared TaskEvents')
        
        RotationEvent.objects.all().delete()
        self.stdout.write('  Cleared RotationEvents')
        
        WorkerTask.objects.all().delete()
        self.stdout.write('  Cleared WorkerTasks')
        
        ThawQueueEntry.objects.all().delete()
        self.stdout.write('  Cleared ThawQueueEntries')
        
        RotationPlan.objects.all().delete()
        self.stdout.write('  Cleared RotationPlans')
        
        Package.objects.all().delete()
        self.stdout.write('  Cleared Packages')
        
        Batch.objects.all().delete()
        self.stdout.write('  Cleared Batches')
        
        Product.objects.all().delete()
        self.stdout.write('  Cleared Products')
        
        StorageLocation.objects.all().delete()
        self.stdout.write('  Cleared StorageLocations')
        
        FreezeProfile.objects.all().delete()
        self.stdout.write('  Cleared FreezeProfiles')
        
        ThawProfile.objects.all().delete()
        self.stdout.write('  Cleared ThawProfiles')
        
        self.stdout.write(self.style.SUCCESS('All demo data cleared successfully!'))
