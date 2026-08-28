"""
Utility functions สำหรับ CL.MEAT TaskManager

Timezone-aware helper สำหรับใช้ date ใน Asia/Bangkok timezone
"""


def today_local():
    """
    คืนค่า date ปัจจุบันใน timezone Asia/Bangkok

    แก้ปัญหา timezone.now().date() ที่คืนค่า UTC date แทน Bangkok date
    ทำให้งานหายจากหน้า "วันนี้" ในช่วง 00:00-07:00 เวลาไทย

    Returns:
        date object ใน timezone Bangkok
    """
    from django.utils import timezone as tz_utils
    return tz_utils.localtime(tz_utils.now()).date()


def now_local():
    """
    คืนค่า datetime ปัจจุบันใน timezone Asia/Bangkok

    Returns:
        datetime object ใน timezone Bangkok
    """
    from django.utils import timezone as tz_utils
    return tz_utils.localtime(tz_utils.now())
