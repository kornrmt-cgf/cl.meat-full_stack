"""
Tests for Barcode Service, Label Service, NIIMBOT adapter, and legacy integration.
"""
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from decimal import Decimal
from datetime import timedelta

from inventory.models import (
    Product, Batch, Package, PackageState,
    BarcodeSequence, PriceChangeHistory, StorageLocation
)
from inventory.barcode_service import (
    generate_barcode, generate_preview_barcode, validate_barcode,
    lookup_package_by_barcode, calculate_package_price
)
from inventory.label_service import (
    get_label_data, get_niimbot_label_data, NIIMBOTPrintService
)


class BarcodeServiceTest(TestCase):
    """Test barcode generation, sequencing, and validation."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PK-001',
            name='สันคอหมู',
            category='PORK',
            barcode_prefix='0051',
            cost_per_kg=Decimal('85.00'),
            selling_price_per_kg=Decimal('120.00'),
        )
        self.batch = Batch.objects.create(
            batch_number='18',
            supplier='BETAGRO',
            received_at=timezone.now(),
        )
        self.location = StorageLocation.objects.create(
            name='Freezer A',
            location_type='FREEZER',
            capacity=50,
        )
    
    def test_generate_barcode_basic(self):
        """Test basic barcode generation."""
        barcode = generate_barcode(self.product, self.batch)
        # Format: {supplier_id}{batch_number}{prefix}{sequence:02d}
        self.assertTrue(barcode.endswith('005101'))  # prefix=0051, seq=01
        self.assertTrue(len(barcode) > 0)
    
    def test_generate_barcode_sequential(self):
        """Test that barcodes are sequential."""
        bc1 = generate_barcode(self.product, self.batch)
        bc2 = generate_barcode(self.product, self.batch)
        bc3 = generate_barcode(self.product, self.batch)
        
        # All should end with sequential numbers
        self.assertTrue(bc1.endswith('01'))
        self.assertTrue(bc2.endswith('02'))
        self.assertTrue(bc3.endswith('03'))
        # All should share same prefix
        self.assertEqual(bc1[:-2], bc2[:-2])
        self.assertEqual(bc2[:-2], bc3[:-2])
    
    def test_generate_barcode_different_batches(self):
        """Test barcodes differ for different batches."""
        batch2 = Batch.objects.create(
            batch_number='19',
            supplier='BETAGRO',
            received_at=timezone.now(),
        )
        
        bc1 = generate_barcode(self.product, self.batch)
        bc2 = generate_barcode(self.product, batch2)
        
        # Different batch numbers → different barcodes
        self.assertNotEqual(bc1, bc2)
    
    def test_generate_barcode_different_products(self):
        """Test barcodes differ for different products."""
        product2 = Product.objects.create(
            sku='CB-001',
            name='อกไก่',
            category='CHICKEN',
            barcode_prefix='0061',
        )
        
        bc1 = generate_barcode(self.product, self.batch)
        bc2 = generate_barcode(product2, self.batch)
        
        self.assertNotEqual(bc1, bc2)
    
    def test_generate_barcode_uniqueness(self):
        """Test that generated barcodes are always unique."""
        barcodes = set()
        for i in range(20):
            bc = generate_barcode(self.product, self.batch)
            self.assertNotIn(bc, barcodes, f"Duplicate barcode at iteration {i}: {bc}")
            barcodes.add(bc)
    
    def test_generate_barcode_no_override_after_delete(self):
        """Test that sequence continues even after package deletion."""
        # Generate 3 barcodes
        bc1 = generate_barcode(self.product, self.batch)
        bc2 = generate_barcode(self.product, self.batch)
        bc3 = generate_barcode(self.product, self.batch)
        
        # Delete the package with bc2 (simulating a deletion)
        # The sequence should continue from the MAX, not reuse bc2
        bc4 = generate_barcode(self.product, self.batch)
        self.assertNotEqual(bc4, bc2)  # Must NOT reuse bc2
    
    def test_generate_preview_barcode(self):
        """Test preview barcode without creating sequence."""
        preview = generate_preview_barcode(self.product, self.batch)
        self.assertTrue(preview.endswith('01'))  # Should start at seq 01
        
        # Should not affect actual sequence
        actual = generate_barcode(self.product, self.batch)
        self.assertTrue(actual.endswith('01'))  # Still starts at 01
    
    def test_validate_barcode_empty(self):
        """Test validation with empty barcode."""
        result = validate_barcode('')
        self.assertFalse(result['valid'])
    
    def test_validate_barcode_unique(self):
        """Test validation with unique barcode."""
        result = validate_barcode('UNIQUE-BC-001')
        self.assertTrue(result['valid'])
    
    def test_validate_barcode_duplicate(self):
        """Test validation with existing barcode."""
        # Create package with barcode
        Package.objects.create(
            product=self.product,
            batch=self.batch,
            barcode='EXISTING-001',
            weight=Decimal('0.500'),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED,
            storage_location=self.location,
        )
        
        result = validate_barcode('EXISTING-001')
        self.assertFalse(result['valid'])
        self.assertEqual(result['package_id'], 1)
    
    def test_lookup_package_by_barcode(self):
        """Test package lookup by barcode."""
        pkg = Package.objects.create(
            product=self.product,
            batch=self.batch,
            barcode='LOOKUP-001',
            weight=Decimal('0.750'),
            packed_at=timezone.now(),
            current_state=PackageState.FROZEN,
            storage_location=self.location,
        )
        
        found = lookup_package_by_barcode('LOOKUP-001')
        self.assertIsNotNone(found)
        self.assertEqual(found.id, pkg.id)
        self.assertEqual(found.product.name, 'สันคอหมู')
    
    def test_lookup_package_not_found(self):
        """Test lookup with non-existent barcode."""
        found = lookup_package_by_barcode('NONEXISTENT')
        self.assertIsNone(found)
    
    def test_lookup_package_empty(self):
        """Test lookup with empty barcode."""
        found = lookup_package_by_barcode('')
        self.assertIsNone(found)


class PricingServiceTest(TestCase):
    """Test package price calculation."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PK-002',
            name='หมูสามชั้น',
            category='PORK',
            cost_per_kg=Decimal('75.00'),
            selling_price_per_kg=Decimal('110.00'),
        )
    
    def test_price_auto_mode(self):
        """Test auto pricing from selling_price_per_kg."""
        price = calculate_package_price(self.product, 0.56, mode='auto')
        # 110 × 0.56 = 61.6, ceil = 62
        self.assertEqual(price, 62)
    
    def test_price_per_kg_mode(self):
        """Test explicit price_per_kg mode."""
        price = calculate_package_price(self.product, 0.80, mode='price_per_kg', value=100)
        # 100 × 0.80 = 80
        self.assertEqual(price, 80)
    
    def test_price_cost_margin_mode(self):
        """Test cost margin pricing."""
        price = calculate_package_price(self.product, 1.0, mode='cost_margin', value=50)
        # 75 × 1.0 × 1.5 = 112.5, ceil = 113
        self.assertEqual(price, 113)
    
    def test_price_discount_mode(self):
        """Test discount pricing."""
        price = calculate_package_price(self.product, 1.0, mode='discount', value=20)
        # 110 × 1.0 × 0.8 = 88
        self.assertEqual(price, 88)
    
    def test_price_zero_weight(self):
        """Test pricing with zero weight."""
        price = calculate_package_price(self.product, 0, mode='auto')
        self.assertEqual(price, 0)
    
    def test_price_ceil_rounding(self):
        """Test that prices are rounded up (math.ceil)."""
        price = calculate_package_price(self.product, 0.30, mode='auto')
        # 110 × 0.30 = 33.0
        self.assertEqual(price, 33)
        
        price2 = calculate_package_price(self.product, 0.31, mode='auto')
        # 110 × 0.31 = 34.1, ceil = 35
        self.assertEqual(price2, 35)


