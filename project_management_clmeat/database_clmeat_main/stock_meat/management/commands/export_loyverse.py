import os

from django.core.management.base import BaseCommand

from stock_meat.loyverse_export import (
    generate_loyverse_csv
)


class Command(BaseCommand):

    help = (
        "Export เฉพาะ Product_list "
        "ที่ยังไม่ได้ยืนยัน Loyverse"
    )

    def handle(
        self,
        *args,
        **options
    ):

        filename = (
            "loyverse_products.csv"
        )

        csv_content, count = (
            generate_loyverse_csv()
        )

        with open(
            filename,
            "w",
            encoding="utf-8-sig",
            newline=""
        ) as csvfile:

            csvfile.write(
                csv_content
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Export สำเร็จ"
            )
        )

        self.stdout.write(
            f"จำนวนสินค้าใหม่: {count}"
        )

        self.stdout.write(
            "ไฟล์:"
        )

        self.stdout.write(
            os.path.abspath(
                filename
            )
        )