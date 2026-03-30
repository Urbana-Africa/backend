from django.db import transaction
from django.utils import timezone
from apps.pay.models import Escrow, Wallet, WalletTransaction
from django.core.exceptions import ValidationError

@transaction.atomic
def release_escrow(escrow_id, admin_user=None):
    """
    Releases held escrow funds to the designer's wallet.
    Idempotent operation wrapped in a transaction block.
    """
    escrow = Escrow.objects.select_for_update().filter(id=escrow_id).first()
    if not escrow:
        raise ValidationError("Escrow not found")

    if escrow.status != "held":
        raise ValidationError(f"Escrow cannot be released. Current status: {escrow.status}")

    # Calculate designer share
    designer_share = escrow.amount - escrow.platform_commission

    designer_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=escrow.designer)

    # Move to available balance
    designer_wallet.available_balance += designer_share
    designer_wallet.save(update_fields=["available_balance"])

    # Log Wallet Transaction
    WalletTransaction.objects.create(
        wallet=designer_wallet,
        user=escrow.designer,
        transaction_type="escrow_release",
        status="completed",
        amount=designer_share,
        reference=f"ESCROW-{escrow.id}",
        related_payment=escrow.payment,
        related_order_id=escrow.order_item_id,
        description="Escrow release to wallet"
    )

    # Update Escrow entry
    escrow.status = "released"
    escrow.released_at = timezone.now()
    escrow.save(update_fields=["status", "released_at"])

    return escrow
