from django.db import models
from django.utils import timezone
from datetime import timedelta


class Category(models.Model):

    ids = models.AutoField(
        primary_key=True
    )

    name_type = models.CharField(
        max_length=50
    )

    def __str__(self):
        return self.name_type


class Supply_meat(models.Model):

    ids = models.AutoField(
        primary_key=True
    )

    name_place = models.CharField(
        max_length=20
    )

    locations = models.CharField(
        max_length=200
    )

    def __str__(self):
        return self.name_place


class meat_parts(models.Model):

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='meat_parts',
        null=True,
        blank=True,
    )

    name = models.CharField(
        max_length=30
    )

    prefix_barcode = models.CharField(
        max_length=100
    )

    kcalories = models.FloatField(
        default=0
    )

    protent = models.FloatField(
        default=0
    )

    fat = models.FloatField(
        default=0
    )

    def __str__(self):
        return self.name


# ============================================================
# LOYVERSE SYNC BATCH
# ============================================================

class LoyverseSyncBatch(models.Model):

    confirmed_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):

        local_time = self.confirmed_at

        return (
            f"Sync #{self.id} - "
            f"{local_time.strftime('%d/%m/%Y %H:%M')}"
        )

    @property
    def item_count(self):

        return self.products.count()


# ============================================================
# PRODUCT INFO
# ============================================================

class Product_info(models.Model):

    type_product = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True
    )

    import_from = models.ForeignKey(
        Supply_meat,
        on_delete=models.SET_NULL,
        null=True
    )

    # --------------------------------------------------------
    # Lot
    # --------------------------------------------------------

    lot_number = models.PositiveIntegerField(
        default=1
    )

    name = models.ForeignKey(
        meat_parts,
        on_delete=models.SET_NULL,
        null=True
    )

    # --------------------------------------------------------
    # Stock
    # --------------------------------------------------------

    weight = models.FloatField()

    # --------------------------------------------------------
    # Cost
    # --------------------------------------------------------

    cost = models.FloatField(
        blank=True,
        null=True
    )

    # --------------------------------------------------------
    # Selling price / KG
    # --------------------------------------------------------

    selling_price_per_kg = models.FloatField(
        default=0
    )

    # --------------------------------------------------------
    # Display limit
    # --------------------------------------------------------

    max_display_count = models.PositiveIntegerField(
        default=5,
        help_text='จำนวนสินค้าที่วางขายได้สูงสุดหน้าตู้โชว์',
    )

    # --------------------------------------------------------
    # Created
    # --------------------------------------------------------

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    # ========================================================
    # COMPUTED PROPERTIES
    # ========================================================

    def __str__(self):

        return (
            f"{self.name}, "
            f"ล็อต {self.lot_number}, "
            f"น้ำหนัก {self.weight} กรัม, "
            f"จาก {self.import_from}"
        )

    @property
    def profit_per_kg(self):

        cost = float(
            self.cost or 0
        )

        selling = float(
            self.selling_price_per_kg or 0
        )

        return selling - cost

    @property
    def profit_percent(self):

        cost = float(
            self.cost or 0
        )

        selling = float(
            self.selling_price_per_kg or 0
        )

        if cost <= 0:
            return 0

        return (
            (selling - cost)
            / cost
            * 100
        )

# ============================================================
# FREEZE ROTATION HISTORY
# ============================================================

