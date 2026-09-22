"""
Flutterwave OTP service — phone/email verification via Flutterwave's OTP API.

Flutterwave generates and stores the OTP server-side; we keep the returned
`reference` and pass it to the validate endpoint.

Docs: https://developer.flutterwave.com/v3.0/docs/otps

  - POST {base}/otps                      → generate + dispatch an OTP
  - POST {base}/otps/{reference}/validate → verify the code

Note: delivery is billed per channel from the merchant's NGN wallet
(email ₦1, SMS ₦4, WhatsApp ₦15) — the wallet must be funded or the
create call returns a 400 "Insufficient funds in merchant's NGN wallet".
"""

import logging

import requests
from django.conf import settings

from apps.pay.config import get_flutterwave_keys

logger = logging.getLogger(__name__)

_CHANNEL_TO_MEDIUM = {
    "whatsapp": "whatsapp",
    "sms": "sms",
    "email": "email",
}

# send_verification_otp error codes → consumed by the view to build
# professional, user-safe messages (raw provider errors stay in logs).
SEND_ERROR_MESSAGES = {
    "network": "We couldn't reach the verification service. Please check your connection and try again.",
    "invalid_phone": "We couldn't send a code to this number. Please double-check it and try again.",
    "invalid_email": "We couldn't send a code to this email address. Please double-check it and try again.",
    "provider_unavailable": "Phone verification is temporarily unavailable. Please try again shortly.",
    "provider_error": "We couldn't send the verification code right now. Please try again.",
}

# check_verification_otp reasons → user-facing messages.
VERIFY_ERROR_MESSAGES = {
    "invalid": "The code you entered is incorrect. Please try again.",
    "expired": "This code has expired. Please request a new one.",
    "no_reference": "Your verification session has expired. Please request a new code.",
    "provider_error": "We couldn't verify your code right now. Please try again in a moment.",
}


def _clean_phone(phone: str) -> str:
    """Flutterwave expects the number in international format WITHOUT the leading +."""
    phone = phone.strip().replace(" ", "")
    if phone.startswith("+"):
        phone = phone[1:]
    return phone


def _get_sender() -> str:
    return getattr(settings, "FLUTTERWAVE_OTP_SENDER", "Urbana") or "Urbana"


def is_configured() -> bool:
    """True when Flutterwave keys are available for this environment."""
    try:
        return bool(get_flutterwave_keys()["secret_key"])
    except Exception:
        return False


