from decimal import Decimal
import threading
import requests
from django.db import transaction
from django.utils import timezone
from apps.pay.models import Withdrawal, Wallet, WalletTransaction
from django.core.exceptions import ValidationError
from apps.pay.config import get_flutterwave_keys, get_stripe_keys, get_paystack_keys
import stripe

FW_BASE_URL = "https://api.flutterwave.com/v3"


def _fw_headers():
    return {
        "Authorization": f"Bearer {get_flutterwave_keys()['secret_key']}",
        "Content-Type": "application/json",
    }


def _ps_headers():
    return {
        "Authorization": f"Bearer {get_paystack_keys()['secret_key']}",
        "Content-Type": "application/json",
    }


@transaction.atomic
def request_withdrawal(user, amount, payout_amount, payout_currency, bank_code, account_number, bank_name, account_name, client_reference=None, account_type="flutterwave"):
    """
    Deducts balance (in USD), creates a Withdrawal record, and immediately fires the
    transfer in a background thread based on account_type (flutterwave, stripe, paystack).
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

    # Generate reference or use client-provided idempotency key
    withdrawal_ref = client_reference or f"WDR-{wallet.id}-{int(timezone.now().timestamp())}"

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

    # Route to correct processor based on account_type
    if account_type == "stripe" or payout_currency == "USD":
        threading.Thread(
            target=_fire_stripe_transfer,
            args=(withdrawal.id,),
            daemon=True,
        ).start()
    elif account_type == "paystack":
        threading.Thread(
            target=_fire_paystack_transfer,
            args=(withdrawal.id,),
            daemon=True,
        ).start()
    else:
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
            error_msg = data.get("message", "Flutterwave error")
            fail_withdrawal(withdrawal_id, reason=error_msg)

    except Exception as e:
        print(f"[Wallet] FW transfer error for {withdrawal_id}: {e}")
        # Don't auto-fail on network errors — leave as 'processing' for manual check

def _fire_paystack_transfer(withdrawal_id: str):
    """
    Background thread: calls Paystack and updates the Withdrawal status.
    Uses Paystack Transfers API to move funds to the recipient.
    """
    try:
        with transaction.atomic():
            withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
            if withdrawal.status != "pending":
                return
            withdrawal.status = "processing"
            withdrawal.save(update_fields=["status"])

        # Get or create Paystack transfer recipient
        from apps.pay.models import AccountDetail
        account_detail = AccountDetail.objects.filter(user=withdrawal.user).first()
        recipient_code = account_detail.recipient_code if account_detail else None

        # If no recipient code, create one
        if not recipient_code:
            create_resp = requests.post(
                "https://api.paystack.co/transferrecipient",
                headers=_ps_headers(),
                json={
                    "type": "nuban",
                    "name": withdrawal.account_name,
                    "account_number": withdrawal.account_number,
                    "bank_code": withdrawal.bank_code,
                    "currency": withdrawal.payout_currency or "NGN",
                },
                timeout=30,
            )
            create_data = create_resp.json()
            if create_data.get("status"):
                recipient_code = create_data["data"]["recipient_code"]
                if account_detail:
                    account_detail.recipient_code = recipient_code
                    account_detail.save(update_fields=["recipient_code", "updated_at"])
            else:
                error_msg = create_data.get("message", "Paystack recipient creation failed")
                fail_withdrawal(withdrawal_id, reason=error_msg)
                return

        # Initiate Paystack transfer
        transfer_resp = requests.post(
            "https://api.paystack.co/transfer",
            headers=_ps_headers(),
            json={
                "source": "balance",
                "reason": f"Urbana payout – {withdrawal.reference}",
                "amount": int(withdrawal.payout_amount * 100),  # Paystack uses kobo for NGN
                "recipient": recipient_code,
                "reference": withdrawal.reference,
            },
            timeout=30,
        )
        transfer_data = transfer_resp.json()

        if transfer_data.get("status"):
            transfer_code = transfer_data["data"]["transfer_code"]
            ps_status = transfer_data["data"]["status"]
            with transaction.atomic():
                w = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
                w.flutterwave_transfer_id = transfer_code  # re-use field for Paystack transfer code
                if ps_status in ("success", "Success"):
                    w.status = "completed"
                    w.processed_at = timezone.now()
                    w.save()
                    _mark_wallet_txn_completed(w.reference)
                else:
                    w.status = "processing"
                    w.save()
        else:
            error_msg = transfer_data.get("message", "Paystack transfer failed")
            fail_withdrawal(withdrawal_id, reason=error_msg)

    except Exception as e:
        print(f"[Wallet] Paystack transfer error for {withdrawal_id}: {e}")

def _fire_stripe_transfer(withdrawal_id: str):
    """
    Background thread: calls Stripe and updates the Withdrawal status.
    Uses Stripe Transfers to move funds to the connected account.
    """
    try:
        stripe.api_key = get_stripe_keys()['secret_key']
        with transaction.atomic():
            withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
            if withdrawal.status != "pending":
                return
            withdrawal.status = "processing"
            withdrawal.save(update_fields=["status"])

        # Create Stripe Transfer
        # account_number should be the connected Stripe account ID for the designer
        transfer = stripe.Transfer.create(
            amount=int(withdrawal.payout_amount * 100), # Stripe uses cents
            currency="usd",
            destination=withdrawal.account_number, # The connected account ID
            transfer_group=withdrawal.reference,
            description=f"Urbana payout – {withdrawal.reference}",
            metadata={"withdrawal_id": withdrawal_id, "reference": withdrawal.reference}
        )

        with transaction.atomic():
            w = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
            w.flutterwave_transfer_id = transfer.id # re-use this field or add a new one, we'll reuse it for now
            w.status = "completed"
            w.processed_at = timezone.now()
            w.save()
            _mark_wallet_txn_completed(w.reference)

    except stripe.error.StripeError as e:
        error_msg = str(e.user_message) if getattr(e, 'user_message', None) else str(e)
        fail_withdrawal(withdrawal_id, reason=error_msg)
    except Exception as e:
        print(f"[Wallet] Stripe transfer error for {withdrawal_id}: {e}")



def _get_user_currency(user) -> str:
    """Returns the user's preferred currency from their profile, defaulting to USD."""
    try:
        profile = user.profile or {}
        return profile.get("currency", "USD")
    except Exception:
        return "USD"


