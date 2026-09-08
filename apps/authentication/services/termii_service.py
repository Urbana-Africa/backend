"""
Termii OTP service — handles phone number verification via the Termii API.

Termii generates and stores the OTP code server-side; we only need to keep
the returned `pin_id` and pass it to the verify endpoint.

Docs: https://developers.termii.com/messaging/one-time-passwords

Required settings (settings.py / .env):
  - TERMII_API_KEY    : your Termii API key
  - TERMII_SENDER_ID  : sender ID shown to the recipient (e.g. "Urbana")
  - TERMII_API_BASE   : optional, defaults to https://api.ng.termii.com
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.ng.termii.com"
OTP_SEND_URL = f"{API_BASE}/api/sms/otp/send"
OTP_VERIFY_URL = f"{API_BASE}/api/sms/otp/verify"

# Termii channel options: "dnd" (bypasses Do-Not-Disturb, recommended for
# Nigeria), "sms", "whatsapp", "voice", "generic".
_CHANNEL_MAP = {
    "whatsapp": "whatsapp",
    "sms": "dnd",      # use "dnd" so DND-registered numbers still receive
    "voice": "voice",
}


def _get_api_key():
    return getattr(settings, "TERMII_API_KEY", "") or ""


def _get_sender_id():
    return getattr(settings, "TERMII_SENDER_ID", "Urbana") or "Urbana"


def _clean_phone(phone: str) -> str:
    """Termii expects the number in international format WITHOUT the leading +."""
    phone = phone.strip().replace(" ", "")
    if phone.startswith("+"):
        phone = phone[1:]
    return phone


def is_configured() -> bool:
    """Returns True if Termii API key is set."""
    return bool(_get_api_key())


def send_verification_otp(to_phone: str, code: str, channel: str = "whatsapp") -> dict:
    """
    Requests Termii to generate and dispatch an OTP to the phone number.

    Termii generates the code itself — the `code` argument is accepted for
    interface compatibility but is NOT used (Termii manages it server-side).

    Args:
        to_phone: international format, e.g. "+2348123456789"
        code:     ignored (kept for call-site compatibility)
        channel:  "whatsapp" (default) or "sms"

    Returns:
        dict with keys:
          - success (bool)
          - method  (str, e.g. "termii_whatsapp")
          - pin_id  (str, needed for verification — store it!)
          - status  (str, raw Termii status)
    """
    api_key = _get_api_key()
    to = _clean_phone(to_phone)
    termii_channel = _CHANNEL_MAP.get((channel or "whatsapp").lower(), "whatsapp")

    if not api_key:
        # Simulation mode — no API key configured
        logger.info(f"[TERMII SIMULATION] To: {to} | Channel: {termii_channel}")
        return {
            "success": True,
            "method": f"termii_{termii_channel}_sim",
            "pin_id": f"sim_{to}",
            "status": "simulated",
        }

    payload = {
        "api_key": api_key,
        "to": to,
        "from": _get_sender_id(),
        "channel": termii_channel,
        "message_type": "NUMERIC",
        "pin_attempts": 3,
        "pin_time_to_live": 10,          # minutes
        "pin_length": 6,
        "pin_placeholder": "< 1234 >",   # shown in the message before the code arrives
        "message_text": "Your Urbana verification code is < 1234 >. It expires in 10 minutes.",
        "pin_type": "NUMERIC",
    }

    try:
        resp = requests.post(OTP_SEND_URL, json=payload, timeout=15)
        data = resp.json()
    except Exception as e:
        logger.error(f"[TERMII] Send request failed for {to}: {e}")
        return {"success": False, "method": f"termii_{termii_channel}", "error": str(e)}

    logger.info(f"[TERMII] Send to {to} via {termii_channel}: {data}")

    pin_id = data.get("pinId")
    if pin_id and data.get("status") in ("success", "pending", "Message sent", 200, "200"):
        return {
            "success": True,
            "method": f"termii_{termii_channel}",
            "pin_id": pin_id,
            "status": data.get("status"),
        }

    # Fallback: if WhatsApp fails, retry with SMS/dnd
    if termii_channel == "whatsapp":
        logger.warning(f"[TERMII] WhatsApp failed for {to}; retrying via dnd (SMS).")
        payload["channel"] = "dnd"
        try:
            resp = requests.post(OTP_SEND_URL, json=payload, timeout=15)
            data = resp.json()
        except Exception as e:
            logger.error(f"[TERMII] SMS fallback failed for {to}: {e}")
            return {"success": False, "method": "termii_sms", "error": str(e)}

        logger.info(f"[TERMII] SMS fallback to {to}: {data}")
        pin_id = data.get("pinId")
        if pin_id:
            return {
                "success": True,
                "method": "termii_sms",
                "pin_id": pin_id,
                "status": data.get("status"),
            }

    return {
        "success": False,
        "method": f"termii_{termii_channel}",
        "error": data.get("message", "Unknown Termii error"),
        "status": data.get("status"),
    }


def check_verification_otp(to_phone: str, code: str, pin_id: str = None) -> bool:
    """
    Verifies the OTP against Termii's verify endpoint.

    Args:
        to_phone: international format (unused by Termii verify, kept for
                  interface compatibility).
        code:     the 6-digit code the user entered.
        pin_id:   the pinId returned by send_verification_otp (REQUIRED).

    Returns:
        True if the code is valid, False otherwise.
    """
    api_key = _get_api_key()

    if not api_key:
        # Simulation mode — accept any 6-digit code
        logger.info(f"[TERMII SIMULATION] Auto-accepting code for {to_phone}")
        return True

    if not pin_id:
        logger.error(f"[TERMII] Cannot verify — missing pin_id for {to_phone}")
        return False

    payload = {
        "api_key": api_key,
        "pin_id": pin_id,
        "pin": code,
    }

    try:
        resp = requests.post(OTP_VERIFY_URL, json=payload, timeout=15)
        data = resp.json()
    except Exception as e:
        logger.error(f"[TERMII] Verify request failed for {to_phone}: {e}")
        return False

    verified = data.get("verified") is True or data.get("status") == "success"
    logger.info(f"[TERMII] Verify {to_phone} (pin_id={pin_id}): verified={verified} | {data}")
    return verified