def _create_otp(to: str, medium: str, customer_name: str, customer_email: str,
                length: int, expiry: int) -> tuple:
    """Returns (http_status, response_json)."""
    keys = get_flutterwave_keys()
    payload = {
        "length": length,
        "customer": {
            # Flutterwave caps customer.name at 10 characters.
            "name": (customer_name or "Urbana")[:10],
            "email": customer_email or "",
            "phone": to,
        },
        "sender": _get_sender(),
        "send": True,
        "medium": [medium],
        "expiry": expiry,
    }
    resp = requests.post(
        f"{keys['base']}/otps",
        json=payload,
        headers={
            "Authorization": f"Bearer {keys['secret_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=15,
    )
    return resp.status_code, resp.json()


def _classify_send_error(http_status: int, data: dict) -> str:
    """Map a failed create-otp response to a stable error code."""
    msg = (data.get("message") or "").lower()
    if "insufficient" in msg or "wallet" in msg or "fund" in msg:
        return "provider_unavailable"  # merchant billing issue — never leak to users
    if "phone" in msg:
        return "invalid_phone"
    if "email" in msg:
        return "invalid_email"
    if http_status >= 500:
        return "provider_unavailable"
    return "provider_error"


def send_verification_otp(
    to_phone: str,
    channel: str = "whatsapp",
    customer_name: str = "",
    customer_email: str = "",
    length: int = 6,
    expiry: int = 10,
) -> dict:
    """
    Generates an OTP via Flutterwave and dispatches it on `channel`.

    Args:
        to_phone:       international format, e.g. "+2348123456789"
        channel:        "whatsapp" (default), "sms" or "email"
        customer_name:  shown in the customer object (max 10 chars used)
        customer_email: customer's email address
        length:         OTP length (Flutterwave supports 5-7)
        expiry:         validity window in minutes

    Returns:
        dict with keys:
          - success    (bool)
          - method     (str, e.g. "flw_whatsapp")
          - reference  (str, needed for verification — store it!)
          - expiry     (str, ISO timestamp — store it for local expiry checks)
          - error_code (str, key of SEND_ERROR_MESSAGES — on failure only)
    """
    to = _clean_phone(to_phone)
    medium = _CHANNEL_TO_MEDIUM.get((channel or "whatsapp").lower(), "whatsapp")

    try:
        http_status, data = _create_otp(to, medium, customer_name, customer_email, length, expiry)
    except Exception as e:
        logger.error(f"[FLW OTP] Create request failed for {to}: {e}")
        return {"success": False, "method": f"flw_{medium}", "error_code": "network"}

    logger.info(f"[FLW OTP] Create for {to} via {medium}: {data}")

    entries = data.get("data") or []
    if data.get("status") == "success" and entries:
        entry = entries[0]
        # The raw OTP is echoed back by the API — never return it to clients,
        # but log it so devs can test without reading SMS/WhatsApp.
        logger.info(f"[FLW OTP] Code for {to}: {entry.get('otp')}")
        return {
            "success": True,
            "method": f"flw_{entry.get('medium', medium)}",
            "reference": entry.get("reference"),
            "expiry": entry.get("expiry"),
            "status": data.get("status"),
        }

    send_error = _classify_send_error(http_status, data)

    # Fallback: if WhatsApp fails, retry with SMS
    if medium == "whatsapp":
        logger.warning(f"[FLW OTP] WhatsApp failed for {to}; retrying via SMS.")
        try:
            http_status, data = _create_otp(to, "sms", customer_name, customer_email, length, expiry)
        except Exception as e:
            logger.error(f"[FLW OTP] SMS fallback failed for {to}: {e}")
            return {"success": False, "method": "flw_sms", "error_code": "network"}

        logger.info(f"[FLW OTP] SMS fallback to {to}: {data}")
        entries = data.get("data") or []
        if data.get("status") == "success" and entries:
            entry = entries[0]
            logger.info(f"[FLW OTP] Code for {to}: {entry.get('otp')}")
            return {
                "success": True,
                "method": "flw_sms",
                "reference": entry.get("reference"),
                "expiry": entry.get("expiry"),
                "status": data.get("status"),
            }
        send_error = _classify_send_error(http_status, data)

    return {
        "success": False,
        "method": f"flw_{medium}",
        "error_code": send_error,
        "error": data.get("message", "Unknown Flutterwave OTP error"),
        "status": data.get("status"),
    }


def check_verification_otp(reference: str, code: str) -> dict:
    """
    Validates the OTP against Flutterwave's validate endpoint.

    Args:
        reference: the reference returned by send_verification_otp (REQUIRED).
        code:      the code the user entered.

    Returns:
        {"verified": bool, "reason": "ok"|"invalid"|"expired"|"no_reference"|"provider_error"}
    """
    if not reference:
        logger.error("[FLW OTP] Cannot verify — missing OTP reference")
        return {"verified": False, "reason": "no_reference"}

    try:
        keys = get_flutterwave_keys()
        resp = requests.post(
            f"{keys['base']}/otps/{reference}/validate",
            json={"otp": code},
            headers={
                "Authorization": f"Bearer {keys['secret_key']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15,
        )
        data = resp.json()
    except Exception as e:
        logger.error(f"[FLW OTP] Validate request failed (ref={reference}): {e}")
        return {"verified": False, "reason": "provider_error"}

    if data.get("status") == "success":
        logger.info(f"[FLW OTP] Validate ref={reference}: verified=True")
        return {"verified": True, "reason": "ok"}

    # Flutterwave doesn't document its validate error strings — classify
    # defensively so users get accurate feedback.
    msg = (data.get("message") or "").lower()
    if resp.status_code >= 500:
        reason = "provider_error"
    elif "expired" in msg:
        reason = "expired"
    elif (
        resp.status_code == 404
        or "not found" in msg
        or "invalid reference" in msg
        or "does not exist" in msg
    ):
        # Reference unknown to Flutterwave — same UX as an expired session.
        reason = "expired"
    else:
        reason = "invalid"

    logger.info(f"[FLW OTP] Validate ref={reference}: verified=False reason={reason} | {data}")
    return {"verified": False, "reason": reason, "message": data.get("message")}
