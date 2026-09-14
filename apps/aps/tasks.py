# apps/pay/tasks.py
import logging
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone
from datetime import timedelta
from django.db import transaction

from apps.customers.models import OrderItem
from apps.pay.models import Escrow, Wallet, WalletTransaction

logger = logging.getLogger(__name__)


# ============================================================
# 1. CREATE ESCROW WHEN PAYMENT IS SUCCESSFUL
# ============================================================

@transaction.atomic
def create_escrows_for_successful_payments():
    """
    Sweep job: creates escrows for any order items whose payment succeeded
    but that don't yet have an escrow record.

    This is a FALLBACK for items missed by the synchronous
    ``complete_successful_payment`` path (e.g. if the server crashed between
    payment confirmation and escrow creation). It must use the SAME pricing
    logic as ``checkout.complete_successful_payment`` so commission is
    consistent regardless of which path created the escrow.
    """
    order_items = OrderItem.objects.select_related(
        "designer",
        "order__invoice__payment",
        "order__customer__user",
    ).filter(
        escrow__isnull=True,
        order__invoice__payment__status="success",
        order__invoice__payment__is_paid=True,
        order__invoice__payment__is_deleted=False,
    )

    for item in order_items:
        payment = item.order.invoice.payment

        # Use the same dynamic pricing split as checkout.py
        props = item.properties or {}
        if 'base_price' in props:
            qty = Decimal(str(item.quantity))
            base_price = Decimal(str(props['base_price']))
            platform_margin = Decimal(str(props.get('platform_margin', 0)))
            duties_buffer = Decimal(str(props.get('duties_buffer', 0)))

            designer_base = (base_price - platform_margin) * qty
            commission = (platform_margin + duties_buffer) * qty
            escrow_amount = designer_base + commission
        else:
            # Legacy fallback: 10% of sub_total
            escrow_amount = item.sub_total
            commission = calculate_platform_commission(item.sub_total)

        escrow = Escrow.objects.create(
            payment=payment,
            customer=item.order.customer.user,
            designer=item.designer,
            amount=escrow_amount,
            platform_commission=commission,
            status="held",
        )
        item.escrow = escrow
        item.save(update_fields=["escrow"])



def calculate_platform_commission(amount):
    commission_rate = Decimal("0.10")
    commission = amount * commission_rate

    return commission.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )


# ============================================================
# 2. RELEASE ESCROW WHEN CUSTOMER MARKS RECEIVED
# ============================================================

@transaction.atomic
def release_escrows_for_received_items():
    """
    Release escrow funds when customer_status == 'received'
    and escrow is still held.

    Uses select_for_update on both escrow and wallet to prevent race
    conditions where two concurrent jobs could credit the same escrow twice.
    """

    eligible_items = OrderItem.objects.select_related("escrow").filter(
        customer_status="received",
        escrow__status="held"
    )

    for item in eligible_items:
        escrow = item.escrow

        if not escrow:
            continue

        # Lock the escrow row to prevent double-release by concurrent jobs
        escrow = Escrow.objects.select_for_update().filter(id=escrow.id).first()
        if not escrow or escrow.status != "held":
            continue

        # Lock the wallet row to prevent concurrent balance corruption
        designer_wallet, _ = Wallet.objects.select_for_update().get_or_create(
            user=escrow.designer
        )

        designer_share = escrow.amount - escrow.platform_commission

        # Credit designer wallet
        designer_wallet.available_balance += designer_share
        designer_wallet.save(update_fields=["available_balance"])

        # Create ledger entry
        WalletTransaction.objects.create(
            wallet=designer_wallet,
            user=escrow.designer,
            transaction_type="escrow_release",
            status="completed",
            amount=designer_share,
            reference=f"ESCROW-{escrow.id}",
            related_payment=escrow.payment,
            related_order_id=escrow.order_item.item_id,
            completed_at=timezone.now()
        )

        # Update escrow
        escrow.status = "released"
        escrow.released_at = timezone.now()
        escrow.save(update_fields=["status", "released_at"])



