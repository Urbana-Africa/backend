from django.db import migrations

STANDARD_SIZES = [
    ("XS", "Extra Small"),
    ("S", "Small"),
    ("M", "Medium"),
    ("L", "Large"),
    ("XL", "Extra Large"),
    ("XXL", "Double Extra Large"),
    ("XXXL", "Triple Extra Large"),
    ("One Size", "Fits all sizes"),
]


def seed_sizes(apps, schema_editor):
    Sizes = apps.get_model("core", "Sizes")
    for name, description in STANDARD_SIZES:
        Sizes.objects.get_or_create(name=name, defaults={"description": description})


def reverse_seed(apps, schema_editor):
    Sizes = apps.get_model("core", "Sizes")
    Sizes.objects.filter(name__in=[n for n, _ in STANDARD_SIZES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0027_product_fit_me_image"),
    ]

    operations = [
        migrations.RunPython(seed_sizes, reverse_seed),
    ]
