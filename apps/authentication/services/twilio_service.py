"""
Twilio OTP service — preserved for future reactivation.

This module is currently NOT used by the active authentication flow (Termii is
the active provider). It is kept here so that the phone verification flow can
be re-enabled with Twilio at a later date without having to recreate the file.

To reactivate:
  1. Add `twilio` to backend/requirements.txt and install it.
  2. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_VERIFY_SERVICE_SID in
     settings.py / .env.
  3. Wire `send_verification_otp` and `verify_otp` into the phone verification
     views in apps/authentication/views.py (RequestPhoneVerificationCodeView
     and VerifyPhoneCodeView) as a fallback behind Termii.
"""

import logging

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """Returns True if Twilio credentials are present in settings."""
    from django.conf import settings

    return bool(
        getattr(settings, "TWILIO_ACCOUNT_SID", "")
        and getattr(settings, "TWILIO_AUTH_TOKEN", "")
        and getattr(settings, "TWILIO_VERIFY_SERVICE_SID", "")
    )


def send_verification_otp(to_phone: str, code: str = "", channel: str = "whatsapp") -> dict:
    """
    Send an OTP via Twilio Verify.

    NOTE: This function is a placeholder. It requires the `twilio` Python
    package to be installed and the credentials above to be configured
    before it can be used.
    """
    if not is_configured():
        logger.warning("Twilio is not configured; cannot send OTP.")
        return {"success": False, "message": "Twilio not configured"}

    try:
        from twilio.rest import Client  # type: ignore

        from django.conf import settings

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verifications.create(to=to_phone, channel=channel)
        return {
            "success": True,
            "status": verification.status,
            "sid": verification.sid,
        }
    except Exception as e:
        logger.exception("Twilio OTP send failed: %s", e)
        return {"success": False, "message": str(e)}


def verify_otp(to_phone: str, code: str) -> dict:
    """
    Verify an OTP via Twilio Verify.

    NOTE: This function is a placeholder. It requires the `twilio` Python
    package to be installed and the credentials above to be configured
    before it can be used.
    """
    if not is_configured():
        logger.warning("Twilio is not configured; cannot verify OTP.")
        return {"success": False, "message": "Twilio not configured"}

    try:
        from twilio.rest import Client  # type: ignore

        from django.conf import settings

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification_check = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verification_checks.create(to=to_phone, code=code)
        return {
            "success": verification_check.status == "approved",
            "status": verification_check.status,
        }
    except Exception as e:
        logger.exception("Twilio OTP verify failed: %s", e)
        return {"success": False, "message": str(e)}
