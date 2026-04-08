from django.db import transaction
from django.utils import timezone
from apps.pay.models import Escrow, Wallet, WalletTransaction
from django.core.exceptions import ValidationError

ESCROW_STATUS_CHOICES = ("held", "released", "refunded", "disputed")


@transaction.atomic
def release_escrow(escrow_id, admin_user=None):
    """
    Releases held escrow funds to the designer's wallet.
    Called when customer marks item received / auto-release timer fires.
    """
    escrow = Escrow.objects.select_for_update().select_related('order_item').filter(id=escrow_id).first()
    if not escrow:
        raise ValidationError("Escrow not found")

    if escrow.status not in ("held", "disputed"):
        raise ValidationError(f"Escrow cannot be released. Current status: {escrow.status}")

    designer_share = escrow.amount - escrow.platform_commission
    designer_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=escrow.designer)

    designer_wallet.available_balance += designer_share
    designer_wallet.save(update_fields=["available_balance"])

    WalletTransaction.objects.create(
        wallet=designer_wallet,
        user=escrow.designer,
        transaction_type="escrow_release",
        status="completed",
        amount=designer_share,
        reference=f"ESCROW-{escrow.id}",
        related_payment=escrow.payment,
        related_order_id=escrow.order_item.item_id,
        description="Escrow release to wallet",
    )

    escrow.status = "released"
    escrow.released_at = timezone.now()
    escrow.save(update_fields=["status", "released_at"])
    return escrow


@transaction.atomic
def refund_escrow_to_customer(escrow_id):
    """
    Refunds held/disputed escrow funds to the CUSTOMER's wallet.
    Called when admin approves a return request.
    Full amount (including platform commission) is credited to the customer.
    """
    escrow = Escrow.objects.select_for_update().select_related('order_item').filter(id=escrow_id).first()
    if not escrow:
        raise ValidationError("Escrow not found")

    if escrow.status not in ("held", "disputed"):
        raise ValidationError(f"Escrow cannot be refunded. Current status: {escrow.status}")

    customer_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=escrow.customer)

    # Full amount back to customer — platform absorbs the commission on returns
    customer_wallet.available_balance += escrow.amount
    customer_wallet.save(update_fields=["available_balance"])

    WalletTransaction.objects.create(
        wallet=customer_wallet,
        user=escrow.customer,
        transaction_type="refund",
        status="completed",
        amount=escrow.amount,
        reference=f"REFUND-ESCROW-{escrow.id}",
        related_payment=escrow.payment,
        related_order_id=escrow.order_item.item_id,
        description="Return approved — refund credited to wallet",
    )

    escrow.status = "refunded"
    escrow.released_at = timezone.now()
    escrow.save(update_fields=["status", "released_at"])
    return escrow


@transaction.atomic
def hold_escrow_for_return(escrow_id):
    """
    Transitions escrow to 'disputed' when a return is initiated.
    Funds remain locked (not released to designer, not refunded to customer)
    until admin resolves the return.
    """
    escrow = Escrow.objects.select_for_update().filter(id=escrow_id).first()
    if not escrow:
        raise ValidationError("Escrow not found")

    if escrow.status != "held":
        # Already disputed or resolved — idempotent
        return escrow

    escrow.status = "disputed"
    escrow.save(update_fields=["status"])
    return escrow
