from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('stock_meat', '0021_product_list_storage_status_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExpenseCategory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('category_type', models.CharField(choices=[('expense', 'รายจ่าย'), ('income', 'รายรับ')], default='expense', max_length=10)),
                ('icon', models.CharField(blank=True, default='📦', max_length=10)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name_plural': 'Expense Categories',
            },
        ),
        migrations.CreateModel(
            name='Transaction',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('transaction_type', models.CharField(choices=[('income', 'รายรับ'), ('expense', 'รายจ่าย')], max_length=10)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('description', models.TextField(blank=True, default='')),
                ('payment_method', models.CharField(choices=[('cash', 'เงินสด'), ('transfer', 'โอน'), ('card', 'บัตร'), ('promptpay', 'PromptPay')], default='cash', max_length=20)),
                ('receipt_date', models.DateField()),
                ('receipt_number', models.CharField(blank=True, default='', help_text='เลขที่ใบเสร็จ/บิล', max_length=50)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='stock_meat.expensecategory')),
            ],
            options={
                'ordering': ['-receipt_date', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ProcessType',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True, default='')),
                ('output_price_per_kg', models.DecimalField(decimal_places=2, default=0, help_text='ราคาขายหลังแปรรูป (ต่อกิโล)', max_digits=10)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name_plural': 'Process Types',
            },
        ),
        migrations.CreateModel(
            name='ProductProcessing',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('process', '🔄 แปรรูป'), ('donate', '🎁 บริจาค'), ('discard', '🗑️ ทิ้ง')], default='process', max_length=10)),
                ('input_weight', models.DecimalField(decimal_places=2, help_text='น้ำหนักก่อนแปรรูป (กรัม)', max_digits=10)),
                ('output_weight', models.DecimalField(blank=True, decimal_places=2, help_text='น้ำหนักหลังแปรรูป (กรัม)', max_digits=10, null=True)),
                ('processed_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('output_product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='processing_outputs', to='stock_meat.product_info')),
                ('process_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='stock_meat.processtype')),
                ('product_list', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='processings', to='stock_meat.product_list')),
            ],
            options={
                'ordering': ['-processed_at'],
            },
        ),
    ]
