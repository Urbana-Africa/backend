from decimal import Decimal
import threading
import requests
from django.db import transaction
from django.utils import timezone
from apps.pay.models import Withdrawal, Wallet, WalletTransaction
from django.core.exceptions import ValidationError
from apps.pay.config import get_flutterwave_keys

FW_BASE_URL = "https://api.flutterwave.com/v3"


def _fw_headers():
    return {
        "Authorization": f"Bearer {get_flutterwave_keys()['secret_key']}",
        "Content-Type": "application/json",
    }


@transaction.atomic
def request_withdrawal(user, amount, payout_amount, payout_currency, bank_code, account_number, bank_name, account_name):
    """
    Deducts balance (in USD), creates a Withdrawal record, and immediately fires the
    Flutterwave transfer in a background thread.
    """
    wallet = Wallet.objects.select_for_update().filter(user=user).first()
    if not wallet:
        raise ValidationError("Wallet not found")

    amount = Decimal(str(amount))
    payout_amount = Decimal(str(payout_amount))
    
    if amount <= 0 or payout_amount <= 0:
        raise ValidationError("Withdrawal amounts must be greater than zero")

    if wallet.available_balance < amount:
        raise ValidationError("Insufficient balance")

    # Lock funds immediately
    wallet.available_balance -= amount
    wallet.save(update_fields=["available_balance"])

    withdrawal_ref = f"WDR-{wallet.id}-{int(timezone.now().timestamp())}"

    withdrawal = Withdrawal.objects.create(
        wallet=wallet,
        user=user,
        amount=amount, # USD debit amount
        payout_amount=payout_amount,
        payout_currency=payout_currency,
        status="pending",
        reference=withdrawal_ref,
        bank_name=bank_name,
        bank_code=bank_code,
        account_number=account_number,
        account_name=account_name,
    )

    WalletTransaction.objects.create(
        wallet=wallet,
        user=user,
        transaction_type="withdrawal",
        status="pending",
        amount=amount,
        reference=withdrawal_ref,
        description="Withdrawal Request",
    )

    # Fire FW transfer in background so the API response is immediate
    threading.Thread(
        target=_fire_flutterwave_transfer,
        args=(withdrawal.id,),
        daemon=True,
    ).start()

    return withdrawal


def _fire_flutterwave_transfer(withdrawal_id: str):
    """
    Background thread: calls Flutterwave and updates the Withdrawal status.
    """
    try:
        with transaction.atomic():
            withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
            if withdrawal.status != "pending":
                return  # already handled
            withdrawal.status = "processing"
            withdrawal.save(update_fields=["status"])

        payload = {
            "account_bank": withdrawal.bank_code,
            "account_number": withdrawal.account_number,
            "amount": float(withdrawal.payout_amount), # Use dynamic payout amount
            "narration": f"Urbana payout – {withdrawal.reference}",
            "currency": withdrawal.payout_currency, # Use dynamic payout currency
            "reference": withdrawal.reference,
            "debit_currency": "NGN", # Flutterwave debit account is NGN-based
        }

        resp = requests.post(
            f"{FW_BASE_URL}/transfers",
            json=payload,
            headers=_fw_headers(),
            timeout=30,
        )
        data = resp.json()

        if data.get("status") == "success":
            fw_id = str(data.get("data", {}).get("id", ""))
            fw_status = data.get("data", {}).get("status", "processing")
            with transaction.atomic():
                w = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
                w.flutterwave_transfer_id = fw_id
                # FW may immediately mark it NEW/PENDING/SUCCESSFUL
                if fw_status in ("SUCCESSFUL", "successful", "success"):
                    w.status = "completed"
                    w.processed_at = timezone.now()
                    _mark_wallet_txn_completed(w.reference)
                else:
                    w.status = "processing"
                w.save()
        else:
            # FW rejected — refund balance
            fail_withdrawal(withdrawal_id, reason=data.get("message", "Flutterwave error"))

    except Exception as e:
        print(f"[Wallet] FW transfer error for {withdrawal_id}: {e}")
        # Don't auto-fail on network errors — leave as 'processing' for manual check


def _get_user_currency(user) -> str:
    """Returns the user's preferred currency from their profile, defaulting to NGN."""
    try:
        profile = user.profile or {}
        return profile.get("currency", "NGN")
    except Exception:
        return "NGN"


