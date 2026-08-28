"""
Tests for Inventory API endpoints.
"""
import json
from django.test import TestCase, Client
from django.utils import timezone
from decimal import Decimal
from inventory.models import Product, Batch, Package, PackageState


class PackageListApiTest(TestCase):
    """Test package list API."""
    
    def setUp(self):
        self.client = Client()
        self.product = Product.objects.create(
            sku='PKC001',
            name='Pork Collar',
            category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001',
            supplier='Thai Fresh',
            received_at=timezone.now()
        )
    
    def test_get_packages_empty(self):
        """Test getting packages when none exist."""
        response = self.client.get('/api/packages/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['packages'], [])
    
    def test_get_packages_with_data(self):
        """Test getting packages with data."""
        Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        
        response = self.client.get('/api/packages/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['packages']), 1)
        self.assertEqual(data['packages'][0]['product_name'], 'Pork Collar')
    
    def test_filter_by_state(self):
        """Test filtering packages by state."""
        Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.560'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED
        )
        Package.objects.create(
            product=self.product,
            batch=self.batch,
            weight=Decimal('0.600'),
            packed_at=timezone.now(),
            current_state=PackageState.FROZEN
        )
        
        response = self.client.get('/api/packages/?state=PACKED')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['packages']), 1)
        self.assertEqual(data['packages'][0]['current_state'], 'PACKED')


class PackageCreateApiTest(TestCase):
    """Test package create API."""
    
    def setUp(self):
        self.client = Client()
        self.product = Product.objects.create(
            sku='PKC001',
            name='Pork Collar',
            category='PORK'
        )
        self.batch = Batch.objects.create(
            batch_number='BATCH-001',
            supplier='Thai Fresh',
            received_at=timezone.now()
        )
    
    def test_create_package_success(self):
        """Test successful package creation via API."""
        response = self.client.post(
            '/api/packages/create/',
            data=json.dumps({
                'product_id': self.product.pk,
                'batch_id': self.batch.pk,
                'weight': 0.560,
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertIn('id', data)
    
    def test_create_package_invalid_product(self):
        """Test creating package with invalid product."""
        response = self.client.post(
            '/api/packages/create/',
            data=json.dumps({
                'product_id': 9999,
                'batch_id': self.batch.pk,
                'weight': 0.560,
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 404)
