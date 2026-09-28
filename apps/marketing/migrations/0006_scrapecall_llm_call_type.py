from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0005_set_instagram_2_api_defaults'),
    ]

    operations = [
        migrations.AlterField(
            model_name='scrapecall',
            name='call_type',
            field=models.CharField(choices=[('search', 'Search'), ('extract', 'Extract'), ('llm', 'LLM Parse')], max_length=20),
        ),
    ]
