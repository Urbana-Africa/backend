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