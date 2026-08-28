"""
Context processors สำหรับ accounts
จัดการ theme และข้อมูลที่ต้องใช้ใน template ทุกหน้า
"""


def theme_context(request):
    """
    เพิ่มข้อมูล theme ใน context ทุกหน้า
    รองรับ dark/light mode toggle
    """
    context = {
        "user_theme": "dark",  # default
    }

    if request.user.is_authenticated:
        # ใช้ theme จาก profile ถ้ามี
        profile = getattr(request.user, "profile", None)
        if profile:
            context["user_theme"] = profile.theme

    return context
