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

  # Subcategories for Clothing
  { "value": "clothing-womenswear", "label": "Womenswear", "parent": "clothing" },
  { "value": "clothing-menswear", "label": "Menswear", "parent": "clothing" },
  { "value": "clothing-unisex", "label": "Unisex", "parent": "clothing" },
  { "value": "clothing-kidswear", "label": "Kidswear", "parent": "clothing" },
  { "value": "clothing-streetwear", "label": "Streetwear", "parent": "clothing" },

  # Subcategories for Shoes
  { "value": "shoes-womenswear", "label": "Womenswear", "parent": "shoes" },
  { "value": "shoes-menswear", "label": "Menswear", "parent": "shoes" },
  { "value": "shoes-unisex", "label": "Unisex", "parent": "shoes" },
  { "value": "shoes-kidswear", "label": "Kidswear", "parent": "shoes" },

  # Subcategories for Accessories
  { "value": "accessories-womenswear", "label": "Womenswear", "parent": "accessories" },
  { "value": "accessories-menswear", "label": "Menswear", "parent": "accessories" },
  { "value": "accessories-unisex", "label": "Unisex", "parent": "accessories" },

  # Subcategories for Bags
  { "value": "bags-womenswear", "label": "Womenswear", "parent": "bags" },
  { "value": "bags-menswear", "label": "Menswear", "parent": "bags" },
  { "value": "bags-unisex", "label": "Unisex", "parent": "bags" },
]

# Clear existing categories to keep it clean
Category.objects.all().delete()

for item in CATEGORIES:
    Category.objects.create(
        id=item["value"],
        name=item["label"],
        slug=slugify(item["value"]),
        parent_id=item.get("parent")
    )

print("Categories seeded successfully.")
