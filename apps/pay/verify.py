import requests
from decouple import config
from apps.pay.config import get_flutterwave_keys, get_paystack_keys, get_stripe_keys

ENV = config("ENV", default="dev")


# verify.py
import stripe


def verify_stripe_payment(intent_id):
    try:

        keys = get_stripe_keys()
        stripe.api_key = keys["secret_key"]
        intent = stripe.PaymentIntent.retrieve(intent_id)

        return {
            "status": "success",
            "data": {
                "id": intent.id,
                "status": intent.status,
                "amount": intent.amount,
                "currency": intent.currency,
                "metadata": intent.metadata,
            },
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }


# ==========================================
# ✅ VERIFY PAYSTACK TRANSACTION
# ==========================================
def verify_paystack_transaction(reference):
    """
    Verify a Paystack transaction by its reference.
    Returns dict:
      {"status": "success" | "error", "message": str, "data": dict | None}
    """
    keys = get_paystack_keys()
    secret_key = keys["secret_key"]
    url = f"https://api.paystack.co/transaction/verify/{reference}"
    headers = {"Authorization": f"Bearer {secret_key}"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        res_data = response.json()

        if response.status_code == 200 and res_data.get("status") is True:
            return {
                "status": "success",
                "message": "Verification successful",
                "data": res_data.get("data"),
            }

        return {
            "status": "error",
            "message": res_data.get("message", "Verification failed"),
            "data": res_data.get("data"),
        }

    except requests.RequestException as e:
        return {"status": "error", "message": str(e), "data": None}
    
    
# ==========================================
# ✅ VERIFY FLUTTERWAVE TRANSACTION
# ==========================================
def verify_flutterwave_transaction(transaction_id):
    """
    Verifies a Flutterwave transaction by its transaction ID.
    Returns dict:
      {"status": "success" | "error", "message": str, "data": dict | None}
    """
    keys = get_flutterwave_keys()
    url = f"{keys['base']}/transactions/{transaction_id}/verify"
    headers = {
        "Authorization": f"Bearer {keys['secret_key']}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        res_data = response.json()

        # Flutterwave returns: {"status": "success", "data": {...}}
        if (
            response.status_code == 200
            and res_data.get("status") == "success"
            and res_data.get("data", {}).get("status") == "successful"
        ):
            return {
                "status": "success",
                "message": "Verification successful",
                "data": res_data.get("data"),
            }

        return {
            "status": "error",
            "message": res_data.get("message", "Verification failed"),
            "data": res_data.get("data"),
        }

    except requests.RequestException as e:
        return {"status": "error", "message": str(e), "data": None}
