"""
Tests for Inventory Services.
"""
from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from inventory.models import Product, Batch, Package, StorageLocation, PackageState
from inventory.services import (
    create_product, create_batch, create_package, 
    create_storage_location, move_package_to_location
)


class CreateProductTest(TestCase):
    """Test create_product service."""
    
    def test_create_product_success(self):
        """Test successful product creation."""
        product = create_product(
            sku='PKC001',
            name='Pork Collar',
            category='PORK'
        )
        self.assertEqual(product.sku, 'PKC001')
        self.assertEqual(product.name, 'Pork Collar')
    
    def test_create_product_duplicate_sku(self):
        """Test that duplicate SKU raises error."""
        create_product(sku='PKC001', name='Pork Collar', category='PORK')
        with self.assertRaises(ValueError) as context:
            create_product(sku='PKC001', name='Another Pork', category='PORK')
        self.assertIn('already exists', str(context.exception))


class CreateBatchTest(TestCase):
    """Test create_batch service."""
    
    def test_create_batch_success(self):
        """Test successful batch creation."""
        batch = create_batch(
            batch_number='BATCH-001',
            supplier='Thai Fresh'
        )
        self.assertEqual(batch.batch_number, 'BATCH-001')
    
    def test_create_batch_duplicate(self):
        """Test that duplicate batch number raises error."""
        create_batch(batch_number='BATCH-001', supplier='Thai Fresh')
        with self.assertRaises(ValueError):
            create_batch(batch_number='BATCH-001', supplier='Another')


class CreatePackageTest(TestCase):
    """Test create_package service."""
    
    def setUp(self):
        self.product = create_product(
            sku='PKC001',
            name='Pork Collar',
            category='PORK'
        )
        self.batch = create_batch(
            batch_number='BATCH-001',
            supplier='Thai Fresh'
        )
    
    def test_create_package_success(self):
        """Test successful package creation."""
        package = create_package(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560')
        )
        self.assertEqual(package.product, self.product)
        self.assertEqual(package.weight, Decimal('0.560'))
        self.assertEqual(package.current_state, PackageState.PACKED)
    
    def test_create_package_with_barcode(self):
        """Test package creation with barcode."""
        package = create_package(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            barcode='123456789'
        )
        self.assertEqual(package.barcode, '123456789')


class MovePackageToLocationTest(TestCase):
    """Test move_package_to_location service."""
    
    def setUp(self):
        self.product = create_product(
            sku='PKC001',
            name='Pork Collar',
            category='PORK'
        )
        self.batch = create_batch(
            batch_number='BATCH-001',
            supplier='Thai Fresh'
        )
        self.location = create_storage_location(
            name='Freezer A',
            location_type='FREEZER',
            capacity=2
        )
    
    def test_move_package_success(self):
        """Test successful package move."""
        package = create_package(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560')
        )
        
        updated_package = move_package_to_location(package, self.location)
        self.assertEqual(updated_package.storage_location, self.location)
    
    def test_move_package_to_full_location(self):
        """Test moving package to full location raises error."""
        # Fill location
        pkg1 = create_package(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.500')
        )
        pkg2 = create_package(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.600')
        )
        
        move_package_to_location(pkg1, self.location)
        move_package_to_location(pkg2, self.location)
        
        # Third package should fail
        pkg3 = create_package(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.700')
        )
        
        with self.assertRaises(ValueError) as context:
            move_package_to_location(pkg3, self.location)
        self.assertIn('full capacity', str(context.exception))
    
    def test_move_package_to_inactive_location(self):
        """Test moving package to inactive location raises error."""
        inactive_location = create_storage_location(
            name='Old Freezer',
            location_type='FREEZER',
            capacity=10
        )
        inactive_location.active = False
        inactive_location.save()
        
        package = create_package(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560')
        )
        
        with self.assertRaises(ValueError) as context:
            move_package_to_location(package, inactive_location)
        self.assertIn('not active', str(context.exception))