class FreezeRotation(models.Model):

    ACTION_THAW_START = 'thaw_start'
    ACTION_THAW_READY = 'thaw_ready'
    ACTION_DISPLAY_START = 'display_start'
    ACTION_DISPLAY_END = 'display_end'
    ACTION_FREEZE_RETURN = 'freeze_return'
    ACTION_ALERT_SENT = 'alert_sent'

    ACTION_CHOICES = [
        (ACTION_THAW_START, '🔄 เริ่มละลาย'),
        (ACTION_THAW_READY, '✅ ละลายเสร็จ'),
        (ACTION_DISPLAY_START, '🛒 เริ่มวางขาย'),
        (ACTION_DISPLAY_END, '⏸️ หยุดวางขาย'),
        (ACTION_FREEZE_RETURN, '🧊 กลับแช่'),
        (ACTION_ALERT_SENT, '🔔 ส่งแจ้งเตือน'),
    ]

    product_list = models.ForeignKey(
        'Product_list',
        on_delete=models.CASCADE,
        related_name='freeze_rotations',
    )

    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
    )

    performed_at = models.DateTimeField(
        auto_now_add=True,
    )

    notes = models.TextField(
        blank=True,
        default='',
    )

    # --------------------------------------------------------
    # ข้อมูลเสริม
    # --------------------------------------------------------

    weight_at_action = models.FloatField(
        default=0,
        help_text='น้ำหนัก ณ เวลาที่ทำรายการ',
    )

    status_before = models.CharField(
        max_length=20,
        blank=True,
        default='',
    )

    status_after = models.CharField(
        max_length=20,
        blank=True,
        default='',
    )

    def __str__(self):

        action_label = dict(
            self.ACTION_CHOICES
        ).get(
            self.action,
            self.action
        )

        return (
            f"{action_label} - "
            f"{self.product_list}"
        )

    class Meta:

        ordering = ['-performed_at']


# ============================================================
# PRODUCT LIST
# ============================================================