@transaction.atomic
def auto_release_escrows_after_24hrs():
    """
    Automatically release escrow 24 hours after item is delivered
    if customer has not confirmed.

    Uses select_for_update on escrow and wallet to prevent race conditions.
    """

    threshold = timezone.now() - timedelta(hours=24)

    eligible_items = OrderItem.objects.select_related(
        "escrow",
        "escrow__designer",
    ).filter(
        status="delivered",
        delivered_at__lte=threshold,
        escrow__status="held",
    )

    for item in eligible_items:
        escrow = item.escrow

        if not escrow:
            continue

        # Lock escrow to prevent double-release
        escrow = Escrow.objects.select_for_update().filter(id=escrow.id).first()
        if not escrow or escrow.status != "held":
            continue

        # Lock wallet to prevent concurrent balance corruption
        designer_wallet, _ = Wallet.objects.select_for_update().get_or_create(
            user=escrow.designer
        )

        designer_share = escrow.amount - escrow.platform_commission

        # Credit wallet
        designer_wallet.available_balance += designer_share
        designer_wallet.save(update_fields=["available_balance"])

        # Ledger entry
        WalletTransaction.objects.create(
            wallet=designer_wallet,
            user=escrow.designer,
            transaction_type="escrow_auto_release",
            status="completed",
            amount=designer_share,
            reference=f"AUTO-ESCROW-{escrow.id}",
            related_payment=escrow.payment,
            related_order_id=escrow.order_item.item_id,
            completed_at=timezone.now()
        )

        # Update escrow
        escrow.status = "released"
        escrow.is_auto_released = True
        escrow.released_at = timezone.now()
        escrow.save(update_fields=[
            "status",
            "is_auto_released",
            "released_at",
        ])


# ============================================================
# 3. SCHEDULED EMAIL JOBS
# ============================================================

def send_delayed_designer_emails():
    """Send product-upload reminders to designers who have completed their
    profile but haven't yet uploaded the 5 products required for activation.

    Reminder cadence (all gated on profile completion + <5 products):
      • 24h  after signup  → upload_reminder  (existing email)
      • 72h  after signup  → storefront reminder (existing email)
      •  7d  after signup  → final reminder    (existing storefront email,
                                               re-sent once as a last nudge)
    """
    from apps.authentication.models import User
    from apps.designers.models import Designer
    from apps.core.models import Product
    from apps.utils.notifications import (
        send_designer_product_upload_reminder,
        send_designer_storefront_reminder,
    )

    now = timezone.now()

    # A designer is considered to have "completed their profile" once they
    # have submitted it for review (status moved out of the default pending
    # state OR a welcome email has been sent, which happens on first
    # submission). We use `welcome_email_sent_at` as the reliable signal
    # because it is set exactly once, at first profile submission.
    def profile_completed(designer):
        return designer.welcome_email_sent_at is not None

    def has_fewer_than_five_products(designer):
        # Count products the designer actually uploaded (via Product.user),
        # not just DesignerProduct join records. Products uploaded before the
        # join-record fix have no DesignerProduct link and would be invisible
        # to designer.products.count().
        from apps.core.models import Product as ProductModel
        return ProductModel.objects.filter(user=designer.user).count() < 5

    # ── 24-hour reminder ──────────────────────────────────────────────
    # Window: 24h–48h after signup (wide enough that a 5-minute scheduler
    # will always catch it). Only sent once per designer.
    day_ago = now - timedelta(hours=24)
    two_days_ago = now - timedelta(hours=48)
    designers_24h = Designer.objects.filter(
        created_at__lte=day_ago,
        created_at__gte=two_days_ago,
        upload_reminder_sent_at__isnull=True,
    )
    for designer in designers_24h:
        if not profile_completed(designer):
            continue
        if not has_fewer_than_five_products(designer):
            continue
        try:
            send_designer_product_upload_reminder(designer.user)
            designer.upload_reminder_sent_at = now
            designer.save(update_fields=["upload_reminder_sent_at"])
        except Exception as e:
            logger.error("[SCHEDULED] Designer 24h reminder failed: %s", e)

    # ── 72-hour reminder ───────────────────────────────────────────────
    # Window: 72h–96h after signup. Only sent once per designer.
    three_days_ago = now - timedelta(hours=72)
    four_days_ago = now - timedelta(hours=96)
    designers_72h = Designer.objects.filter(
        created_at__lte=three_days_ago,
        created_at__gte=four_days_ago,
        storefront_reminder_sent_at__isnull=True,
    )
    for designer in designers_72h:
        if not profile_completed(designer):
            continue
        if not has_fewer_than_five_products(designer):
            continue
        try:
            send_designer_storefront_reminder(designer.user)
            designer.storefront_reminder_sent_at = now
            designer.save(update_fields=["storefront_reminder_sent_at"])
        except Exception as e:
            logger.error("[SCHEDULED] Designer storefront reminder failed: %s", e)

    # ── 7-day final reminder ───────────────────────────────────────────
    # Window: 7d–9d after signup. A single last-chance nudge for designers
    # who still haven't reached 5 products.
    week_ago = now - timedelta(days=7)
    nine_days_ago = now - timedelta(days=9)
    designers_7d = Designer.objects.filter(
        created_at__lte=week_ago,
        created_at__gte=nine_days_ago,
        final_reminder_sent_at__isnull=True,
    )
    for designer in designers_7d:
        if not profile_completed(designer):
            continue
        if not has_fewer_than_five_products(designer):
            continue
        try:
            send_designer_storefront_reminder(designer.user)
            designer.final_reminder_sent_at = now
            designer.save(update_fields=["final_reminder_sent_at"])
        except Exception as e:
            logger.error("[SCHEDULED] Designer 7-day reminder failed: %s", e)