def _mark_wallet_txn_completed(reference: str):
    txn = WalletTransaction.objects.filter(reference=reference, transaction_type="withdrawal").first()
    if txn:
        txn.status = "completed"
        txn.completed_at = timezone.now()
        txn.save(update_fields=["status", "completed_at"])


def check_withdrawal_status(withdrawal_id: str) -> dict:
    """
    Polls Flutterwave for live transfer status and syncs our DB record.
    Returns a dict with 'status' and 'flutterwave_status'.
    Called by the polling endpoint.
    """
    try:
        withdrawal = Withdrawal.objects.get(id=withdrawal_id)
    except Withdrawal.DoesNotExist:
        return {"status": "not_found"}

    if not withdrawal.flutterwave_transfer_id:
        return {"status": withdrawal.status, "flutterwave_status": None}

    try:
        resp = requests.get(
            f"{FW_BASE_URL}/transfers/{withdrawal.flutterwave_transfer_id}",
            headers=_fw_headers(),
            timeout=15,
        )
        data = resp.json()
        fw_status = data.get("data", {}).get("status", "")

        if fw_status in ("SUCCESSFUL", "successful"):
            if withdrawal.status != "completed":
                with transaction.atomic():
                    w = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
                    w.status = "completed"
                    w.processed_at = timezone.now()
                    w.save(update_fields=["status", "processed_at"])
                    _mark_wallet_txn_completed(w.reference)
            return {"status": "completed", "flutterwave_status": fw_status}

        elif fw_status in ("FAILED", "failed"):
            fail_withdrawal(withdrawal_id, reason="Flutterwave transfer failed")
            return {"status": "failed", "flutterwave_status": fw_status}

        return {"status": withdrawal.status, "flutterwave_status": fw_status}

    except Exception as e:
        print(f"[Wallet] Status check error for {withdrawal_id}: {e}")
        return {"status": withdrawal.status, "flutterwave_status": "unknown"}


@transaction.atomic
def approve_withdrawal(withdrawal_id, admin_user=None):
    """Legacy admin-approve step (kept for admin UI compatibility)."""
    withdrawal = Withdrawal.objects.select_for_update().filter(id=withdrawal_id).first()
    if not withdrawal:
        raise ValidationError("Withdrawal not found")
    if withdrawal.status != "pending":
        raise ValidationError(f"Cannot approve from status: {withdrawal.status}")
    withdrawal.status = "approved"
    withdrawal.save(update_fields=["status"])
    return withdrawal


def process_withdrawal(withdrawal_id, admin_user=None):
    """Legacy admin-process step (fires FW) — kept for admin UI compatibility."""
    with transaction.atomic():
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
        if withdrawal.status != "approved":
            raise ValidationError(f"Withdrawal must be approved before processing. Current: {withdrawal.status}")
        withdrawal.status = "processing"
        withdrawal.save(update_fields=["status"])

    threading.Thread(target=_fire_flutterwave_transfer, args=(withdrawal_id,), daemon=True).start()
    return withdrawal


@transaction.atomic
def complete_withdrawal(withdrawal_id):
    """Manually marks withdrawal completed (webhook / admin override)."""
    withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
    if withdrawal.status == "completed":
        return withdrawal
    if withdrawal.status not in ("processing", "approved", "pending"):
        raise ValidationError(f"Cannot complete withdrawal from status: {withdrawal.status}")

    withdrawal.status = "completed"
    withdrawal.processed_at = timezone.now()
    withdrawal.save(update_fields=["status", "processed_at"])
    _mark_wallet_txn_completed(withdrawal.reference)
    return withdrawal


@transaction.atomic
def fail_withdrawal(withdrawal_id, reason=""):
    """Fails a withdrawal and refunds the available balance."""
    withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
    if withdrawal.status in ("failed", "rejected", "completed"):
        return withdrawal

    wallet = Wallet.objects.select_for_update().get(id=withdrawal.wallet_id)
    wallet.available_balance += withdrawal.amount
    wallet.save(update_fields=["available_balance"])

    txn = WalletTransaction.objects.filter(
        reference=withdrawal.reference, transaction_type="withdrawal"
    ).first()
    if txn:
        txn.status = "failed"
        txn.save(update_fields=["status"])

    withdrawal.status = "failed"
    withdrawal.save(update_fields=["status"])
    return withdrawal