class Product_list(models.Model):

    # ========================================================
    # STORAGE STATUS
    # ========================================================

    STATUS_FROZEN = 'frozen'
    STATUS_THAWING = 'thawing'
    STATUS_DISPLAY = 'display'
    STATUS_DEPLETED = 'depleted'

    STORAGE_STATUS_CHOICES = [
        (STATUS_FROZEN, '❄️ แช่แข็ง'),
        (STATUS_THAWING, '🔄 กำลังละลาย'),
        (STATUS_DISPLAY, '🛒 วางขาย'),
        (STATUS_DEPLETED, '📦 หมด'),
    ]

    product = models.ForeignKey(
        Product_info,
        on_delete=models.SET_NULL,
        null=True
    )

    barcode = models.CharField(
        max_length=100
    )

    weight = models.FloatField()

    # ราคาขายของแพ็ค
    selling_price = models.FloatField(
        default=0
    )

    activated = models.BooleanField(
        default=False
    )

    mfg = models.DateTimeField(
        auto_now_add=True
    )

    # ========================================================
    # LOYVERSE
    # ========================================================

    loyverse_sku = models.CharField(
        max_length=40,
        unique=True,
        null=True,
        blank=True
    )

    loyverse_item_id = models.CharField(
        max_length=100,
        null=True,
        blank=True
    )

    loyverse_variant_id = models.CharField(
        max_length=100,
        null=True,
        blank=True
    )

    # --------------------------------------------------------
    # False = ยังไม่ยืนยันว่าเข้า Loyverse
    # True  = ยืนยันแล้ว
    # --------------------------------------------------------

    loyverse_synced = models.BooleanField(
        default=False
    )

    # --------------------------------------------------------
    # วันที่ยืนยัน Sync
    # --------------------------------------------------------

    loyverse_synced_at = models.DateTimeField(
        null=True,
        blank=True
    )

    # --------------------------------------------------------
    # Folder / Batch
    # --------------------------------------------------------

    loyverse_sync_batch = models.ForeignKey(
        LoyverseSyncBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )

    # ========================================================
    # FREEZE / THAW MANAGEMENT
    # ========================================================

    storage_status = models.CharField(
        max_length=20,
        choices=STORAGE_STATUS_CHOICES,
        default=STATUS_FROZEN,
    )

    # --------------------------------------------------------
    # Display
    # --------------------------------------------------------

    entered_display_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    display_max_days = models.PositiveIntegerField(
        default=3,
        help_text='วางขายได้สูงสุดกี่วัน',
    )

    # --------------------------------------------------------
    # Thaw
    # --------------------------------------------------------

    thaw_started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    thaw_duration_hours = models.PositiveIntegerField(
        default=24,
        help_text='เวลาละลาย (ชั่วโมง)',
    )

    thaw_queue_position = models.PositiveIntegerField(
        default=0,
        help_text='ลำดับในคิวละลาย (0 = ไม่ได้อยู่ในคิว)',
    )

    thaw_scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='เวลาที่เข้าคิวละลาย',
    )

    thaw_target_ready_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='เวลาที่ต้องการให้ละลายเสร็จ (พร้อมจำหน่าย)',
    )

    # --------------------------------------------------------
    # Freeze scheduling
    # --------------------------------------------------------

    freeze_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='เวลาที่เริ่มแช่แข็ง',
    )

    freeze_duration_minutes = models.PositiveIntegerField(
        default=0,
        help_text='ระยะเวลาแช่แข็งที่กำหนด (นาที)',
    )

    freeze_end_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='เวลาที่คาดว่าแช่แข็งเสร็จ (ตั้งเอง)',
    )

    freeze_target_temp = models.IntegerField(
        default=-8,
        help_text='อุณหภูมิเป้าหมายของตู้แช่ (°C)',
    )

    # --------------------------------------------------------
    # Priority
    # --------------------------------------------------------

    rotate_priority = models.PositiveIntegerField(
        default=0,
        help_text='ความสำคัญในการหมุนเวียน (สูง = หมุนก่อน)',
    )

    # --------------------------------------------------------
    # Alert
    # --------------------------------------------------------

    last_alert_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='แจ้งเตือนครั้งล่าสุดเมื่อไหร่',
    )

    # ========================================================
    # COMPUTED PROPERTIES
    # ========================================================

    def __str__(self):

        if self.product:

            product_name = (
                self.product.name.name
                if self.product.name
                else ''
            )

        else:

            product_name = ''

        status_emoji = dict(
            self.STORAGE_STATUS_CHOICES
        ).get(
            self.storage_status,
            ''
        )

        return (
            f"{status_emoji} {product_name} "
            f"{self.weight} กรัม "
            f"฿{self.selling_price}"
        )

    @property
    def display_days_remaining(self):

        if (
            self.storage_status
            != self.STATUS_DISPLAY
        ):
            return None

        if not self.entered_display_at:
            return None

        elapsed = (
            timezone.now()
            - self.entered_display_at
        ).days

        return max(
            0,
            self.display_max_days - elapsed,
        )

    @property
    def display_end_at(self):
        """Calculate when display period ends."""
        if self.storage_status != self.STATUS_DISPLAY:
            return None
        if not self.entered_display_at:
            return None
        return self.entered_display_at + timedelta(days=self.display_max_days)

    @property
    def is_display_expired(self):

        if (
            self.storage_status
            != self.STATUS_DISPLAY
        ):
            return False

        return (
            self.display_days_remaining
            is not None
            and self.display_days_remaining <= 0
        )

    @property
    def thaw_ready_at(self):

        if (
            self.storage_status
            != self.STATUS_THAWING
        ):
            return None

        if not self.thaw_started_at:
            return None

        return (
            self.thaw_started_at
            + timedelta(
                hours=self.thaw_duration_hours
            )
        )

    @property
    def is_thaw_complete(self):

        if (
            self.storage_status
            != self.STATUS_THAWING
        ):
            return False

        ready_at = self.thaw_ready_at

        if not ready_at:
            return False

        return timezone.now() >= ready_at

    @property
    def thaw_hours_remaining(self):

        if (
            self.storage_status
            != self.STATUS_THAWING
        ):
            return None

        ready_at = self.thaw_ready_at

        if not ready_at:
            return None

        remaining = (
            ready_at - timezone.now()
        ).total_seconds() / 3600

        return max(
            0,
            round(remaining, 1),
        )


# ============================================================
# ROTATION SCHEDULE
# ============================================================

