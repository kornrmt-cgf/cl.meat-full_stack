# Generated migration - move freeze fields from Product_info to Product_list

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_meat', '0020_product_info_display_max_days_and_more'),
    ]

    operations = [
        # Add freeze fields to Product_list
        migrations.AddField(
            model_name='product_list',
            name='storage_status',
            field=models.CharField(
                choices=[
                    ('frozen', '❄️ แช่แข็ง'),
                    ('thawing', '🔄 กำลังละลาย'),
                    ('display', '🛒 วางขาย'),
                    ('depleted', '📦 หมด'),
                ],
                default='frozen',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='product_list',
            name='entered_display_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='product_list',
            name='display_max_days',
            field=models.PositiveIntegerField(
                default=3,
                help_text='วางขายได้สูงสุดกี่วัน',
            ),
        ),
        migrations.AddField(
            model_name='product_list',
            name='thaw_started_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='product_list',
            name='thaw_duration_hours',
            field=models.PositiveIntegerField(
                default=24,
                help_text='เวลาละลาย (ชั่วโมง)',
            ),
        ),
        migrations.AddField(
            model_name='product_list',
            name='thaw_queue_position',
            field=models.PositiveIntegerField(
                default=0,
                help_text='ลำดับในคิวละลาย (0 = ไม่ได้อยู่ในคิว)',
            ),
        ),
        migrations.AddField(
            model_name='product_list',
            name='rotate_priority',
            field=models.PositiveIntegerField(
                default=0,
                help_text='ความสำคัญในการหมุนเวียน (สูง = หมุนก่อน)',
            ),
        ),
        migrations.AddField(
            model_name='product_list',
            name='last_alert_at',
            field=models.DateTimeField(
                blank=True,
                help_text='แจ้งเตือนครั้งล่าสุดเมื่อไหร่',
                null=True,
            ),
        ),

        # Update FreezeRotation FK from product_info to product_list
        migrations.RemoveField(
            model_name='freezerotation',
            name='product_info',
        ),
        migrations.AddField(
            model_name='freezerotation',
            name='product_list',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='freeze_rotations',
                to='stock_meat.product_list',
            ),
        ),

        # Remove freeze fields from Product_info
        migrations.RemoveField(
            model_name='product_info',
            name='storage_status',
        ),
        migrations.RemoveField(
            model_name='product_info',
            name='entered_display_at',
        ),
        migrations.RemoveField(
            model_name='product_info',
            name='display_max_days',
        ),
        migrations.RemoveField(
            model_name='product_info',
            name='thaw_started_at',
        ),
        migrations.RemoveField(
            model_name='product_info',
            name='thaw_duration_hours',
        ),
        migrations.RemoveField(
            model_name='product_info',
            name='thaw_queue_position',
        ),
        migrations.RemoveField(
            model_name='product_info',
            name='rotate_priority',
        ),
        migrations.RemoveField(
            model_name='product_info',
            name='last_alert_at',
        ),
    ]