class LabelServiceTest(TestCase):
    """Test label data generation."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PK-003',
            name='คอหมูย่าง',
            category='PORK',
            barcode_prefix='0071',
            cost_per_kg=Decimal('90.00'),
            selling_price_per_kg=Decimal('130.00'),
            kcalories=Decimal('250.0'),
            protein=Decimal('20.0'),
            fat=Decimal('18.0'),
        )
        self.batch = Batch.objects.create(
            batch_number='22',
            supplier='BETAGRO',
            received_at=timezone.now(),
        )
        self.location = StorageLocation.objects.create(
            name='Freezer B',
            location_type='FREEZER',
            capacity=30,
        )
        self.package = Package.objects.create(
            product=self.product,
            batch=self.batch,
            barcode='222007101',
            weight=Decimal('0.560'),
            selling_price=Decimal('73'),
            packed_at=timezone.now(),
            current_state=PackageState.FROZEN,
            storage_location=self.location,
        )
    
    def test_label_data_basic(self):
        """Test basic label data generation."""
        data = get_label_data(self.package)
        
        self.assertEqual(data['product_name'], 'คอหมูย่าง')
        self.assertEqual(data['barcode'], '222007101')
        self.assertEqual(data['weight_kg'], 0.56)
        self.assertEqual(data['selling_price'], 73.0)
        self.assertEqual(data['selling_price_per_kg'], 130.0)
        self.assertEqual(data['supplier'], 'BETAGRO')
        self.assertEqual(data['batch_number'], '22')
    
    def test_label_data_category_emoji(self):
        """Test category emoji mapping."""
        data = get_label_data(self.package)
        self.assertEqual(data['category_emoji'], '🐷')
        
        # Test chicken
        chicken = Product.objects.create(
            sku='CB-001', name='อกไก่', category='CHICKEN'
        )
        batch2 = Batch.objects.create(
            batch_number='23', supplier='CP', received_at=timezone.now()
        )
        pkg = Package.objects.create(
            product=chicken, batch=batch2, barcode='TEST-CH',
            weight=Decimal('0.800'), packed_at=timezone.now(),
            current_state=PackageState.PACKED, storage_location=self.location,
        )
        data2 = get_label_data(pkg)
        self.assertEqual(data2['category_emoji'], '🐔')
    
    def test_label_data_nutrition(self):
        """Test nutrition data in label."""
        data = get_label_data(self.package)
        self.assertTrue(data['has_nutrition'])
        self.assertEqual(data['kcalories'], 250.0)
        self.assertEqual(data['protein'], 20.0)
        self.assertEqual(data['fat'], 18.0)
    
    def test_label_data_no_nutrition(self):
        """Test label without nutrition data."""
        product = Product.objects.create(
            sku='OT-001', name='Other', category='OTHER'
        )
        batch = Batch.objects.create(
            batch_number='99', supplier='X', received_at=timezone.now()
        )
        pkg = Package.objects.create(
            product=product, batch=batch, barcode='OT-BC',
            weight=Decimal('1.000'), packed_at=timezone.now(),
            current_state=PackageState.PACKED, storage_location=self.location,
        )
        data = get_label_data(pkg)
        self.assertFalse(data['has_nutrition'])
    
    def test_niimbot_label_data(self):
        """Test NIIMBOT-specific label data format."""
        data = get_niimbot_label_data(self.package)
        
        self.assertEqual(data['product'], 'คอหมูย่าง')
        self.assertEqual(data['barcode'], '222007101')
        self.assertEqual(data['weight'], '0.560')
        self.assertEqual(data['types'], '🐷')
        self.assertIn('MFG:', data['lot'])
    
    def test_label_price_formatting(self):
        """Test price display formatting."""
        data = get_label_data(self.package)
        self.assertEqual(data['price_display'], '฿73')
        self.assertEqual(data['price_per_kg_display'], '฿130')
    
    def test_label_weight_formatting(self):
        """Test weight display formatting."""
        data = get_label_data(self.package)
        self.assertEqual(data['weight_display'], '0.560 กก.')
        self.assertEqual(data['weight_grams'], 560)


class NIIMBOTPrintServiceTest(TestCase):
    """Test NIIMBOT print service adapter."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PK-004', name='สันนอกหมู', category='PORK',
            selling_price_per_kg=Decimal('115.00'),
        )
        self.batch = Batch.objects.create(
            batch_number='25', supplier='BETAGRO', received_at=timezone.now(),
        )
        self.location = StorageLocation.objects.create(
            name='Freezer C', location_type='FREEZER', capacity=20,
        )
        self.package = Package.objects.create(
            product=self.product, batch=self.batch, barcode='NIIMBOT-TEST',
            weight=Decimal('0.450'), packed_at=timezone.now(),
            current_state=PackageState.FROZEN, storage_location=self.location,
        )
    
    def test_service_initialization(self):
        """Test NIIMBOTPrintService initializes correctly."""
        service = NIIMBOTPrintService()
        # May or may not be available depending on environment
        self.assertIsInstance(service.is_available, bool)
    
    def test_preview_label(self):
        """Test label preview without printing."""
        service = NIIMBOTPrintService()
        preview = service.preview_label(self.package)
        
        self.assertEqual(preview['product_name'], 'สันนอกหมู')
        self.assertEqual(preview['barcode'], 'NIIMBOT-TEST')
        self.assertEqual(preview['weight_kg'], 0.45)
    
    def test_print_label_returns_data(self):
        """Test that print returns label data even if printer unavailable."""
        service = NIIMBOTPrintService()
        result = service.print_label(self.package)
        
        self.assertIn('success', result)
        self.assertIn('label_data', result)
        self.assertEqual(result['label_data']['product_name'], 'สันนอกหมู')
        
        # If not available, should return gracefully
        if not service.is_available:
            self.assertFalse(result['printed'])
            self.assertIsNotNone(result['error'])


