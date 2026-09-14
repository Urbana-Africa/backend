"""
Backfill missing DesignerProduct links for products uploaded by designers
before the upload-view fix was deployed.

Before the fix, `DesignerProductUploadViewSet.create` saved a Product but
never created the DesignerProduct join record. As a result those products
were invisible to `designer.products.count()` and designers could never
satisfy the 5-product activation gate — even after uploading 5+ products.

Usage:
    python manage.py backfill_designer_products
    python manage.py backfill_designer_products --dry-run
"""
from django.core.management.base import BaseCommand
from apps.core.models import Product
from apps.designers.models import Designer, DesignerProduct


class Command(BaseCommand):
    help = (
        "Create missing DesignerProduct join records for products that "
        "belong to a designer user but were never linked via DesignerProduct."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be backfilled without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Products whose user has a designer profile but which have no
        # DesignerProduct link yet.
        orphaned_products = (
            Product.objects
            .filter(designer_product__isnull=True, user__designer_profile__isnull=False)
            .select_related("user__designer_profile")
        )

        total = orphaned_products.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No orphaned products found. Nothing to backfill."))
            return

        self.stdout.write(f"Found {total} orphaned product(s) to backfill.")

        created = 0
        skipped = 0
        for product in orphaned_products:
            designer = product.user.designer_profile
            if dry_run:
                self.stdout.write(
                    f"  [dry-run] would link Product#{product.id} "
                    f"({product.name!r}) → Designer#{designer.id} "
                    f"({designer.user.email})"
                )
                continue

            _, created_flag = DesignerProduct.objects.get_or_create(
                designer=designer,
                product=product,
                defaults={"stock": product.stock or 0},
            )
            if created_flag:
                created += 1
                self.stdout.write(
                    f"  linked Product#{product.id} ({product.name!r}) "
                    f"→ Designer#{designer.id} ({designer.user.email})"
                )
            else:
                skipped += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run complete — {total} link(s) would have been created."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Backfill complete — {created} created, {skipped} already existed."
            ))
