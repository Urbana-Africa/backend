import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def get_twilio_client():
    """
    Initializes and returns the Twilio REST Client if credentials are configured.
    """
    account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", None)
    auth_token = getattr(settings, "TWILIO_AUTH_TOKEN", None)

    if not account_sid or not auth_token:
        logger.debug("[TWILIO] Account SID or Auth Token is not configured.")
        return None

    try:
        from twilio.rest import Client
        return Client(account_sid, auth_token)
    except Exception as e:
        logger.error(f"[TWILIO] Failed to initialize Twilio client: {str(e)}")
        return None


def send_sms(to_phone: str, body: str) -> bool:
    """
    Sends an SMS to the specified international phone number using Twilio.
    """
    client = get_twilio_client()
    from_number = getattr(settings, "TWILIO_PHONE_NUMBER", None)

    # Clean phone number
    to_phone = to_phone.strip().replace(" ", "")

    if not client or not from_number:
        logger.info(f"[SMS SIMULATION] To: {to_phone} | Body: {body}")
        return True

    try:
        message = client.messages.create(
            body=body,
            from_=from_number,
            to=to_phone,
        )
        logger.info(f"[TWILIO SMS] Sent message {message.sid} to {to_phone}")
        return True
    except Exception as e:
        logger.error(f"[TWILIO SMS ERROR] Failed to send SMS to {to_phone}: {str(e)}")
        return False


def send_verification_otp(to_phone: str, code: str) -> dict:
    """
    Dispatches a 6-digit verification code to the recipient phone number.
    Supports Twilio Verify Service or standard Twilio SMS fallback.
    """
    to_phone = to_phone.strip().replace(" ", "")
    service_sid = getattr(settings, "TWILIO_VERIFY_SERVICE_SID", None)
    client = get_twilio_client()

    # Case 1: Twilio Verify Service (v2)
    if client and service_sid:
        try:
            verification = client.verify.v2.services(service_sid).verifications.create(
                to=to_phone,
                channel="sms",
            )
            logger.info(f"[TWILIO VERIFY] Verification requested for {to_phone}: status={verification.status}")
            return {"success": True, "method": "twilio_verify", "status": verification.status}
        except Exception as e:
            logger.error(f"[TWILIO VERIFY ERROR] Verification failed for {to_phone}: {str(e)}")
            # Fall back to manual SMS if verify fails

    # Case 2: Standard Twilio SMS
    body = f"Your Urbana verification code is: {code}. It will expire in 10 minutes."
    sent = send_sms(to_phone, body)
    return {"success": sent, "method": "twilio_sms"}


def check_verification_otp(to_phone: str, code: str) -> bool:
    """
    Checks verification code against Twilio Verify API if configured.
    """
    to_phone = to_phone.strip().replace(" ", "")
    service_sid = getattr(settings, "TWILIO_VERIFY_SERVICE_SID", None)
    client = get_twilio_client()

    if client and service_sid:
        try:
            check = client.verify.v2.services(service_sid).verification_checks.create(
                to=to_phone,
                code=code,
            )
            logger.info(f"[TWILIO VERIFY CHECK] {to_phone}: status={check.status}")
            return check.status == "approved"
        except Exception as e:
            logger.error(f"[TWILIO VERIFY CHECK ERROR] Check failed for {to_phone}: {str(e)}")
            return False

    return False
