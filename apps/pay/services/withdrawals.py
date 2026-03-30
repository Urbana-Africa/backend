from decimal import Decimal
import requests
from django.db import transaction
from django.utils import timezone
from apps.pay.models import Withdrawal, Wallet, WalletTransaction
from django.core.exceptions import ValidationError
from apps.pay.config import get_flutterwave_keys

@transaction.atomic
def request_withdrawal(user, amount, bank_code, account_number, bank_name, account_name):
    """
    User requests a withdrawal. Validates balance and deducts it upfront.
    Returns the Withdrawal instance.
    """
    wallet = Wallet.objects.select_for_update().filter(user=user).first()
    if not wallet:
        raise ValidationError("Wallet not found")

    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValidationError("Withdrawal amount must be greater than zero")
        
    if wallet.available_balance < amount:
        raise ValidationError("Insufficient balance")

    # Lock funds by deducting from available balance immediately
    wallet.available_balance -= amount
    wallet.save(update_fields=["available_balance"])

    withdrawal_ref = f"WDR-{wallet.id}-{timezone.now().timestamp()}"

    withdrawal = Withdrawal.objects.create(
        wallet=wallet,
        user=user,
        amount=amount,
        status="pending",
        reference=withdrawal_ref,
        bank_name=bank_name,
        bank_code=bank_code,
        account_number=account_number,
        account_name=account_name
    )

    # Initial pending ledger record
    WalletTransaction.objects.create(
        wallet=wallet,
        user=user,
        transaction_type="withdrawal",
        status="pending",
        amount=amount,
        reference=withdrawal_ref,
        description="Withdrawal Request",
    )

    return withdrawal

@transaction.atomic
def approve_withdrawal(withdrawal_id, admin_user=None):
    """
    Admin approves a pending withdrawal request.
    """
    withdrawal = Withdrawal.objects.select_for_update().filter(id=withdrawal_id).first()
    if not withdrawal:
        raise ValidationError("Withdrawal not found")
        
    if withdrawal.status != "pending":
        raise ValidationError(f"Cannot approve from status: {withdrawal.status}")

    withdrawal.status = "approved"
    withdrawal.save(update_fields=["status"])
    return withdrawal

def process_withdrawal(withdrawal_id, admin_user=None):
    """
    Triggers the Flutterwave external payout.
    Transitions from approved -> processing.
    """
    with transaction.atomic():
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
        if withdrawal.status != "approved":
            raise ValidationError(f"Withdrawal must be approved before processing. Current: {withdrawal.status}")
        
        # Mark as processing early to prevent double sends
        withdrawal.status = "processing"
        withdrawal.save(update_fields=["status"])

        bank_code = withdrawal.bank_code
        account_number = withdrawal.account_number
        amount = withdrawal.amount
        reference = withdrawal.reference

    # Perform API Request outside transaction lock to avoid long DB locks
    headers = {
        "Authorization": f"Bearer {get_flutterwave_keys()['secret_key']}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "account_bank": bank_code,
        "account_number": account_number,
        "amount": float(amount),
        "narration": f"Payout for {reference}",
        "currency": "NGN",
        "reference": reference,
        "debit_currency": "NGN"
    }

    try:
        response = requests.post(
            "https://api.flutterwave.com/v3/transfers",
            json=payload,
            headers=headers,
            timeout=30
        )
        data = response.json()
        
        if data.get("status") == "success":
            fw_id = str(data.get("data", {}).get("id", ""))
            
            with transaction.atomic():
                w = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
                w.flutterwave_transfer_id = fw_id
                w.save(update_fields=["flutterwave_transfer_id"])
        else:
            # Revert to approved or fail the transaction
            fail_withdrawal(withdrawal_id, reason=data.get("message", "Flutterwave error"))
    except Exception as e:
        # We don't fail immediately on network errors, might require manual intervention
        pass

    return withdrawal

@transaction.atomic
def complete_withdrawal(withdrawal_id):
    """
    Completes a withdrawal (called via webhook or manual verification).
    """
    withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
    if withdrawal.status == "completed":
        return withdrawal

    if withdrawal.status not in ["processing", "approved"]:
        raise ValidationError(f"Cannot complete withdrawal from status: {withdrawal.status}")

    # Mark withdrawal object complete
    withdrawal.status = "completed"
    withdrawal.processed_at = timezone.now()
    withdrawal.save(update_fields=["status", "processed_at"])

    # Update ledger entry
    wallet_txn = WalletTransaction.objects.filter(reference=withdrawal.reference, transaction_type="withdrawal").first()
    if wallet_txn:
        wallet_txn.status = "completed"
        wallet_txn.completed_at = timezone.now()
        wallet_txn.save()
    else:
        # Fallback if no ledger was properly created
        WalletTransaction.objects.create(
            wallet=withdrawal.wallet,
            user=withdrawal.user,
            transaction_type="withdrawal",
            status="completed",
            amount=withdrawal.amount,
            reference=withdrawal.reference,
            description="Withdrawal completed",
            completed_at=timezone.now()
        )

    return withdrawal

@transaction.atomic
def fail_withdrawal(withdrawal_id, reason=""):
    """
    Fails a withdrawal, either rejected by admin or failed by Flutterwave.
    Refunds the available balance.
    """
    withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal_id)
    if withdrawal.status in ["failed", "rejected"]:
        return withdrawal
        
    if withdrawal.status == "completed":
        raise ValidationError("Cannot fail an already completed withdrawal")

    # Refund the designer's available wallet balance
    wallet = Wallet.objects.select_for_update().get(id=withdrawal.wallet_id)
    wallet.available_balance += withdrawal.amount
    wallet.save(update_fields=["available_balance"])

    # Mark transaction as failed
    wallet_txn = WalletTransaction.objects.filter(reference=withdrawal.reference, transaction_type="withdrawal").first()
    if wallet_txn:
        wallet_txn.status = "failed"
        wallet_txn.save(update_fields=["status"])

    withdrawal.status = "failed"
    withdrawal.save(update_fields=["status"])
    
    return withdrawal
