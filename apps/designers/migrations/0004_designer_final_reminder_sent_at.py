from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("designers", "0003_remove_designer_local_shipping_fee"),
    ]

    operations = [
        migrations.AddField(
            model_name="designer",
            name="final_reminder_sent_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Last-chance 7-day reminder for designers still under "
                    "5 products"
                ),
                null=True,
            ),
        ),
    ]
