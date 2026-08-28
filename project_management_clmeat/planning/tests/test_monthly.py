"""
Tests for Monthly Planning.
"""
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta, datetime, timezone as dt_timezone
from decimal import Decimal
from planning.models import FreezeProfile, ThawProfile, RotationPlan, PlanStatus
from planning.services import create_rotation_plan
from planning.selectors import get_calendar_data, get_plans_for_date_range
from inventory.models import Product, Batch, Package, PackageState


class MonthlyPlanningTest(TestCase):
    """Test monthly planning functionality."""
    
    def setUp(self):
        self.freeze_profile = FreezeProfile.objects.create(
            name='Standard',
            target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24)
        )
        self.thaw_profile = ThawProfile.objects.create(
            name='Standard',
            default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12)
        )
        
        self.product = Product.objects.create(
            sku='PKC001', name='Pork Collar', category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001', supplier='Thai Fresh', received_at=timezone.now()
        )
    
    def create_package(self, weight):
        """Helper to create package."""
        return Package.objects.create(
            product=self.product, batch=self.batch,
            weight=Decimal(str(weight)), packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
    
    def test_calendar_data_structure(self):
        """Test calendar data has correct structure."""
        calendar = get_calendar_data(2026, 9)
        
        self.assertEqual(len(calendar), 30)  # September has 30 days
        
        for day_data in calendar:
            self.assertIn('date', day_data)
            self.assertIn('required', day_data)
            self.assertIn('planned', day_data)
            self.assertIn('status', day_data)
    
    def test_calendar_with_plans(self):
        """Test calendar shows plans correctly."""
        # Create packages and plans
        for i in range(3):
            package = self.create_package(0.560)
            target_date = timezone.make_aware(datetime(2026, 9, 10 + i, 10, 0))
            create_rotation_plan(
                package, target_date,
                self.freeze_profile, self.thaw_profile
            )
        
        # Get calendar for September
        calendar = get_calendar_data(2026, 9)
        
        # Find Sept 10-12
        sept_10 = next(d for d in calendar if d['date'] == datetime(2026, 9, 10).date())
        sept_11 = next(d for d in calendar if d['date'] == datetime(2026, 9, 11).date())
        sept_12 = next(d for d in calendar if d['date'] == datetime(2026, 9, 12).date())
        
        self.assertEqual(sept_10['required'], 1)
        self.assertEqual(sept_11['required'], 1)
        self.assertEqual(sept_12['required'], 1)
    
    def test_date_range_query(self):
        """Test querying plans for date range."""
        # Create plans for multiple dates
        dates = [
            datetime(2026, 9, 1, 10, 0),
            datetime(2026, 9, 5, 10, 0),
            datetime(2026, 9, 10, 10, 0),
            datetime(2026, 9, 15, 10, 0),
        ]
        
        for date in dates:
            package = self.create_package(0.560)
            target = timezone.make_aware(date)
            create_rotation_plan(
                package, target,
                self.freeze_profile, self.thaw_profile
            )
        
        # Query range
        plans = get_plans_for_date_range(
            datetime(2026, 9, 1).date(),
            datetime(2026, 9, 10).date()
        )
        
        self.assertEqual(plans.count(), 3)  # Sept 1, 5, 10
