"""Flutterwave OTP client — v3 /otps service (SMS / WhatsApp / email).

Wraps ``{v3_base_url}/otps`` — Flutterwave generates, delivers
and validates the code itself, so no OTP material is ever stored or logged
server-side (the create response echoes the OTP back inside ``data`` — it
is read for its ``reference`` only). Auth is the v3 ``FLWSECK`` secret key
(``config.v3_secret_key``), which the collection client only needs
when ``charge_api=v3`` — enabling this provider effectively makes the
v3 key required even on the default v4 charge path. A missing key fails
closed: there is no mock path; tests stub this client at the boundary.

Delivery channels are chosen via ``medium``: 'sms' or 'whatsapp' — one
billed message per send. ``sender`` is the name shown inside the OTP
message — for SMS it is the sender ID, which must be registered with
Flutterwave for some corridors (Nigeria included); configure
``config.otp_sender`` to match the registered ID or sends are rejected
upstream.
"""
import logging
import re

import requests

from .config import FlutterwaveConfig
from .constants import V3_BASE_URL

logger = logging.getLogger(__name__)


class FlutterwaveOTPError(RuntimeError):
    """Provider failure carrying Flutterwave's error detail."""

    def __init__(self, message, *, code=None, status_code=None, request_id=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.request_id = request_id


class FlutterwaveOTPClient:
    """Thin REST client for the v3 /otps endpoints (create + validate).

    Responses are normalized to the shared create/check vocabulary other
    OTP providers consume, so swapping providers is a one-line change.
    """

    V3_BASE_URL = V3_BASE_URL
    OTP_LENGTH = 6          # Flutterwave accepts lengths 5–7
    OTP_EXPIRY_MINUTES = 10

    # --- class-level defaults; __init__ overrides from the config ---
    secret_key = ''
    base_url = V3_BASE_URL
    sender = 'Verify'
    brand_name = 'Merchant'
    customer_name = ''

    def __init__(self, config=None):
        cfg = config or FlutterwaveConfig()
        self.secret_key = cfg.v3_secret_key or ''
        self.base_url = (cfg.v3_base_url or self.V3_BASE_URL).rstrip('/')
        # Sender name inside the OTP message (the SMS sender ID — must be
        # registered with Flutterwave in sender-ID corridors like NG).
        self.sender = (cfg.otp_sender or 'Verify').strip()
        self.brand_name = cfg.brand_name or 'Merchant'
        # v3 /otps requires a customer name — capped at 10 chars upstream.
        self.customer_name = (
            cfg.otp_customer_name or f'{self.brand_name} user')

    def _request(self, path, payload, *, allow_error_status=False):
        if not self.secret_key:
            raise FlutterwaveOTPError(
                'FLUTTERWAVE_V3_SECRET_KEY is not configured.')
        try:
            resp = requests.post(
                f'{self.base_url}{path}',
                json=payload,
                headers={
                    'Authorization': f'Bearer {self.secret_key}',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.error('Flutterwave OTP connection error: %s', exc)
            raise FlutterwaveOTPError(
                f'Flutterwave connection failed: {exc}') from exc

        if resp.status_code == 429:
            raise FlutterwaveOTPError(
                'Verification provider is rate limiting — try again shortly.',
                code='rate_limited', status_code=429,
            )
        # A rejected OTP check is an expected outcome (wrong/expired code),
        # so the validate path reads the 4xx body instead of raising; real
        # failures (5xx, or 4xx on the send path) still raise.
        if resp.status_code >= 400 and not (
            allow_error_status and resp.status_code < 500
        ):
            try:
                err = resp.json()
            except ValueError:
                err = {}
            raise FlutterwaveOTPError(
                err.get('message')
                    or f'Flutterwave request failed ({resp.status_code})',
                code=err.get('code'), status_code=resp.status_code,
            )
        try:
            return resp.json()
        except ValueError:
            raise FlutterwaveOTPError(
                f'Flutterwave returned an unreadable response ({resp.status_code}).',
                status_code=resp.status_code,
            )

    def create_verification(self, phone_number, *, ip=None, user_agent=None,
                            preferred_channel=None, correlation_id=None,
                            customer=None):
        """POST /v3/otps — send a provider-generated OTP to an E.164 number.

        ``preferred_channel`` selects the delivery medium: 'sms' →
        ``["sms"]``, 'whatsapp' → ``["whatsapp"]``. There is no 'auto'
        — Flutterwave delivers to every listed medium and bills each, so
        a stray non-explicit request falls back to a single SMS (the
        channel that reaches any number; a WhatsApp send to a
        non-WhatsApp line is billed but never arrives).
        ``ip``/``user_agent``/``correlation_id`` are accepted for signature
        parity; the OTP API takes no fraud signals or metadata. The v3
        ``customer`` block requires email/name alongside the phone —
        supplied via ``customer`` even though delivery only needs the
        number.
        """
        mediums = [preferred_channel if preferred_channel in ('sms', 'whatsapp')
                   else 'sms']
        customer = dict(customer or {})
        result = self._request('/otps', {
            'length': self.OTP_LENGTH,
            'customer': {
                # v3 caps the customer name at 10 characters.
                'name': str(customer.get('name') or self.customer_name)[:10],
                'email': str(customer.get('email') or ''),
                'phone': re.sub(r'\D', '', phone_number),
            },
            'sender': self.sender,
            'send': True,
            'medium': mediums,
            'expiry': self.OTP_EXPIRY_MINUTES,
        })

        if str(result.get('status') or '').lower() != 'success':
            raise FlutterwaveOTPError(
                result.get('message') or 'Flutterwave could not create the OTP.',
                code='create_failed',
            )
        # data[] carries one entry per medium (same OTP each) — plus the
        # OTP itself, which is deliberately not logged or returned. Any
        # single reference validates the shared code, so the first is kept
        # as the session's provider_reference.
        data = [d for d in result.get('data') or [] if isinstance(d, dict)]
        if not data:
            raise FlutterwaveOTPError(
                'Flutterwave returned no OTP reference.', code='empty_response')
        sent = [str(d.get('medium') or '') for d in data if d.get('medium')]
        return {
            'id': data[0].get('reference') or '',
            'status': 'sent',
            'channels': sent,
            'channel': sent[0] if sent else (preferred_channel or ''),
        }

    def check_verification(self, phone_number, code, *, session=None):
        """POST /v3/otps/{reference}/validate — keyed by the send-time
        reference stored on the session row (``session.provider_reference``).
        Normalizes the answer to the shared vocabulary: success | failure |
        expired_or_not_found. ``phone_number`` is accepted for signature
        parity — the reference alone identifies the OTP.

        Flutterwave's verdict vocabulary is ambiguous: a wrong code AND a
        dead reference both return ``"OTP not found"`` (verified against the
        live API — a failed validate does not burn the OTP). It maps to a
        retryable ``failure`` — real expiry is already caught by the local
        TTL before this call. Explicitly-dead wording maps to
        ``expired_or_not_found``; any other error raises so an operational
        failure (billing, auth) can't masquerade as a wrong code."""
        reference = getattr(session, 'provider_reference', '') or ''
        if not reference:
            return {'status': 'expired_or_not_found', 'reason': 'no_reference'}
        result = self._request(
            f'/otps/{reference}/validate', {'otp': code},
            allow_error_status=True,
        )
        if str(result.get('status') or '').lower() == 'success':
            return {'status': 'success'}
        message = str(result.get('message') or '')
        lowered = message.lower()
        if 'otp not found' in lowered:
            return {'status': 'failure', 'reason': message}
        if any(k in lowered for k in (
            'expired', 'invalid reference', 'reference not found',
        )):
            return {'status': 'expired_or_not_found', 'reason': message}
        raise FlutterwaveOTPError(
            message or 'Flutterwave could not validate the OTP.',
            code='validate_failed',
        )
