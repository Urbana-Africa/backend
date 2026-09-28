from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0006_scrapecall_llm_call_type'),
    ]

    operations = [
        # Per-call costs are sub-cent (e.g. $0.0015/extract) — 2dp silently
        # rounded every increment to 0.00, so spend never accumulated and the
        # monthly budget cap could never trip.
        migrations.AlterField(
            model_name='scrapeproviderconfig',
            name='monthly_spend',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=12),
        ),
    ]
