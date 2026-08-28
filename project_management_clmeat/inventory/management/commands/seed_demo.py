"""
Management command to seed realistic Thai demo data.
Creates 40 packages of pork and chicken with full operations workflow.
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from django.utils.timezone import make_aware
import datetime as dt

from inventory.models import Product, Batch, Package, StorageLocation, PackageState
from planning.models import FreezeProfile, ThawProfile, RotationPlan, PlanStatus, ThawQueueEntry, QueueStatus
from operations.models import WorkerTask, TaskType, TaskStatus, RotationEvent
from common.state_machine import transition_package


class Command(BaseCommand):
    help = 'สร้างข้อมูลทดลอง 40 รายการเนื้อหมูและไก่พร้อมแผนงาน'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('กำลังลบข้อมูลเดิมทั้งหมด...'))

        # Clear existing data
        RotationEvent.objects.all().delete()
        WorkerTask.objects.all().delete()
        ThawQueueEntry.objects.all().delete()
        RotationPlan.objects.all().delete()
        Package.objects.all().delete()
        Batch.objects.all().delete()
        Product.objects.all().delete()
        StorageLocation.objects.all().delete()
        FreezeProfile.objects.all().delete()
        ThawProfile.objects.all().delete()

        self.stdout.write('สร้างข้อมูลใหม่...')
        now = timezone.now()

        # ============================================================
        # FREEZE PROFILES
        # ============================================================
        freeze_standard = FreezeProfile.objects.create(
            name='แช่แข็งมาตรฐาน',
            target_temperature=Decimal('-18.00'),
            minimum_duration=timedelta(hours=12),
            default_duration=timedelta(hours=24),
            buffer_duration=timedelta(hours=2),
        )
        freeze_quick = FreezeProfile.objects.create(
            name='แช่แข็งเร็ว',
            target_temperature=Decimal('-25.00'),
            minimum_duration=timedelta(hours=8),
            default_duration=timedelta(hours=16),
            buffer_duration=timedelta(hours=1),
        )
        self.stdout.write(f'  ✓ โปรไฟล์แช่แข็ง: 2 โปรไฟล์')

        # ============================================================
        # THAW PROFILES
        # ============================================================
        thaw_standard = ThawProfile.objects.create(
            name='ละลายน้ำแข็งมาตรฐาน',
            default_duration=timedelta(hours=24),
            minimum_duration=timedelta(hours=12),
            buffer_duration=timedelta(hours=2),
            weight_threshold_kg=Decimal('0.500'),
            weight_scale_factor=Decimal('1.20'),
            target_temperature=Decimal('3.00'),
            min_temperature=Decimal('1.00'),
            max_temperature=Decimal('5.00'),
            thaw_capacity=20,
            category='',
            notes='ค่าเริ่มต้นสำหรับการละลายน้ำแข็งในตู้เย็น 1-5°C',
        )
        thaw_slow = ThawProfile.objects.create(
            name='ละลายน้ำแข็งช้า',
            default_duration=timedelta(hours=36),
            minimum_duration=timedelta(hours=24),
            buffer_duration=timedelta(hours=4),
            weight_threshold_kg=Decimal('0.800'),
            weight_scale_factor=Decimal('1.15'),
            target_temperature=Decimal('2.00'),
            min_temperature=Decimal('0.50'),
            max_temperature=Decimal('4.00'),
            thaw_capacity=15,
            category='',
            notes='สำหรับสินค้าที่ต้องการละลายช้ากว่าปกติ',
        )
        self.stdout.write(f'  ✓ โปรไฟล์ละลายน้ำแข็ง: 2 โปรไฟล์')

        # ============================================================
        # PRODUCTS (Thai names)
        # ============================================================
        products_data = [
            # Pork
            ('สันคอหมู', 'PKC001', 'PORK', 'หมู'),
            ('สันนอกหมู', 'PKL001', 'PORK', 'หมู'),
            ('หมูสามชั้น', 'PKB001', 'PORK', 'หมู'),
            ('สันในหมู', 'PKT001', 'PORK', 'หมู'),
            ('เนื้อสันไหล่หมู', 'PKS001', 'PORK', 'หมู'),
            ('หมูบด', 'MPK001', 'PORK', 'หมู'),
            ('ซี่โครงหมู', 'PKR001', 'PORK', 'หมู'),
            ('คอหมูย่าง', 'PKY001', 'PORK', 'หมู'),
            # Chicken
            ('อกไก่', 'CHB001', 'CHICKEN', 'ไก่'),
            ('สะโพกไก่', 'CTL001', 'CHICKEN', 'ไก่'),
            ('น่องไก่', 'CRL001', 'CHICKEN', 'ไก่'),
            ('ปีกไก่', 'CWG001', 'CHICKEN', 'ไก่'),
            ('ไก่บด', 'MCN001', 'CHICKEN', 'ไก่'),
        ]

        products = {}
        for name, sku, cat, section in products_data:
            p = Product.objects.create(sku=sku, name=name, category=cat, unit='KG')
            products[sku] = p
        self.stdout.write(f'  ✓ สินค้า: {len(products)} รายการ')

        # ============================================================
        # STORAGE LOCATIONS
        # ============================================================
        locations = [
            ('ตู้แช่ A', 'FREEZER', 50),
            ('ตู้แช่ B', 'FREEZER', 50),
            ('โซนละลายน้ำแข็ง', 'THAW_AREA', 20),
            ('ตู้展示 1', 'DISPLAY', 15),
            ('ตู้展示 2', 'DISPLAY', 15),
        ]
        loc_objs = {}
        for name, ltype, cap in locations:
            loc = StorageLocation.objects.create(name=name, location_type=ltype, capacity=cap)
            loc_objs[name] = loc
        self.stdout.write(f'  ✓ สถานที่เก็บ: {len(locations)} แห่ง')

        # ============================================================
        # BATCHES
        # ============================================================
        batch_main = Batch.objects.create(
            batch_number='BATCH-2026-0901',
            supplier='ไทยเฟรชมีท จำกัด',
            received_at=now - timedelta(days=3),
            notes='ล็อตหลักเดือนกันยายน',
        )
        batch_chicken = Batch.objects.create(
            batch_number='BATCH-2026-0902',
            supplier='โรงเชือดไก่สด จำกัด',
            received_at=now - timedelta(days=2),
            notes='ล็อตไก่สดเดือนกันยายน',
        )
        self.stdout.write(f'  ✓ ล็อตสินค้า: 2 ล็อต')

        # ============================================================
        # PACKAGES — 40 รายการ
        # ============================================================
        # Format: (sku, weight, state, days_ago, barcode, location_name)
        freezer_loc = loc_objs['ตู้แช่ A']

        package_configs = [
            # ===== สันคอหมู (Pork Collar) — 6 ชิ้น =====
            ('PKC001', Decimal('0.480'), PackageState.PACKED,     1, 'TH-PK-001', None),
            ('PKC001', Decimal('0.560'), PackageState.FROZEN,     2, 'TH-PK-002', 'ตู้แช่ A'),
            ('PKC001', Decimal('0.650'), PackageState.FROZEN,     2, 'TH-PK-003', 'ตู้แช่ A'),
            ('PKC001', Decimal('0.720'), PackageState.THAWING,    3, 'TH-PK-004', 'โซนละลายน้ำแข็ง'),
            ('PKC001', Decimal('0.550'), PackageState.READY_FOR_SALE, 4, 'TH-PK-005', 'ตู้展示 1'),
            ('PKC001', Decimal('0.820'), PackageState.ON_DISPLAY, 5, 'TH-PK-006', 'ตู้展示 1'),

            # ===== สันนอกหมู (Pork Loin) — 4 ชิ้น =====
            ('PKL001', Decimal('0.600'), PackageState.PACKED,     1, 'TH-PL-001', None),
            ('PKL001', Decimal('0.750'), PackageState.FROZEN,     3, 'TH-PL-002', 'ตู้แช่ A'),
            ('PKL001', Decimal('0.900'), PackageState.THAW_QUEUED, 2, 'TH-PL-003', None),
            ('PKL001', Decimal('0.540'), PackageState.FROZEN,     2, 'TH-PL-004', 'ตู้แช่ B'),

            # ===== หมูสามชั้น (Pork Belly) — 5 ชิ้น =====
            ('PKB001', Decimal('0.500'), PackageState.PACKED,     1, 'TH-PB-001', None),
            ('PKB001', Decimal('0.620'), PackageState.FROZEN,     2, 'TH-PB-002', 'ตู้แช่ A'),
            ('PKB001', Decimal('0.780'), PackageState.FROZEN,     3, 'TH-PB-003', 'ตู้แช่ B'),
            ('PKB001', Decimal('1.050'), PackageState.READY_FOR_SALE, 4, 'TH-PB-004', None),
            ('PKB001', Decimal('0.450'), PackageState.THAWING,    3, 'TH-PB-005', 'โซนละลายน้ำแข็ง'),

            # ===== สันในหมู (Pork Tenderloin) — 3 ชิ้น =====
            ('PKT001', Decimal('0.380'), PackageState.PACKED,     1, 'TH-PT-001', None),
            ('PKT001', Decimal('0.420'), PackageState.FROZEN,     2, 'TH-PT-002', 'ตู้แช่ A'),
            ('PKT001', Decimal('0.350'), PackageState.READY_FOR_SALE, 5, 'TH-PT-003', None),

            # ===== เนื้อสันไหล่หมู (Pork Shoulder) — 3 ชิ้น =====
            ('PKS001', Decimal('1.200'), PackageState.FROZEN,     3, 'TH-PS-001', 'ตู้แช่ A'),
            ('PKS001', Decimal('0.950'), PackageState.PACKED,     1, 'TH-PS-002', None),
            ('PKS001', Decimal('1.100'), PackageState.THAW_QUEUED, 2, 'TH-PS-003', None),

            # ===== หมูบด (Minced Pork) — 3 ชิ้น =====
            ('MPK001', Decimal('0.500'), PackageState.PACKED,     1, 'TH-MP-001', None),
            ('MPK001', Decimal('0.450'), PackageState.FROZEN,     2, 'TH-MP-002', 'ตู้แช่ A'),
            ('MPK001', Decimal('0.600'), PackageState.FROZEN,     3, 'TH-MP-003', 'ตู้แช่ B'),

            # ===== ซี่โครงหมู (Pork Ribs) — 3 ชิ้น =====
            ('PKR001', Decimal('0.850'), PackageState.PACKED,     1, 'TH-PR-001', None),
            ('PKR001', Decimal('1.300'), PackageState.FROZEN,     2, 'TH-PR-002', 'ตู้แช่ A'),
            ('PKR001', Decimal('0.920'), PackageState.ON_DISPLAY, 4, 'TH-PR-003', 'ตู้展示 1'),

            # ===== คอหมูย่าง (Grilled Pork Neck) — 2 ชิ้น =====
            ('PKY001', Decimal('0.700'), PackageState.PACKED,     1, 'TH-PY-001', None),
            ('PKY001', Decimal('0.680'), PackageState.READY_FOR_SALE, 3, 'TH-PY-002', None),

            # ===== อกไก่ (Chicken Breast) — 5 ชิ้น =====
            ('CHB001', Decimal('0.350'), PackageState.PACKED,     1, 'TH-CB-001', None),
            ('CHB001', Decimal('0.420'), PackageState.FROZEN,     2, 'TH-CB-002', 'ตู้แช่ A'),
            ('CHB001', Decimal('0.500'), PackageState.FROZEN,     3, 'TH-CB-003', 'ตู้แช่ B'),
            ('CHB001', Decimal('0.650'), PackageState.THAWING,    3, 'TH-CB-004', 'โซนละลายน้ำแข็ง'),
            ('CHB001', Decimal('0.550'), PackageState.ON_DISPLAY, 4, 'TH-CB-005', 'ตู้展示 2'),

            # ===== สะโพกไก่ (Chicken Thigh) — 4 ชิ้น =====
            ('CTL001', Decimal('0.450'), PackageState.PACKED,     1, 'TH-CT-001', None),
            ('CTL001', Decimal('0.520'), PackageState.FROZEN,     2, 'TH-CT-002', 'ตู้แช่ A'),
            ('CTL001', Decimal('0.600'), PackageState.THAW_QUEUED, 2, 'TH-CT-003', None),
            ('CTL001', Decimal('0.480'), PackageState.FROZEN,     3, 'TH-CT-004', 'ตู้แช่ B'),

            # ===== น่องไก่ (Chicken Drumstick) — 3 ชิ้น =====
            ('CRL001', Decimal('0.300'), PackageState.PACKED,     1, 'TH-CR-001', None),
            ('CRL001', Decimal('0.350'), PackageState.FROZEN,     2, 'TH-CR-002', 'ตู้แช่ A'),
            ('CRL001', Decimal('0.280'), PackageState.FROZEN,     3, 'TH-CR-003', 'ตู้แช่ B'),

            # ===== ปีกไก่ (Chicken Wing) — 3 ชิ้น =====
            ('CWG001', Decimal('0.250'), PackageState.PACKED,     1, 'TH-CW-001', None),
            ('CWG001', Decimal('0.300'), PackageState.FROZEN,     2, 'TH-CW-002', 'ตู้แช่ A'),
            ('CWG001', Decimal('0.220'), PackageState.READY_FOR_SALE, 5, 'TH-CW-003', None),

            # ===== ไก่บด (Minced Chicken) — 2 ชิ้น =====
            ('MCN001', Decimal('0.500'), PackageState.PACKED,     1, 'TH-MC-001', None),
            ('MCN001', Decimal('0.400'), PackageState.FROZEN,     3, 'TH-MC-002', 'ตู้แช่ A'),
        ]

        all_packages = []
        for i, (sku, weight, state, days_ago, barcode, loc_name) in enumerate(package_configs):
            loc = loc_objs.get(loc_name) if loc_name else None
            batch = batch_main if sku.startswith('PK') or sku.startswith('MP') or sku.startswith('CH') else batch_chicken
            if sku.startswith('CH') or sku.startswith('CT') or sku.startswith('CR') or sku.startswith('CW') or sku.startswith('MC'):
                batch = batch_chicken

            pkg = Package.objects.create(
                product=products[sku],
                batch=batch,
                barcode=barcode,
                weight=weight,
                packed_at=now - timedelta(days=days_ago),
                current_state=state,
                storage_location=loc,
            )
            all_packages.append(pkg)

        self.stdout.write(f'  ✓ แพ็กเกจ: {len(all_packages)} ชิ้น')

        # ============================================================
        # ROTATION PLANS for FROZEN packages (create plans + tasks)
        # ============================================================
        # Target ready dates spread across September 2026
        target_dates = [
            make_aware(dt.datetime(2026, 9, 5, 10, 0)),
            make_aware(dt.datetime(2026, 9, 6, 10, 0)),
            make_aware(dt.datetime(2026, 9, 7, 9, 30)),
            make_aware(dt.datetime(2026, 9, 8, 10, 0)),
            make_aware(dt.datetime(2026, 9, 9, 11, 0)),
            make_aware(dt.datetime(2026, 9, 10, 10, 0)),
            make_aware(dt.datetime(2026, 9, 11, 10, 0)),
            make_aware(dt.datetime(2026, 9, 12, 9, 0)),
            make_aware(dt.datetime(2026, 9, 13, 10, 30)),
            make_aware(dt.datetime(2026, 9, 14, 10, 0)),
            make_aware(dt.datetime(2026, 9, 15, 10, 0)),
            make_aware(dt.datetime(2026, 9, 16, 11, 0)),
            make_aware(dt.datetime(2026, 9, 17, 10, 0)),
            make_aware(dt.datetime(2026, 9, 18, 10, 0)),
            make_aware(dt.datetime(2026, 9, 19, 9, 30)),
        ]

        # Select FROZEN packages that don't have plans yet
        frozen_packages = [p for p in all_packages if p.current_state == PackageState.FROZEN]

        plans_created = 0
        target_idx = 0

        for pkg in frozen_packages:
            if target_idx >= len(target_dates):
                break

            target_ready = target_dates[target_idx]
            freeze_prof = freeze_standard if float(pkg.weight) <= 1.0 else freeze_quick
            thaw_prof = thaw_standard

            # Calculate durations
            from planning.services import calculate_rotation_plan

            try:
                plan_data = calculate_rotation_plan(pkg, target_ready, freeze_prof, thaw_prof)

                plan = RotationPlan.objects.create(
                    package=pkg,
                    target_ready_at=plan_data['target_ready_at'],
                    planned_thaw_start_at=plan_data['planned_thaw_start_at'],
                    planned_thaw_queue_at=plan_data['planned_thaw_queue_at'],
                    planned_freeze_start_at=plan_data['planned_freeze_start_at'],
                    planned_freeze_end_at=plan_data['planned_freeze_end_at'],
                    freeze_profile=freeze_prof,
                    thaw_profile=thaw_prof,
                    freeze_duration=plan_data['freeze_duration'],
                    thaw_duration=plan_data['thaw_duration'],
                    status=PlanStatus.PLANNED,
                )

                # Generate worker tasks
                from operations.services import generate_worker_tasks
                generate_worker_tasks(plan)

                plans_created += 1
                target_idx += 1
            except Exception as e:
                self.stdout.write(f'  ⚠ ไม่สามารถสร้างแผนสำหรับ {pkg.barcode}: {e}')

        self.stdout.write(f'  ✓ แผนการหมุนเวียน: {plans_created} แผน')

        # ============================================================
        # THAW QUEUE ENTRIES for THAW_QUEUED packages
        # ============================================================
        queued_packages = [p for p in all_packages if p.current_state == PackageState.THAW_QUEUED]
        queue_pos = 1
        for pkg in queued_packages:
            plan = RotationPlan.objects.filter(package=pkg).first()
            if not plan:
                # Create a plan for queued packages
                target = now + timedelta(days=14, hours=10)
                try:
                    plan_data = calculate_rotation_plan(pkg, target, freeze_standard, thaw_standard)
                    plan = RotationPlan.objects.create(
                        package=pkg,
                        target_ready_at=plan_data['target_ready_at'],
                        planned_thaw_start_at=plan_data['planned_thaw_start_at'],
                        planned_thaw_queue_at=plan_data['planned_thaw_queue_at'],
                        planned_freeze_start_at=plan_data['planned_freeze_start_at'],
                        planned_freeze_end_at=plan_data['planned_freeze_end_at'],
                        freeze_profile=freeze_standard,
                        thaw_profile=thaw_standard,
                        freeze_duration=plan_data['freeze_duration'],
                        thaw_duration=plan_data['thaw_duration'],
                        status=PlanStatus.PLANNED,
                    )
                except Exception:
                    continue

            ThawQueueEntry.objects.create(
                package=pkg,
                rotation_plan=plan,
                queue_position=queue_pos,
                planned_start_at=plan.planned_thaw_start_at,
                target_ready_at=plan.target_ready_at,
                status=QueueStatus.QUEUED,
            )
            queue_pos += 1
        self.stdout.write(f'  ✓ คิวละลายน้ำแข็ง: {queue_pos - 1} รายการ')

        # ============================================================
        # DEMO WORKER TASKS — completed and in-progress
        # ============================================================
        # Create some completed tasks to show history
        demo_task_types = [
            (TaskType.FREEZE_START, 3),
            (TaskType.FREEZE_CHECK, 2),
            (TaskType.MOVE_TO_THAW_QUEUE, 1),
            (TaskType.THAW_START, 2),
            (TaskType.THAW_CHECK, 1),
        ]

        # Get plans with rotation plans
        plans_with_tasks = RotationPlan.objects.select_related('package')[:5]
        tasks_created = 0
        for plan in plans_with_tasks:
            # Create a completed FREEZE_START task
            WorkerTask.objects.create(
                package=plan.package,
                rotation_plan=plan,
                task_type=TaskType.FREEZE_START,
                scheduled_at=plan.planned_freeze_start_at,
                status=TaskStatus.COMPLETED,
                completed_at=plan.planned_freeze_start_at + timedelta(minutes=15),
                completed_by='สมชาย พนักงาน A',
            )
            tasks_created += 1

        self.stdout.write(f'  ✓ งานพนักงาน (ตัวอย่าง): {tasks_created} งาน')

        # ============================================================
        # ROTATION EVENTS — audit trail for demo
        # ============================================================
        events_created = 0
        for pkg in all_packages:
            if pkg.current_state == PackageState.FROZEN:
                RotationEvent.objects.create(
                    package=pkg,
                    event_type='STATE_CHANGE',
                    from_state='PACKED',
                    to_state='FREEZING',
                    timestamp=pkg.packed_at + timedelta(hours=1),
                    actor='สมชาย พนักงาน A',
                    reason='เริ่มแช่แข็ง',
                )
                RotationEvent.objects.create(
                    package=pkg,
                    event_type='STATE_CHANGE',
                    from_state='FREEZING',
                    to_state='FROZEN',
                    timestamp=pkg.packed_at + timedelta(hours=26),
                    actor='ระบบ',
                    reason='แช่แข็งเสร็จสิ้น',
                )
                events_created += 2

        self.stdout.write(f'  ✓ บันทึกเหตุการณ์ (ตัวอย่าง): {events_created} เหตุการณ์')

        # ============================================================
        # SUMMARY
        # ============================================================
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('สร้างข้อมูลทดลองเสร็จสิ้น!'))
        self.stdout.write(self.style.SUCCESS(f'  สินค้า:          {Product.objects.count()} รายการ'))
        self.stdout.write(self.style.SUCCESS(f'  ล็อตสินค้า:      {Batch.objects.count()} ล็อต'))
        self.stdout.write(self.style.SUCCESS(f'  แพ็กเกจ:         {Package.objects.count()} ชิ้น'))
        self.stdout.write(self.style.SUCCESS(f'  สถานที่เก็บ:     {StorageLocation.objects.count()} แห่ง'))
        self.stdout.write(self.style.SUCCESS(f'  โปรไฟล์แช่แข็ง:  {FreezeProfile.objects.count()} โปรไฟล์'))
        self.stdout.write(self.style.SUCCESS(f'  โปรไฟล์ละลาย:    {ThawProfile.objects.count()} โปรไฟล์'))
        self.stdout.write(self.style.SUCCESS(f'  แผนหมุนเวียน:    {RotationPlan.objects.count()} แผน'))
        self.stdout.write(self.style.SUCCESS(f'  คิวละลาย:        {ThawQueueEntry.objects.count()} รายการ'))
        self.stdout.write(self.style.SUCCESS(f'  งานพนักงาน:      {WorkerTask.objects.count()} งาน'))
        self.stdout.write(self.style.SUCCESS(f'  บันทึกเหตุการณ์:  {RotationEvent.objects.count()} เหตุการณ์'))
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write('')
        self.stdout.write('สถานะแพ็กเกจ:')
        for state_val, state_label in PackageState.choices:
            count = Package.objects.filter(current_state=state_val).count()
            if count > 0:
                self.stdout.write(f'  {state_label}: {count} ชิ้น')
