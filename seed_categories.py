import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'urbana.settings')
django.setup()

from apps.core.models import Category
from django.utils.text import slugify

CATEGORIES = [
  # Main Categories
  { "value": "clothing", "label": "Clothing" },
  { "value": "shoes", "label": "Shoes" },
  { "value": "accessories", "label": "Accessories" },
  { "value": "bags", "label": "Bags" },
  # Subcategories
  { "value": "womenswear", "label": "Womenswear" },
  { "value": "menswear", "label": "Menswear" },
  { "value": "unisex", "label": "Unisex" },
  { "value": "kidswear", "label": "Kidswear" },
  { "value": "streetwear", "label": "Streetwear" },
]

# Clear existing categories to keep it clean
Category.objects.all().delete()

for item in CATEGORIES:
    Category.objects.create(
        id=item["value"],
        name=item["label"],
        slug=slugify(item["label"])
    )

print("Categories seeded successfully.")
