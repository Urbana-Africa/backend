import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'urbana.settings')
django.setup()

from apps.core.models import Category
from django.utils.text import slugify

MAIN_CATEGORIES = [
  { "value": "clothing", "label": "Clothing" },
  { "value": "shoes", "label": "Shoes" },
  { "value": "accessories", "label": "Accessories" },
]

# Clear existing categories to keep it clean
Category.objects.all().delete()

for item in MAIN_CATEGORIES:
    Category.objects.create(
        id=item["value"],
        name=item["label"],
        slug=slugify(item["label"])
    )

print("Main categories seeded successfully.")
