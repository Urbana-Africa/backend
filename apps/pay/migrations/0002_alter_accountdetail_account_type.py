from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pay", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="accountdetail",
            name="account_type",
            field=models.CharField(
                choices=[
                    ("flutterwave", "Flutterwave"),
                    ("stripe", "Stripe"),
                ],
                default="flutterwave",
                max_length=20,
            ),
        ),
    ]
