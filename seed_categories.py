import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'urbana.settings')
django.setup()

from apps.core.models import Category
from django.utils.text import slugify

SPECIALTIES = [
  { "value": "womenswear", "label": "Womenswear" },
  { "value": "menswear", "label": "Menswear" },
  { "value": "unisex", "label": "Unisex" },
  { "value": "bridal", "label": "Bridal" },
  { "value": "accessories", "label": "Accessories" },
  { "value": "footwear", "label": "Footwear" },
  { "value": "bags", "label": "Bags" },
  { "value": "jewellery", "label": "Jewellery" },
  { "value": "kidswear", "label": "Kidswear" },
  { "value": "luxury_couture", "label": "Luxury / Couture" },
  { "value": "streetwear", "label": "Streetwear" },
]

for item in SPECIALTIES:
    Category.objects.update_or_create(
        id=item["value"],
        defaults={
            "name": item["label"],
            "slug": slugify(item["label"])
        }
    )
print("Categories seeded successfully.")
