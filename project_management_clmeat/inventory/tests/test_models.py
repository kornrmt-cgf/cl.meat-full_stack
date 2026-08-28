"""
Tests for Inventory Models.
"""
from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from datetime import timedelta
from inventory.models import Product, Batch, Package, StorageLocation, PackageState


class ProductModelTest(TestCase):
    """Test Product model."""
    
    def test_create_product(self):
        """Test creating a product."""
        product = Product.objects.create(
            sku='PKC001',
            name='Pork Collar',
            category='PORK',
            unit='KG'
        )
        self.assertEqual(product.sku, 'PKC001')
        self.assertEqual(product.name, 'Pork Collar')
        self.assertEqual(product.category, 'PORK')
        self.assertTrue(product.active)
    
    def test_product_str(self):
        """Test product string representation."""
        product = Product.objects.create(
            sku='PKC001',
            name='Pork Collar',
            category='PORK'
        )
        self.assertEqual(str(product), 'Pork Collar (PKC001)')
    
    def test_product_unique_sku(self):
        """Test that SKU must be unique."""
        Product.objects.create(sku='PKC001', name='Pork Collar', category='PORK')
        with self.assertRaises(Exception):
            Product.objects.create(sku='PKC001', name='Another Pork', category='PORK')


class BatchModelTest(TestCase):
    """Test Batch model."""
    
    def test_create_batch(self):
        """Test creating a batch."""
        batch = Batch.objects.create(
            batch_number='BATCH-2026-001',
            supplier='Thai Fresh Meats',
            received_at=timezone.now()
        )
        self.assertEqual(batch.batch_number, 'BATCH-2026-001')
        self.assertEqual(batch.supplier, 'Thai Fresh Meats')
        self.assertTrue(batch.active)
    
    def test_batch_str(self):
        """Test batch string representation."""
        batch = Batch.objects.create(
            batch_number='BATCH-2026-001',
            supplier='Thai Fresh Meats',
            received_at=timezone.now()
        )
        self.assertEqual(str(batch), 'Batch BATCH-2026-001 from Thai Fresh Meats')


class StorageLocationModelTest(TestCase):
    """Test StorageLocation model."""
    
    def test_create_location(self):
        """Test creating a storage location."""
        location = StorageLocation.objects.create(
            name='Freezer A',
            location_type='FREEZER',
            capacity=50
        )
        self.assertEqual(location.name, 'Freezer A')
        self.assertEqual(location.location_type, 'FREEZER')
        self.assertEqual(location.capacity, 50)
    
    def test_available_capacity(self):
        """Test available capacity calculation."""
        location = StorageLocation.objects.create(
            name='Freezer A',
            location_type='FREEZER',
            capacity=2
        )
        
        # No packages yet
        self.assertEqual(location.available_capacity, 2)
        
        # Create product and batch for packages
        product = Product.objects.create(sku='TEST', name='Test', category='PORK')
        batch = Batch.objects.create(
            batch_number='B001',
            supplier='Test',
            received_at=timezone.now()
        )
        
        # Add packages
        Package.objects.create(
            product=product,
            batch=batch,
            weight=Decimal('0.500'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED,
            storage_location=location
        )
        
        self.assertEqual(location.available_capacity, 1)
        
        Package.objects.create(
            product=product,
            batch=batch,
            weight=Decimal('0.600'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED,
            storage_location=location
        )
        
        self.assertEqual(location.available_capacity, 0)


class PackageModelTest(TestCase):
    """Test Package model."""
    
    def setUp(self):
        """Set up test data."""
        self.product = Product.objects.create(
            sku='PKC001',
            name='Pork Collar',
            category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-2026-001',
            supplier='Thai Fresh Meats',
            received_at=timezone.now()
        )
    
    def test_create_package(self):
        """Test creating a package."""
        package = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        
        self.assertEqual(package.product, self.product)
        self.assertEqual(package.batch, self.batch)
        self.assertEqual(package.weight, Decimal('0.560'))
        self.assertEqual(package.current_state, PackageState.PACKED)
    
    def test_package_str(self):
        """Test package string representation."""
        package = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        self.assertIn('Pork Collar', str(package))
        self.assertIn('0.560', str(package))
    
    def test_package_display_name(self):
        """Test package display name."""
        package = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        self.assertEqual(package.display_name, 'Pork Collar 0.560kg')
    
    def test_package_is_frozen(self):
        """Test is_frozen property."""
        package = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        self.assertFalse(package.is_frozen)
        
        package.current_state = PackageState.FROZEN
        package.save()
        self.assertTrue(package.is_frozen)
    
    def test_package_can_transition_to(self):
        """Test can_transition_to method."""
        package = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        
        self.assertTrue(package.can_transition_to('FREEZING'))
        self.assertFalse(package.can_transition_to('FROZEN'))
        self.assertFalse(package.can_transition_to('THAWING'))
    
    def test_package_weight_unique_per_package(self):
        """Test that different packages can have different weights."""
        pkg1 = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.500'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        
        pkg2 = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        
        pkg3 = Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.800'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        
        self.assertNotEqual(pkg1.weight, pkg2.weight)
        self.assertNotEqual(pkg2.weight, pkg3.weight)
