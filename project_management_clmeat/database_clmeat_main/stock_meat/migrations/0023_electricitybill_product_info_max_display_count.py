from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_meat', '0022_expensecategory_transaction_processtype_productprocessing'),
    ]

    operations = [
        migrations.CreateModel(
            name='ElectricityBill',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.PositiveIntegerField(help_text='เดือน (1-12)')),
                ('year', models.PositiveIntegerField(help_text='ปี พ.ศ. หรือ ค.ศ.')),
                ('units_used', models.DecimalField(decimal_places=2, help_text='หน่วยที่ใช้ (kWh)', max_digits=10)),
                ('total_amount', models.DecimalField(decimal_places=2, default=0, help_text='ยอดเงินที่ต้องจ่าย (บาท)', max_digits=12)),
                ('meter_reading', models.DecimalField(blank=True, decimal_places=2, help_text='เลขมิเตอร์ปัจจุบัน', max_digits=12, null=True)),
                ('previous_reading', models.DecimalField(blank=True, decimal_places=2, help_text='เลขมิเตอร์ครั้งก่อน', max_digits=12, null=True)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-year', '-month'],
                'unique_together': {('month', 'year')},
            },
        ),
        migrations.AddField(
            model_name='product_info',
            name='max_display_count',
            field=models.PositiveIntegerField(default=5, help_text='จำนวนสินค้าที่วางขายได้สูงสุดหน้าตู้โชว์'),
        ),
    ]
