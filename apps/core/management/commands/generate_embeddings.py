"""
Management command to generate and update ProductEmbedding records
for semantic vector search in AI mode.

Usage:
    python manage.py generate_embeddings          # all products
    python manage.py generate_embeddings --id <product_id>
    python manage.py generate_embeddings --clear  # wipe all embeddings
"""
import os
import sys
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Generate text embeddings for products"

    def add_arguments(self, parser):
        parser.add_argument("--id", type=str, help="Process a single product ID")
        parser.add_argument("--clear", action="store_true", help="Clear all existing embeddings")

    def handle(self, *args, **options):
        single_id = options.get("id")
        clear = options.get("clear")

        # Lazy imports so Django setup is done first
        from apps.core.models import ProductEmbedding, Product

        if clear:
            deleted, _ = ProductEmbedding.objects.all().delete()
            self.stdout.write(self.style.SUCCESS(f"Cleared {deleted} embedding records."))
            return

        if single_id:
            products = Product.objects.filter(id=single_id)
        else:
            products = Product.objects.all()

        total = products.count()
        if not total:
            self.stdout.write(self.style.WARNING("No products found."))
            return

        self.stdout.write(f"Processing {total} product(s)...")

        created_count = 0
        updated_count = 0

        for product in products:
            text = self._build_text(product)
            emb, created = ProductEmbedding.objects.update_or_create(
                product=product,
                defaults={
                    "embedding_text": text,
                    "dimensions": 0,
                    "embedding": [],
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created_count}, updated {updated_count}."
            )
        )

    def _build_text(self, product):
        parts = [
            product.name or "",
            product.description or "",
            product.category.name if product.category else "",
            product.designer.brand_name if product.designer else "",
            " ".join([c.name for c in product.colors.all()]) if hasattr(product, "colors") else "",
            " ".join([t.name for t in product.tags.all()]) if hasattr(product, "tags") else "",
        ]
        return " ".join([p for p in parts if p]).strip()