def _mark_wallet_txn_completed(reference: str):
    txn = WalletTransaction.objects.filter(reference=reference, transaction_type="withdrawal").first()
    if txn:
        txn.status = "completed"
        txn.completed_at = timezone.now()
        txn.save(update_fields=["status", "completed_at"])


def check_withdrawal_status(withdrawal_id: str) -> dict:
    """
    Polls the payment processor for live transfer status and syncs our DB record.
    Supports both Flutterwave and Stripe withdrawals.
    Returns a dict with 'status' and optional processor-specific status.
    Called by the polling endpoint.
    """
    try:
        withdrawal = Withdrawal.objects.get(id=withdrawal_id)
    except Withdrawal.DoesNotExist:
        return {"status": "not_found"}
    if withdrawal.status in ["completed", "failed", "rejected"]:
        return {"status": withdrawal.status}

    # ── Paystack withdrawal ─────────────────────────────────────
    if withdrawal.payout_currency != "USD" and withdrawal.flutterwave_transfer_id:
        # Check if this is actually a Paystack transfer by trying Paystack API first
        try:
            resp = requests.get(
                f"https://api.paystack.co/transfer/{withdrawal.flutterwave_transfer_id}",
                headers=_ps_headers(),
                timeout=15,
            )
            data = resp.json()
            if data.get("status"):
                ps_status = data.get("data", {}).get("status", "").lower()
                if ps_status in ("success", "successful"):
                    if withdrawal.status != "completed":
                        with transaction.atomic():
                            w = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
                            w.status = "completed"
                            w.processed_at = timezone.now()
                            w.save(update_fields=["status", "processed_at"])
                            _mark_wallet_txn_completed(w.reference)
                    return {"status": "completed", "paystack_status": ps_status}
                elif ps_status in ("failed", "reversed"):
                    error_msg = data.get("message") or "Paystack transfer failed"
                    fail_withdrawal(withdrawal_id, reason=error_msg)
                    return {"status": "failed", "paystack_status": ps_status}
                return {"status": withdrawal.status, "paystack_status": ps_status}
        except Exception:
            # If Paystack check fails, fall through to Flutterwave check
            pass

    # ── Stripe withdrawal ──────────────────────────────────────
    if withdrawal.payout_currency == "USD" and withdrawal.flutterwave_transfer_id:
        try:
            stripe.api_key = get_stripe_keys()["secret_key"]
            transfer = stripe.Transfer.retrieve(withdrawal.flutterwave_transfer_id)
            if transfer.reversed:
                fail_withdrawal(withdrawal_id, reason="Transfer reversed by Stripe")
                return {"status": "failed", "stripe_status": "reversed"}
            if transfer.status == "paid":
                if withdrawal.status != "completed":
                    with transaction.atomic():
                        w = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
                        w.status = "completed"
                        w.processed_at = timezone.now()
                        w.save(update_fields=["status", "processed_at"])
                        _mark_wallet_txn_completed(w.reference)
                return {"status": "completed", "stripe_status": transfer.status}
            elif transfer.status == "pending":
                return {"status": "processing", "stripe_status": transfer.status}
            else:
                return {"status": withdrawal.status, "stripe_status": transfer.status}
        except stripe.error.StripeError as e:
            print(f"[Wallet] Stripe status check error for {withdrawal_id}: {e}")
            return {"status": withdrawal.status, "stripe_status": "error"}
        except Exception as e:
            print(f"[Wallet] Stripe status check error for {withdrawal_id}: {e}")
            return {"status": withdrawal.status, "stripe_status": "unknown"}

    # ── Flutterwave withdrawal ───────────────────────────────────
    if not withdrawal.flutterwave_transfer_id:
        return {"status": withdrawal.status}

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
            error_msg = data.get("message") or data.get("data", {}).get("complete_message") or "Flutterwave transfer failed"
            fail_withdrawal(withdrawal_id, reason=error_msg)
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
    """Legacy admin-process step — kept for admin UI compatibility.
    Routes to the correct processor based on the user's account_type."""
    from apps.pay.models import AccountDetail
    with transaction.atomic():
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
        if withdrawal.status != "approved":
            raise ValidationError(f"Withdrawal must be approved before processing. Current: {withdrawal.status}")
        withdrawal.status = "processing"
        withdrawal.save(update_fields=["status"])

    account_detail = AccountDetail.objects.filter(user=withdrawal.user).first()
    account_type = account_detail.account_type if account_detail else "flutterwave"

    if account_type == "stripe":
        threading.Thread(target=_fire_stripe_transfer, args=(withdrawal_id,), daemon=True).start()
    elif account_type == "paystack":
        threading.Thread(target=_fire_paystack_transfer, args=(withdrawal_id,), daemon=True).start()
    else:
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
    if reason:
        withdrawal.failure_reason = reason
    withdrawal.save(update_fields=["status", "failure_reason"])
    return withdrawal