class RotationSchedule(models.Model):
    """
    แผนการหมุนเวียนสินค้า
    เก็บ timeline ทั้งหมดของแต่ละแพ็ค:
    target_ready_at → thaw_start → freeze_end → freeze_start
    """

    STATUS_PLANNED = 'planned'
    STATUS_ACTIVE = 'active'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_PLANNED, '📋 วางแผน'),
        (STATUS_ACTIVE, '🔄 กำลังดำเนินการ'),
        (STATUS_COMPLETED, '✅ เสร็จสิ้น'),
        (STATUS_CANCELLED, '❌ ยกเลิก'),
    ]

    product_list = models.ForeignKey(
        'Product_list',
        on_delete=models.CASCADE,
        related_name='rotation_schedules',
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PLANNED,
    )

    # Target
    target_ready_at = models.DateTimeField(
        help_text='เวลาที่ต้องการให้สินค้าพร้อมจำหน่าย',
    )

    # Calculated schedule
    thaw_start_at = models.DateTimeField(
        null=True, blank=True,
        help_text='เวลาที่ต้องเริ่มละลาย (คำนวณจาก target - thaw_duration - buffer)',
    )
    freeze_end_at = models.DateTimeField(
        null=True, blank=True,
        help_text='เวลาที่แช่แข็งเสร็จ (ต้องก่อน thaw_start)',
    )
    freeze_start_at = models.DateTimeField(
        null=True, blank=True,
        help_text='เวลาที่เริ่มแช่แข็ง',
    )

    # Durations (minutes)
    freeze_duration_minutes = models.PositiveIntegerField(
        default=0,
        help_text='ระยะเวลาแช่แข็ง (นาที)',
    )
    thaw_duration_minutes = models.PositiveIntegerField(
        default=0,
        help_text='ระยะเวลาละลาย (นาที)',
    )
    buffer_minutes = models.PositiveIntegerField(
        default=120,
        help_text='เวลา buffer ก่อน ready (นาที)',
    )

    # Estimated vs actual
    freeze_duration_estimated = models.PositiveIntegerField(
        default=0,
        help_text='เวลาแช่ประมาณ (นาที)',
    )
    thaw_duration_estimated = models.PositiveIntegerField(
        default=0,
        help_text='เวลาละลายประมาณ (นาที)',
    )
    is_override = models.BooleanField(
        default=False,
        help_text='True = ผู้ใช้แก้ไขเวลาเอง',
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True, default='')

    def __str__(self):
        return (
            f"Schedule #{self.id} - {self.product_list} "
            f"→ ready {self.target_ready_at.strftime('%d/%m/%Y %H:%M')}"
        )

    class Meta:
        ordering = ['target_ready_at']


# ============================================================
# WORKER TASK
# ============================================================

