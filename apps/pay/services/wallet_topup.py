"""
Customer wallet top-up helpers.

A top-up is funded through the normal invoice/payment pipeline:
  1. POST /pay/customer-wallet/deposit creates an Invoice with
     purpose == WALLET_TOPUP_PURPOSE.
  2. The customer pays it through the standard payment page
     (Flutterwave / Stripe), same as an order invoice.
  3. Whichever path confirms the payment (webhook, /pay/confirm/*,
     /pay/invoices/reverify, or complete_successful_payment) calls
     credit_wallet_topup(), which is idempotent per Payment.
"""

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Invoice.purpose marker — also shown to the customer on the payment page.
WALLET_TOPUP_PURPOSE = "Urbana Wallet Top-Up"


def is_wallet_topup_invoice(invoice) -> bool:
    return getattr(invoice, "purpose", None) == WALLET_TOPUP_PURPOSE


def credit_wallet_topup(invoice, payment) -> bool:
    """
    Credit the invoice amount to the owner's wallet.

    Idempotent: keyed on a completed deposit transaction for this payment,
    so webhook + confirm + reverify firing together can't double-credit.
    Returns True if a credit was applied.
    """
    from apps.pay.models import Wallet, WalletTransaction

    if not is_wallet_topup_invoice(invoice):
        return False

    with transaction.atomic():
        already_credited = WalletTransaction.objects.filter(
            related_payment=payment,
            transaction_type="deposit",
            status="completed",
        ).exists()
        if already_credited:
            return False

        wallet, _ = (
            Wallet.objects.select_for_update().get_or_create(
                user=invoice.user, defaults={"currency": "USD"}
            )
        )
        amount = Decimal(str(invoice.amount))
        wallet.available_balance = (wallet.available_balance or Decimal("0")) + amount
        wallet.save(update_fields=["available_balance"])

        WalletTransaction.objects.create(
            wallet=wallet,
            user=invoice.user,
            transaction_type="deposit",
            status="completed",
            amount=amount,
            reference=f"TOPUP-{invoice.id}",
            related_payment=payment,
            description="Wallet top-up",
            completed_at=timezone.now(),
        )

    logger.info("Wallet top-up credited: invoice=%s amount=%s", invoice.id, amount)
    return True
