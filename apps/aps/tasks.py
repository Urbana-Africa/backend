# apps/pay/tasks.py
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone
from datetime import timedelta
from django.db import transaction

from apps.customers.models import OrderItem
from apps.pay.models import Escrow, Wallet, WalletTransaction


# ============================================================
# 1. CREATE ESCROW WHEN PAYMENT IS SUCCESSFUL
# ============================================================

@transaction.atomic
def create_escrows_for_successful_payments():

    order_items = OrderItem.objects.select_related(
        "designer",
        "order__invoice__payment",
    ).filter(
        escrow__isnull=True,
        order__invoice__payment__status="success",
        order__invoice__payment__is_paid=True,
        order__invoice__payment__is_deleted=False,
    )

    for item in order_items:
        payment = item.order.invoice.payment

        escrow = Escrow.objects.create(
            payment=payment,
            customer=payment.user,
            designer=item.designer,
            amount=item.sub_total,
            platform_commission=calculate_platform_commission(item.sub_total),
            status="held",
        )
        item.escrow = escrow
        item.save(update_fields=["escrow"])



def calculate_platform_commission(amount):
    commission_rate = Decimal("0.10")  # NOT 0.10
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
    """

    eligible_items = OrderItem.objects.select_related("escrow").filter(
        customer_status="received",
        escrow__status="held"
    )

    for item in eligible_items:
        escrow = item.escrow

        if not escrow:
            continue

        designer_wallet, _ = Wallet.objects.get_or_create(
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

        designer_wallet, _ = Wallet.objects.get_or_create(
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
    """Send 24h product-upload and 48-72h storefront reminders to designers."""
    from apps.authentication.models import User
    from apps.designers.models import Designer
    from apps.core.models import Product
    from apps.utils.notifications import (
        send_designer_product_upload_reminder,
        send_designer_storefront_reminder,
    )

    now = timezone.now()

    # 24-hour reminder: designers with no published products
    day_ago = now - timedelta(hours=24)
    designers_24h = Designer.objects.filter(
        created_at__lte=day_ago,
        created_at__gte=day_ago - timedelta(hours=2),
        upload_reminder_sent_at__isnull=True,
    )
    for designer in designers_24h:
        published = Product.objects.filter(
            user=designer.user, is_published=True, is_admin_published=True
        ).exists()
        if not published:
            try:
                send_designer_product_upload_reminder(designer.user)
                designer.upload_reminder_sent_at = now
                designer.save(update_fields=["upload_reminder_sent_at"])
            except Exception as e:
                print(f"[SCHEDULED] Designer 24h reminder failed: {e}")

    # 48-72 hour reminder: designers still with no products
    two_days_ago = now - timedelta(hours=48)
    three_days_ago = now - timedelta(hours=72)
    designers_48h = Designer.objects.filter(
        created_at__lte=two_days_ago,
        created_at__gte=three_days_ago,
        storefront_reminder_sent_at__isnull=True,
    )
    for designer in designers_48h:
        published = Product.objects.filter(
            user=designer.user, is_published=True, is_admin_published=True
        ).exists()
        if not published:
            try:
                send_designer_storefront_reminder(designer.user)
                designer.storefront_reminder_sent_at = now
                designer.save(update_fields=["storefront_reminder_sent_at"])
            except Exception as e:
                print(f"[SCHEDULED] Designer storefront reminder failed: {e}")


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
                print(f"[SCHEDULED] Customer browse reminder failed: {e}")

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
            print(f"[SCHEDULED] Customer review request failed: {e}")