class WorkerTask(models.Model):
    """
    งานที่คนงานต้องทำในแต่ละวัน
    สร้างจาก RotationSchedule
    """

    TASK_TYPES = [
        ('freeze_start', '🧊 เริ่มแช่แข็ง'),
        ('freeze_check', '🔍 ตรวจสอบแช่'),
        ('thaw_queue', '📋 เข้าคิวละลาย'),
        ('thaw_start', '🔄 เริ่มละลาย'),
        ('thaw_check', '🔍 ตรวจสอบละลาย'),
        ('display_start', '🛒 นำออกวางขาย'),
        ('display_return', '🧊 นำกลับแช่'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED = 'completed'
    STATUS_SKIPPED = 'skipped'
    STATUS_OVERDUE = 'overdue'

    STATUS_CHOICES = [
        (STATUS_PENDING, '⏳ รอดำเนินการ'),
        (STATUS_IN_PROGRESS, '🔄 กำลังทำ'),
        (STATUS_COMPLETED, '✅ เสร็จแล้ว'),
        (STATUS_SKIPPED, '⏭️ ข้าม'),
        (STATUS_OVERDUE, '🔴 เกินกำหนด'),
    ]

    rotation_schedule = models.ForeignKey(
        RotationSchedule,
        on_delete=models.CASCADE,
        related_name='tasks',
    )

    task_type = models.CharField(
        max_length=20,
        choices=TASK_TYPES,
    )

    scheduled_at = models.DateTimeField(
        help_text='เวลาที่กำหนดให้ทำ',
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    completed_at = models.DateTimeField(
        null=True, blank=True)
    completed_by = models.CharField(
        max_length=100, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        label = dict(self.TASK_TYPES).get(self.task_type, self.task_type)
        return (
            f"{label} - {self.rotation_schedule.product_list} "
            f"@ {self.scheduled_at.strftime('%d/%m %H:%M')}"
        )

    @property
    def is_overdue(self):
        if self.status in (self.STATUS_COMPLETED, self.STATUS_SKIPPED):
            return False
        return timezone.now() > self.scheduled_at

    class Meta:
        ordering = ['scheduled_at']


# ============================================================
# PRICE CHANGE HISTORY / UNDO
# ============================================================

class PriceChangeHistory(models.Model):

    product_list = models.ForeignKey(
        Product_list,
        on_delete=models.CASCADE,
        related_name="price_changes",
    )

    old_price = models.FloatField(default=0)

    new_price = models.FloatField(default=0)

    mode = models.CharField(
        max_length=30,
        default="manual",
    )

    value = models.FloatField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    undone_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    def __str__(self):

        return (
            f"Price #{self.id} - "
            f"Product {self.product_list_id} - "
            f"{self.old_price} -> {self.new_price}"
        )


# ============================================================
# EXPENSE / INCOME CATEGORIES
# ============================================================

class ExpenseCategory(models.Model):
    """หมวดหมู่ค่าใช้จ่าย/รายรับ"""

    CATEGORY_TYPES = [
        ('expense', 'รายจ่าย'),
        ('income', 'รายรับ'),
    ]

    name = models.CharField(max_length=100)
    category_type = models.CharField(
        max_length=10,
        choices=CATEGORY_TYPES,
        default='expense',
    )
    icon = models.CharField(
        max_length=10,
        blank=True,
        default='📦',
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.icon} {self.name}"

    class Meta:
        verbose_name_plural = 'Expense Categories'


# ============================================================
# TRANSACTIONS (Income & Expense)
# ============================================================

class Transaction(models.Model):
    """บันทึกรายรับ-รายจ่าย"""

    TYPE_CHOICES = [
        ('income', 'รายรับ'),
        ('expense', 'รายจ่าย'),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('cash', 'เงินสด'),
        ('transfer', 'โอน'),
        ('card', 'บัตร'),
        ('promptpay', 'PromptPay'),
    ]

    transaction_type = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
    )
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    description = models.TextField(
        blank=True,
        default='',
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='cash',
    )
    receipt_date = models.DateField()
    receipt_number = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text='เลขที่ใบเสร็จ/บิล',
    )
    notes = models.TextField(
        blank=True,
        default='',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        sign = '+' if self.transaction_type == 'income' else '-'
        return (
            f"{sign}฿{self.amount:,.2f} "
            f"({self.get_transaction_type_display()}) "
            f"{self.description[:30]}"
        )

    @property
    def amount_float(self):
        return float(self.amount)

    class Meta:
        ordering = ['-receipt_date', '-created_at']


# ============================================================
# PRODUCT PROCESSING (Processing Zone)
# ============================================================

class ProcessType(models.Model):
    """ประเภทการแปรรูป"""

    name = models.CharField(max_length=100)
    description = models.TextField(
        blank=True,
        default='',
    )
    output_price_per_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text='ราคาขายหลังแปรรูป (ต่อกิโล)',
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'Process Types'


class ProductProcessing(models.Model):
    """บันทึกการแปรรูปสินค้า"""

    ACTION_CHOICES = [
        ('process', '🔄 แปรรูป'),
        ('donate', '🎁 บริจาค'),
        ('discard', '🗑️ ทิ้ง'),
    ]

    product_list = models.ForeignKey(
        'Product_list',
        on_delete=models.CASCADE,
        related_name='processings',
    )
    process_type = models.ForeignKey(
        ProcessType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    action = models.CharField(
        max_length=10,
        choices=ACTION_CHOICES,
        default='process',
    )
    input_weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='น้ำหนักก่อนแปรรูป (กรัม)',
    )
    output_weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='น้ำหนักหลังแปรรูป (กรัม)',
    )
    output_product = models.ForeignKey(
        'Product_info',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='processing_outputs',
    )
    processed_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(
        blank=True,
        default='',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        action_label = dict(self.ACTION_CHOICES).get(
            self.action, self.action
        )
        return (
            f"{action_label} - "
            f"{self.product_list} "
            f"({self.input_weight}g)"
        )

    @property
    def input_weight_float(self):
        return float(self.input_weight)

    @property
    def output_weight_float(self):
        return float(self.output_weight) if self.output_weight else 0

    @property
    def yield_percent(self):
        if self.output_weight and self.input_weight:
            return round(
                float(self.output_weight) / float(self.input_weight) * 100,
                1,
            )
        return 0

    class Meta:
        ordering = ['-processed_at']


# ============================================================
# ELECTRICITY BILL
# ============================================================

class ElectricityBill(models.Model):
    """บันทึกค่าไฟฟ้า"""

    RATE_PER_UNIT = 4.58  # บาท/หน่วย

    month = models.PositiveIntegerField(
        help_text='เดือน (1-12)',
    )
    year = models.PositiveIntegerField(
        help_text='ปี พ.ศ. หรือ ค.ศ.',
    )
    units_used = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='หน่วยที่ใช้ (kWh)',
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='ยอดเงินที่ต้องจ่าย (บาท)',
    )
    meter_reading = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='เลขมิเตอร์ปัจจุบัน',
    )
    previous_reading = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='เลขมิเตอร์ครั้งก่อน',
    )
    notes = models.TextField(
        blank=True,
        default='',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return (
            f"ค่าไฟ {self.month:02d}/{self.year} "
            f"- {self.units_used} หน่วย "
            f"฿{self.total_amount:,.2f}"
        )

    @property
    def units_used_float(self):
        return float(self.units_used)

    @property
    def total_amount_float(self):
        return float(self.total_amount)

    def save(self, *args, **kwargs):
        if not self.total_amount or self.total_amount == 0:
            self.total_amount = float(self.units_used) * self.RATE_PER_UNIT
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-year', '-month']
        unique_together = ['month', 'year']


# ============================================================
# DAILY ELECTRICITY
# ============================================================

class DailyElectricity(models.Model):
    """บันทึกหน่วยไฟรายวัน"""

    date = models.DateField(unique=True)
    meter_reading = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='เลขมิเตอร์ ณ วันนั้น',
    )
    units_used = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='หน่วยที่ใช้ในวันนั้น',
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='ค่าไฟในวันนั้น (บาท)',
    )
    rate_per_unit = models.DecimalField(
        max_digits=6, decimal_places=2, default=4.58,
    )
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.date} - {self.units_used} หน่วย = ฿{self.amount}"

    class Meta:
        ordering = ['-date']


