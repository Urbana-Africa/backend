"""Framework-agnostic configuration for flutterwave_kit.

``FlutterwaveConfig`` carries everything the client and OTP client need —
no Django, no settings module. Build it directly, from a flat dict
(``from_dict`` — e.g. a Django settings dict keyed by the FLUTTERWAVE_*
names), or from environment variables (``from_env``).
"""
import json
import os
from dataclasses import dataclass, field
from typing import Optional

from .constants import V3_BASE_URL, V4_SANDBOX_BASE_URL


@dataclass
class FlutterwaveConfig:
    # --- v4 API (charges, customers, payment methods, transfers, banks,
    # refunds, virtual accounts) — OAuth 2.0 client credentials ---
    client_id: str = ''
    client_secret: str = ''
    v4_base_url: str = V4_SANDBOX_BASE_URL
    # AES-256-GCM key (base64) for encrypting card fields — dashboard API
    # settings. Doubles as the v3 encryption-key fallback.
    encryption_key: str = ''
    # Webhook secret hash — verifies the flutterwave-signature HMAC header.
    webhook_hash: str = ''

    # --- charge rail: 'v4' (default) or 'v3' legacy direct charge ---
    charge_api: str = 'v4'
    # v3 (legacy) credentials — FLWSECK bearer key + 3DES-ECB charge
    # encryption. Only needed when charge_api='v3' or when using the OTP
    # client (the /otps service is v3-only).
    v3_secret_key: str = ''
    v3_base_url: str = V3_BASE_URL
    v3_encryption_key: str = ''  # falls back to encryption_key

    # --- behaviour flags ---
    # testing: forces mock mode and accepts unsigned webhook requests.
    testing: bool = False
    # debug: enables DEBUG-only mock branches (e.g. verify_transaction
    # short-circuit on mock references).
    debug: bool = False
    # Explicit mock override. Mock responses are produced only when this is
    # True or ``testing`` is True; missing credentials always fail closed.
    mock: Optional[bool] = None

    # --- branding — strings surfaced to payers and the provider ---
    # Used in narrations ('{brand_name} payout') and default names.
    brand_name: str = 'Merchant'
    # Prefix for auto-minted references: '{prefix}flw…', '{prefix}va…',
    # '{prefix}payout…' (kept ASCII-alphanumeric for v4 validation).
    reference_prefix: str = 'pay'
    # Fallback name parts when a customer name can't be split into valid
    # >=2-char first/last (v4 rejects short or missing parts).
    name_fallback_first: str = 'Customer'
    name_fallback_last: str = 'User'
    # OTP sender name — for SMS it is the sender ID and must be registered
    # with Flutterwave in sender-ID corridors (Nigeria included).
    otp_sender: str = 'Verify'
    # v3 /otps customer.name (capped at 10 chars by the API). '' defaults
    # to '{brand_name} user'.
    otp_customer_name: str = ''

    # --- payouts ---
    # Transfer-sender entity — mandatory on EUR/GBP/EGP/INR corridors:
    # {'name': {'first','last'}, 'email', 'address': {...}, 'phone': {...}}.
    payout_sender: dict = field(default_factory=dict)
    # Static FX table {currency: rate} used only by MOCK transfer-rate
    # quotes — live quotes always come from POST /transfers/rates.
    fx_rates: dict = field(default_factory=dict)

    # --- bill payments (v3-only service — debits the merchant wallet) ---
    # Provider category codes surfaced to clients (AIRTIME, MOBILEDATA,
    # CABLEBILLS, UTILITYBILLS by default). Empty tuple = package default.
    bill_categories: tuple = ()
    # ISO countries whose catalog is served. Bills are Nigeria-only.
    bill_countries: tuple = ()

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d):
        """Build from a flat settings-style dict.

        Recognized keys mirror the FLUTTERWAVE_* / FLW_* env names —
        a Django settings dict can be passed directly.
        """
        d = d or {}

        def get(key, default=''):
            return d.get(key, default)

        return cls(
            client_id=get('FLUTTERWAVE_CLIENT_ID'),
            client_secret=get('FLUTTERWAVE_CLIENT_SECRET'),
            v4_base_url=get('FLUTTERWAVE_V4_BASE_URL') or V4_SANDBOX_BASE_URL,
            encryption_key=get('FLUTTERWAVE_ENCRYPTION_KEY'),
            webhook_hash=get('FLUTTERWAVE_WEBHOOK_HASH'),
            charge_api=str(get('FLW_CHARGE_API', 'v4') or 'v4').strip().lower(),
            v3_secret_key=get('FLUTTERWAVE_V3_SECRET_KEY'),
            v3_base_url=get('FLUTTERWAVE_V3_BASE_URL') or V3_BASE_URL,
            v3_encryption_key=get('FLUTTERWAVE_V3_ENCRYPTION_KEY'),
            testing=_as_bool(get('FLW_TESTING')),
            debug=_as_bool(get('FLW_DEBUG')),
            mock=_as_optional_bool(get('FLW_MOCK')),
            payout_sender=_as_dict(get('PAYOUT_SENDER')),
            fx_rates=_as_dict(get('FX_RATES_TO_NGN')),
            otp_sender=(
                get('FLW_OTP_SENDER') or get('FLW_BRAND_NAME') or 'Verify'),
            otp_customer_name=get('FLW_OTP_CUSTOMER_NAME'),
            brand_name=get('FLW_BRAND_NAME') or 'Merchant',
            reference_prefix=get('FLW_REFERENCE_PREFIX') or 'pay',
            name_fallback_first=get('FLW_NAME_FALLBACK_FIRST') or 'Customer',
            name_fallback_last=get('FLW_NAME_FALLBACK_LAST') or 'User',
            bill_categories=tuple(
                m.strip().upper()
                for m in str(get('BILL_CATEGORIES', '') or '').split(',')
                if m.strip()),
            bill_countries=tuple(
                m.strip().upper()
                for m in str(get('BILL_COUNTRIES', '') or '').split(',')
                if m.strip()),
        )

    @classmethod
    def from_env(cls, environ=None):
        """Build from environment variables (defaults to ``os.environ``).

        Same key names as ``from_dict`` plus ``PAYOUT_SENDER_JSON`` /
        ``FX_RATES_TO_NGN_JSON`` for the dict fields and ``FLW_TESTING``
        ('1'/'true'/'yes') for the mock/testing flag.
        """
        env = os.environ if environ is None else environ
        cfg = cls.from_dict(env)
        if env.get('PAYOUT_SENDER_JSON'):
            cfg.payout_sender = _as_dict(env.get('PAYOUT_SENDER_JSON'))
        if env.get('FX_RATES_TO_NGN_JSON'):
            cfg.fx_rates = _as_dict(env.get('FX_RATES_TO_NGN_JSON'))
        return cfg


def _as_dict(value):
    """Coerce a config value into a dict — accepts a dict, a JSON string,
    or anything falsy (-> {})."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _as_bool(value):
    """Parse a settings/env boolean without treating non-empty strings as true."""
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _as_optional_bool(value):
    """Return None when unset, otherwise parse the supplied boolean value."""
    if value is None or str(value).strip() == '':
        return None
    return _as_bool(value)
