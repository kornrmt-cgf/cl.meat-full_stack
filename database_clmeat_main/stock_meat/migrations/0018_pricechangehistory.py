from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("stock_meat", "0017_loyversesyncbatch_product_list_loyverse_synced_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PriceChangeHistory",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("old_price", models.FloatField(default=0)),
                ("new_price", models.FloatField(default=0)),
                ("mode", models.CharField(default="manual", max_length=30)),
                ("value", models.FloatField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("undone_at", models.DateTimeField(blank=True, null=True)),
                (
                    "product_list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="price_changes",
                        to="stock_meat.product_list",
                    ),
                ),
            ],
        ),
    ]