def send_delayed_customer_emails():
    """Send browse reminder and review-request emails to customers."""
    from apps.authentication.models import User
    from apps.customers.models import Customer, Order
    from apps.utils.notifications import (
        send_customer_browse_reminder,
        send_customer_review_request,
    )

    now = timezone.now()

    # 24-48h browse reminder: customers with no orders
    day_ago = now - timedelta(hours=36)
    customers_no_order = Customer.objects.filter(
        created_at__lte=day_ago,
        created_at__gte=day_ago - timedelta(hours=2),
        browse_reminder_sent_at__isnull=True,
    )
    for customer in customers_no_order:
        has_order = Order.objects.filter(customer=customer).exists()
        if not has_order:
            try:
                send_customer_browse_reminder(customer.user)
                customer.browse_reminder_sent_at = now
                customer.save(update_fields=["browse_reminder_sent_at"])
            except Exception as e:
                logger.error("[SCHEDULED] Customer browse reminder failed: %s", e)

    # 2-3 days after delivery: review request
    two_days_after = now - timedelta(days=2)
    three_days_after = now - timedelta(days=3)
    from apps.customers.models import OrderItem
    delivered_items = OrderItem.objects.filter(
        status="delivered",
        delivered_at__lte=two_days_after,
        delivered_at__gte=three_days_after,
        review_request_sent_at__isnull=True,
    )
    for item in delivered_items:
        try:
            send_customer_review_request(item)
            item.review_request_sent_at = now
            item.save(update_fields=["review_request_sent_at"])
        except Exception as e:
            logger.error("[SCHEDULED] Customer review request failed: %s", e)


def process_scrape_jobs():
    """Pick up queued ScrapeJobs and run them through the provider engine."""
    from apps.marketing.models import ScrapeJob
    from apps.marketing.scraping.engine import run_scrape_engine

    jobs = ScrapeJob.objects.filter(status='queued').order_by('created_at')[:5]
    for job in jobs:
        try:
            run_scrape_engine(job.id)
        except Exception as e:
            job.refresh_from_db()
            job.status = 'failed'
            job.error_message = str(e)
            job.completed_at = timezone.now()
            job.save(update_fields=['status', 'error_message', 'completed_at'])
            print(f"[SCHEDULED] Scrape job {job.id} failed: {e}")