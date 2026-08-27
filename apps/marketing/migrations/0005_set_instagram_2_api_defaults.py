from decimal import Decimal
from django.db import migrations


def set_instagram_2_api_defaults(apps, schema_editor):
    ScrapeProviderConfig = apps.get_model("marketing", "ScrapeProviderConfig")

    dataforseo, _ = ScrapeProviderConfig.objects.get_or_create(
        name="dataforseo",
        defaults={
            "enabled": True,
            "priority": 1,
            "cost_per_1k_credits": Decimal("0.80"),
            "monthly_budget": Decimal("20.00"),
            "config": {},
        },
    )
    dataforseo.enabled = True
    dataforseo.priority = 1
    dataforseo.cost_per_1k_credits = Decimal("0.80")
    dataforseo.monthly_budget = Decimal("20.00")
    dataforseo.save()

    brightdata, _ = ScrapeProviderConfig.objects.get_or_create(
        name="brightdata",
        defaults={
            "enabled": True,
            "priority": 2,
            "cost_per_1k_credits": Decimal("1.50"),
            "monthly_budget": Decimal("50.00"),
            "config": {
                "instagram_dataset_id": "gd_l1vikfch901nx3by4",
            },
        },
    )
    brightdata.enabled = True
    brightdata.priority = 2
    brightdata.cost_per_1k_credits = Decimal("1.50")
    brightdata.monthly_budget = Decimal("50.00")
    brightdata.save()

    ScrapeProviderConfig.objects.filter(
        name__in=("apify", "firecrawl", "zyte")
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("marketing", "0004_designerlead_confidence_score_and_more"),
    ]

    operations = [
        migrations.RunPython(
            set_instagram_2_api_defaults,
            migrations.RunPython.noop,
        ),
    ]
