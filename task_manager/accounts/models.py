"""
Custom User model สำหรับ Freebuff Desktop

ขยาย Django User model เพื่อรองรับ:
- Custom user ด้วย email แทน username
- Employee profile
- Role-based access
"""

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    """จัดการ User creation ด้วย email แทน username"""

    def create_user(self, userid=None, password=None, email=None, **extra_fields):
        if not userid and not email:
            raise ValueError("กรุณากรอก userid หรือ email")
        if email and not userid:
            extra_fields["userid"] = email.split("@")[0]
        elif userid:
            extra_fields["userid"] = userid
        if email:
            email = self.normalize_email(email)
        extra_fields.setdefault("is_active", True)
        user = self.model(email=email or "", **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, userid=None, password=None, email=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser ต้องมี is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser ต้องมี is_superuser=True")

        return self.create_user(userid=userid, password=password, email=email, **extra_fields)


class User(AbstractUser):
    """
    Custom User model
    ใช้ email สำหรับ login แทน username
    """

    username = None  # ไม่ใช้ username
    email = models.EmailField("email", unique=True)
    phone = models.CharField("เบอร์โทรศัพท์", max_length=20, blank=True)
    userid = models.CharField(
        "รหัสผู้ใช้",
        max_length=50,
        unique=True,
        blank=True,
        null=True,
        help_text="รหัสสำหรับเข้าสู่ระบบ (ตั้งเอง)",
    )
    avatar = models.ImageField("รูปโปรไฟล์", upload_to="avatars/", blank=True, null=True)

    USERNAME_FIELD = "userid"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

    class Meta:
        verbose_name = "ผู้ใช้"
        verbose_name_plural = "ผู้ใช้"
        ordering = ["-date_joined"]

    def __str__(self):
        return f"{self.get_full_name() or self.userid or self.email}"

    @property
    def display_name(self):
        """ชื่อที่แสดง"""
        return self.get_full_name() or self.userid or self.email

    @property
    def initials(self):
        """ตัวอักษรย่อ"""
        name = self.get_full_name()
        if name:
            parts = name.split()
            return "".join([p[0].upper() for p in parts[:2]])
        if self.email:
            return self.email[0].upper()
        if self.userid:
            return self.userid[0].upper()
        return "?"


class Role(models.Model):
    """
    บทบาทของพนักงาน
    เช่น Manager, Staff, Driver, Warehouse
    """

    name = models.CharField("ชื่อบทบาท", max_length=100, unique=True)
    slug = models.SlugField("Slug", unique=True)
    description = models.TextField("คำอธิบาย", blank=True)
    color = models.CharField("สี", max_length=7, default="#6366f1")  # hex color
    icon = models.CharField("ไอคอน", max_length=50, blank=True)  # lucide icon name
    is_active = models.BooleanField("ใช้งาน", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "บทบาท"
        verbose_name_plural = "บทบาท"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Team(models.Model):
    """
    ทีม/แผนก
    """

    name = models.CharField("ชื่อทีม", max_length=100, unique=True)
    slug = models.SlugField("Slug", unique=True)
    description = models.TextField("คำอธิบาย", blank=True)
    color = models.CharField("สี", max_length=7, default="#8b5cf6")
    leader = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_teams",
        verbose_name="หัวหน้าทีม",
    )
    is_active = models.BooleanField("ใช้งาน", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ทีม"
        verbose_name_plural = "ทีม"
        ordering = ["name"]

    def __str__(self):
        return self.name


class EmployeeProfile(models.Model):
    """
    ข้อมูลเพิ่มเติมของพนักงาน
    เชื่อมกับ User model
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "ทำงานอยู่"
        ON_LEAVE = "on_leave", "ลา"
        SUSPENDED = "suspended", "พักงาน"
        TERMINATED = "terminated", "ออกจากงาน"

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="ผู้ใช้",
    )
    employee_id = models.CharField("รหัสพนักงาน", max_length=20, unique=True, blank=True)
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees",
        verbose_name="บทบาท",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
        verbose_name="ทีม",
    )
    status = models.CharField(
        "สถานะ",
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    position = models.CharField("ตำแหน่ง", max_length=100, blank=True)
    hire_date = models.DateField("วันที่เริ่มงาน", null=True, blank=True)
    notes = models.TextField("หมายเหตุ", blank=True)

    # การตั้งค่าส่วนตัว
    theme = models.CharField(
        "ธีม",
        max_length=10,
        choices=[("dark", "มืด"), ("light", "สว่าง"), ("system", "ตามระบบ")],
        default="dark",
    )
    notification_enabled = models.BooleanField("เปิดแจ้งเตือน", default=True)
    sound_enabled = models.BooleanField("เปิดเสียง", default=True)

    # Per-type notification preferences
    notify_task_assigned = models.BooleanField("แจ้งเตือนมอบหมายงาน", default=True)
    notify_task_starting = models.BooleanField("แจ้งเตือนงานกำลังจะเริ่ม", default=True)
    notify_task_overdue = models.BooleanField("แจ้งเตือนงานเกินกำหนด", default=True)
    notify_task_rescheduled = models.BooleanField("แจ้งเตือนเปลี่ยนเวลางาน", default=True)
    notify_task_problem = models.BooleanField("แจ้งเตือนรายงานปัญหา", default=True)
    notify_task_error = models.BooleanField("แจ้งเตือนรายงานข้อผิดพลาด", default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "โปรไฟล์พนักงาน"
        verbose_name_plural = "โปรไฟล์พนักงาน"

    def __str__(self):
        return f"{self.user.display_name} ({self.employee_id or 'N/A'})"

    def save(self, *args, **kwargs):
        # Auto-generate employee_id ถ้ายังไม่มี
        if not self.employee_id:
            last = EmployeeProfile.objects.order_by("-id").first()
            next_num = (last.id + 1) if last else 1
            self.employee_id = f"EMP{next_num:04d}"
        super().save(*args, **kwargs)