# ============================================================


# SOLD ITEMS (Loyverse Receipt Sync)
# ============================================================

class SoldItem(models.Model):
    """สินค้าที่ขายแล้ว - ดึงจาก Loyverse Receipts"""

    product_list = models.ForeignKey(
        Product_list,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sold_items',
    )

    # Loyverse receipt data
    receipt_number = models.CharField(max_length=50)
    receipt_date = models.DateTimeField()
    store_id = models.CharField(max_length=100, blank=True, default='')

    # Item data
    loyverse_sku = models.CharField(max_length=40)
    item_name = models.CharField(max_length=200, blank=True, default='')
    quantity = models.IntegerField(default=1)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Calculated fields
    profit = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    profit_percent = models.FloatField(default=0)
    days_to_sell = models.IntegerField(default=0, help_text='จำนวนวันจาก mfg ถึงขาย')
    electricity_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='ค่าไฟที่เสียไปกับชิ้นนี้')

    # Loyverse IDs for dedup
    loyverse_item_id = models.CharField(max_length=100, blank=True, default='')
    loyverse_variant_id = models.CharField(max_length=100, blank=True, default='')
    loyverse_receipt_id = models.CharField(max_length=100, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-receipt_date']
        indexes = [
            models.Index(fields=['loyverse_sku']),
            models.Index(fields=['receipt_date']),
            models.Index(fields=['loyverse_receipt_id']),
        ]

    def __str__(self):
        return f"SKU {self.loyverse_sku} - {self.receipt_number} ({self.receipt_date})"

