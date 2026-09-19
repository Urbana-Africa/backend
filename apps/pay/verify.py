import requests
from decouple import config
from apps.pay.config import get_flutterwave_keys, get_stripe_keys

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


def verify_flutterwave_by_reference(tx_ref):
    """
    Verifies a Flutterwave transaction by our internal tx_ref
    (PaymentAttempt.reference) instead of Flutterwave's numeric id —
    needed when the client never saw the transaction id (e.g. the user
    closed the payment modal before the callback fired).
    """
    keys = get_flutterwave_keys()
    url = f"{keys['base']}/transactions/verify_by_reference"
    headers = {
        "Authorization": f"Bearer {keys['secret_key']}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.get(url, params={"tx_ref": tx_ref}, headers=headers, timeout=10)
        res_data = response.json()

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