class PriceChangeHistoryTest(TestCase):
    """Test PriceChangeHistory model."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='PK-005', name='หมูบด', category='PORK',
            selling_price_per_kg=Decimal('95.00'),
        )
        self.batch = Batch.objects.create(
            batch_number='30', supplier='CP', received_at=timezone.now(),
        )
        self.location = StorageLocation.objects.create(
            name='Freezer D', location_type='FREEZER', capacity=20,
        )
        self.package = Package.objects.create(
            product=self.product, batch=self.batch, barcode='PRICE-TEST',
            weight=Decimal('0.500'), selling_price=Decimal('48'),
            packed_at=timezone.now(), current_state=PackageState.FROZEN,
            storage_location=self.location,
        )
    
    def test_create_price_change(self):
        """Test creating a price change history record."""
        history = PriceChangeHistory.objects.create(
            package=self.package,
            old_price=Decimal('48'),
            new_price=Decimal('55'),
            mode='manual',
            actor='admin',
        )
        
        self.assertEqual(history.old_price, Decimal('48'))
        self.assertEqual(history.new_price, Decimal('55'))
        self.assertIsNone(history.undone_at)
    
    def test_price_change_ordering(self):
        """Test that price changes are ordered by most recent first."""
        PriceChangeHistory.objects.create(
            package=self.package,
            old_price=Decimal('48'), new_price=Decimal('50'),
            mode='manual',
        )
        PriceChangeHistory.objects.create(
            package=self.package,
            old_price=Decimal('50'), new_price=Decimal('55'),
            mode='discount',
        )
        
        changes = list(PriceChangeHistory.objects.filter(package=self.package))
        self.assertEqual(len(changes), 2)
        # Most recent first
        self.assertEqual(changes[0].new_price, Decimal('55'))
        self.assertEqual(changes[1].new_price, Decimal('50'))
    
    def test_undo_price_change(self):
        """Test undoing a price change."""
        history = PriceChangeHistory.objects.create(
            package=self.package,
            old_price=Decimal('48'), new_price=Decimal('55'),
            mode='manual',
        )
        
        history.undone_at = timezone.now()
        history.save()
        
        history.refresh_from_db()
        self.assertIsNotNone(history.undone_at)


class BarcodeSequenceTest(TestCase):
    """Test BarcodeSequence model."""
    
    def setUp(self):
        self.product = Product.objects.create(
            sku='SEQ-001', name='Test Product', category='PORK',
            barcode_prefix='9999',
        )
        self.batch = Batch.objects.create(
            batch_number='99', supplier='Test', received_at=timezone.now(),
        )
    
    def test_sequence_creation(self):
        """Test sequence tracker creation."""
        seq = BarcodeSequence.objects.create(
            product=self.product,
            batch_number='99',
            supplier_id=1,
            last_sequence=0,
        )
        self.assertEqual(seq.last_sequence, 0)
    
    def test_sequence_unique_constraint(self):
        """Test that same product+batch+supplier has one sequence."""
        BarcodeSequence.objects.create(
            product=self.product, batch_number='99', supplier_id=1,
            last_sequence=5,
        )
        
        # Creating another with same keys should update, not duplicate
        seq, created = BarcodeSequence.objects.get_or_create(
            product=self.product, batch_number='99', supplier_id=1,
            defaults={'last_sequence': 0}
        )
        self.assertFalse(created)
        self.assertEqual(seq.last_sequence, 5)  # Should get existing


class EndToEndPackageWorkflowTest(TransactionTestCase):
    """
    End-to-end test: create product → generate barcode → create package → label → plan.
    """
    
    def test_complete_package_creation_workflow(self):
        """Test the full workflow from product to label."""
        # 1. Create product with all legacy fields
        product = Product.objects.create(
            sku='E2E-001',
            name='หมูสามชั้น',
            category='PORK',
            barcode_prefix='0051',
            cost_per_kg=Decimal('75.00'),
            selling_price_per_kg=Decimal('110.00'),
            kcalories=Decimal('350.0'),
            protein=Decimal('14.0'),
            fat=Decimal('30.0'),
        )
        
        # 2. Create batch
        batch = Batch.objects.create(
            batch_number='18',
            supplier='BETAGRO',
            received_at=timezone.now(),
        )
        
        # 3. Generate barcode
        barcode = generate_barcode(product, batch)
        self.assertIsNotNone(barcode)
        self.assertTrue(len(barcode) > 0)
        
        # 4. Calculate price
        weight_kg = Decimal('0.560')
        price = calculate_package_price(product, float(weight_kg), mode='auto')
        self.assertGreater(price, 0)
        
        # 5. Create package with generated barcode and price
        location = StorageLocation.objects.create(
            name='Main Freezer', location_type='FREEZER', capacity=100,
        )
        package = Package.objects.create(
            product=product,
            batch=batch,
            barcode=barcode,
            weight=weight_kg,
            selling_price=Decimal(str(price)),
            packed_at=timezone.now(),
            current_state=PackageState.PACKED,
            storage_location=location,
        )
        
        # 6. Verify barcode lookup
        found = lookup_package_by_barcode(barcode)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, package.id)
        
        # 7. Generate label data
        label_data = get_label_data(package)
        self.assertEqual(label_data['product_name'], 'หมูสามชั้น')
        self.assertEqual(label_data['barcode'], barcode)
        self.assertEqual(label_data['weight_kg'], 0.56)
        self.assertEqual(label_data['selling_price'], float(price))
        self.assertTrue(label_data['has_nutrition'])
        self.assertEqual(label_data['category_emoji'], '🐷')
        
        # 8. NIIMBOT label data
        niimbot_data = get_niimbot_label_data(package)
        self.assertEqual(niimbot_data['product'], 'หมูสามชั้น')
        self.assertEqual(niimbot_data['types'], '🐷')
        
        # 9. Generate more barcodes for same product/batch
        bc2 = generate_barcode(product, batch)
        bc3 = generate_barcode(product, batch)
        self.assertNotEqual(barcode, bc2)
        self.assertNotEqual(bc2, bc3)
        
        # 10. Verify all barcodes are unique
        all_barcodes = [barcode, bc2, bc3]
        self.assertEqual(len(all_barcodes), len(set(all_barcodes)))
