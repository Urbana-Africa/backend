"""Flutterwave API client — v4 default, optional v3 direct-charge mode.

Framework-agnostic: all configuration arrives via ``FlutterwaveConfig``
(see ``config.py``) — construct directly, ``from_dict`` (settings-style
dicts) or ``from_env``. No Django import anywhere in this package.

Collection runs on the v4 API (``developersandbox-api.flutterwave.com`` in
sandbox), which authenticates with OAuth 2.0 client-credentials tokens
(10-minute expiry, cached in-process per worker). There is no hosted
checkout in either API: collection happens through charge endpoints and our
own checkout UI.

Setting ``config.charge_api = 'v3'`` switches COLLECTION ONLY to the
legacy v3 API (``api.flutterwave.com/v3``), which authenticates with the
``FLWSECK`` secret key and 3DES-ECB-encrypts charge payloads. v3 is kept as
an alternative while PWBT/feature-gated channels shake out on v4; payouts,
banks, refunds and virtual accounts always stay on v4, so the v4 OAuth
credentials must remain configured regardless of the charge mode.

Collection flows:
- Orchestrator (one-time tips): ``POST /orchestration/direct-charges`` bundles
  customer + payment method + charge in one request.
- General (subscriptions / recurring): ``POST /customers`` ->
  ``POST /payment-methods`` -> ``POST /charges`` -> ``PUT /charges/{id}``
  (authorize) -> ``GET /charges/{id}`` (verify). Stored ``payment_method_id``
  is reused for merchant-initiated renewals with ``recurring: true``.

Sensitive card fields are AES-256-GCM encrypted before transmission: the key
is the base64 encryption key from the Flutterwave dashboard, and the IV is a
random 12-character alphanumeric nonce that travels with the request.

v4 endpoints used:
- POST {V4}/orchestration/direct-charges   one-shot charge (orchestrator)
- POST {V4}/customers                      create customer
- POST {V4}/payment-methods                create (tokenize) payment method
- POST {V4}/charges                        initiate charge (general flow)
- PUT  {V4}/charges/{id}                   authorize (pin / otp / avs)
- GET  {V4}/charges/{id}                   retrieve/verify charge
- POST {V4}/refunds                        refund a charge
- POST {V4}/virtual-accounts               pay-with-bank-transfer account
- POST {V4}/direct-transfers               initiate bank payout
- GET  {V4}/banks?country=XX               bank list per country
- GET  {V4}/banks/{id}/branches            branch codes where required
- POST {V4}/banks/account-resolve          account-name enquiry
- GET  {V4}/transfers/{id}                 transfer status
- GET  {V4}/wallets/balances               payout-balance float per currency

v3 endpoints used beyond charges (bearer-key services):
- POST {V3}/otps + /otps/{ref}/validate    phone OTP (see otp.py)
- GET  {V3}/bill-categories[?flag|c=code] flat bill catalog (items+billers)
- GET  {V3}/bill-items/{code}/validate    customer identifier enquiry
- POST {V3}/bills                        execute bill payment (wallet debit)
- GET  {V3}/bills/{reference}             bill payment status

Webhook verification: v4 sends ``flutterwave-signature`` =
base64(HMAC-SHA256(raw_body, secret_hash)). The legacy v3 ``verif-hash``
plaintext header is also accepted for compatibility.
"""
import base64
import hashlib
import hmac
import re
import secrets
import string
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests
from decimal import Decimal, InvalidOperation

from .config import FlutterwaveConfig
from .constants import (
    TOKEN_URL, V3_BASE_URL, V4_SANDBOX_BASE_URL,
    SUPPORTED_PAYOUT_COUNTRIES, CURRENCY_DIAL_CODES,
    V3_CURRENCY_COUNTRY, V3_MOMO_CHARGE_TYPES, _KNOWN_DIAL_CODES,
    DEFAULT_BILL_CATEGORIES, BILL_CATEGORY_ALIASES, BILL_CATEGORY_FILTERS,
    BILL_CATEGORY_NAMES, BILLER_NAME_HINTS, BILL_COUNTRIES,
    STABLECOIN_FUNDING_CURRENCIES, STABLECOIN_SUPPORTED_NETWORKS,
    validate_crypto_address,
)
from .currency import to_minor_units

logger = logging.getLogger(__name__)


def _to_minor_units(amount, currency='NGN'):
    """Convert a major-unit amount to minor currency units (e.g. kobo/cents;
    whole units for zero-decimal currencies like XOF/RWF/UGX)."""
    return to_minor_units(amount, currency)

class FlutterwaveConfigurationError(RuntimeError):
    """Raised when a live operation is attempted without required settings."""


# In-process OAuth token cache. Entries are isolated by endpoint and a hash of
# the credentials, allowing one process to safely serve multiple merchants.
# The top-level fields are retained for adapter/test compatibility and expose
# only the most recently fetched token.
_token_cache = {
    'access_token': None,
    'expires_at': 0.0,
    'cache_key': None,
    'tokens': {},
}


class FlutterwaveClient:
    """Flutterwave v4 client: charges, customers, payment methods, refunds,
    virtual accounts, transfers, banks.

    Configured via ``FlutterwaveConfig`` — ``FlutterwaveClient(config)``.
    Every setting is also a class attribute, so subclasses (or ``__new__``
    instances in tests) resolve sane defaults without ``__init__`` running.
    """

    # --- class-level defaults; __init__ overrides from the config ---
    charge_api = 'v4'
    client_id = ''
    client_secret = ''
    v4_base_url = V4_SANDBOX_BASE_URL
    encryption_key = ''
    webhook_hash = ''
    v3_secret_key = ''
    v3_base_url = V3_BASE_URL
    v3_encryption_key = ''
    testing = False
    debug = False
    mock = False
    payout_mock = False
    bills_mock = False
    bill_categories = DEFAULT_BILL_CATEGORIES
    bill_countries = BILL_COUNTRIES
    brand_name = 'Merchant'
    reference_prefix = 'pay'
    name_fallback_first = 'Customer'
    name_fallback_last = 'User'
    payout_sender = {}
    fx_rates = {}

    def __init__(self, config=None):
        cfg = config or FlutterwaveConfig()
        # Which API collection (charges) runs on — 'v4' (default) or 'v3'.
        charge_api = str(cfg.charge_api or 'v4').strip().lower()
        self.charge_api = charge_api if charge_api in ('v4', 'v3') else 'v4'
        # v4 OAuth client credentials (charges, transfers, banks, refunds)
        self.client_id = cfg.client_id or ''
        self.client_secret = cfg.client_secret or ''
        self.v4_base_url = (cfg.v4_base_url or V4_SANDBOX_BASE_URL).rstrip('/')
        # AES-256 key (base64) for encrypting card fields — dashboard API settings
        self.encryption_key = cfg.encryption_key or ''
        self.webhook_hash = cfg.webhook_hash or ''
        # v3 (legacy) credentials — secret key auth + 3DES charge encryption.
        # The encryption key is the same "Encryption Key" shown on the
        # dashboard API settings page, so the v4 key doubles as fallback.
        self.v3_secret_key = cfg.v3_secret_key or ''
        self.v3_base_url = (cfg.v3_base_url or V3_BASE_URL).rstrip('/')
        self.v3_encryption_key = cfg.v3_encryption_key or self.encryption_key
        self.testing = bool(cfg.testing)
        self.debug = bool(cfg.debug)
        self.brand_name = cfg.brand_name or 'Merchant'
        self.reference_prefix = (
            self._alnum(cfg.reference_prefix or 'pay') or 'pay')
        self.name_fallback_first = cfg.name_fallback_first or 'Customer'
        self.name_fallback_last = cfg.name_fallback_last or 'User'
        self.payout_sender = dict(cfg.payout_sender or {})
        self.fx_rates = dict(cfg.fx_rates or {})
        # Mock behavior must be explicitly enabled (or selected by a test
        # environment). Missing credentials fail closed at the request layer.
        explicit_mock = cfg.mock is True
        self.mock = self._is_testing() or explicit_mock
        self.payout_mock = self._is_testing() or explicit_mock
        # Bill payments ride the v3 API regardless of the charge mode.
        # Unlike charges/payouts, mock is allowed only outside production
        # contexts — a live deploy missing the v3 key must fail loudly
        # (catalog 502s) rather than fake a bill fulfillment while the
        # ledger debits a real wallet (the /otps "raise, never fake"
        # precedent).
        self.bills_mock = (
            self._is_testing()
            or explicit_mock
            or (cfg.mock is None and self._is_debug() and not self.v3_secret_key)
        )
        self.bill_categories = tuple(cfg.bill_categories or DEFAULT_BILL_CATEGORIES)
        self.bill_countries = tuple(cfg.bill_countries or BILL_COUNTRIES)

    # ------------------------------------------------------------------
    # Environment hooks — subclasses/framework adapters may override to
    # read live settings instead of the constructor-time flags.
    # ------------------------------------------------------------------
    def _is_testing(self):
        return self.testing

    def _is_debug(self):
        return self.debug

    def _get_payout_sender(self):
        return self.payout_sender or {}

    def _get_fx_rates(self):
        return self.fx_rates or {}

    # ------------------------------------------------------------------
    # v4 OAuth
    # ------------------------------------------------------------------
    def _token_cache_key(self):
        material = '\0'.join((
            TOKEN_URL,
            self.v4_base_url,
            self.client_id,
            self.client_secret,
        )).encode('utf-8')
        return hashlib.sha256(material).hexdigest()

    def invalidate_token_cache(self):
        """Clear this client's cached OAuth access token."""
        cache_key = self._token_cache_key()
        _token_cache.setdefault('tokens', {}).pop(cache_key, None)
        _token_cache['access_token'] = None
        _token_cache['expires_at'] = 0.0
        _token_cache['cache_key'] = None

    def _access_token(self, force_refresh=False):
        """Return a valid OAuth access token, refreshing when near expiry or forced."""
        if not self.client_id or not self.client_secret:
            raise FlutterwaveConfigurationError(
                'FLUTTERWAVE_CLIENT_ID and FLUTTERWAVE_CLIENT_SECRET are '
                'required for live v4 operations. Set mock=True explicitly '
                'for local mock responses.')
        cache_key = self._token_cache_key()
        entry = _token_cache.setdefault('tokens', {}).get(cache_key) or {}
        cached = entry.get('access_token')
        # Refresh 60s before expiry to absorb clock skew / latency.
        if not force_refresh and cached and time.time() < entry.get('expires_at', 0.0) - 60:
            return cached
        resp = requests.post(
            TOKEN_URL,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'client_credentials',
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data['access_token']
        expires_in = int(data.get('expires_in', 600))
        expires_at = time.time() + expires_in
        _token_cache['tokens'][cache_key] = {
            'access_token': token,
            'expires_at': expires_at,
        }
        _token_cache['access_token'] = token
        _token_cache['expires_at'] = expires_at
        _token_cache['cache_key'] = cache_key
        return token

    @staticmethod
    def _alnum(value):
        """Keep only ASCII alphanumerics — v4 validates fields like
        ``reference`` (and is strict about header formats) as alphanumeric."""
        return ''.join(c for c in str(value) if c.isascii() and c.isalnum())

    def _v4_headers(self, idempotent=False, idempotency_key=None, force_refresh=False):
        headers = {
            'Authorization': f'Bearer {self._access_token(force_refresh=force_refresh)}',
            'Content-Type': 'application/json',
            'X-Trace-Id': uuid.uuid4().hex,
        }
        key = idempotency_key or (uuid.uuid4().hex if idempotent else None)
        if key:
            clean = self._alnum(key)
            if clean != str(key):
                # Sanitizing collapsed characters — keep the key unique and
                # deterministic so retries still replay the same request.
                clean = f'{clean}{hashlib.sha256(str(key).encode()).hexdigest()[:10]}'
            headers['X-Idempotency-Key'] = clean
        return headers

    def _v4_request(self, method, url, idempotency_key=None, **kwargs):
        """Execute a v4 API request, automatically refreshing OAuth token and retrying once on 401."""
        func = getattr(requests, method.lower(), requests.request)
        headers = self._v4_headers(idempotency_key=idempotency_key)
        resp = func(url, headers=headers, **kwargs) if func != requests.request else requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            self.invalidate_token_cache()
            headers = self._v4_headers(idempotency_key=idempotency_key, force_refresh=True)
            resp = func(url, headers=headers, **kwargs) if func != requests.request else requests.request(method, url, headers=headers, **kwargs)
        return resp

    # ------------------------------------------------------------------
    # Card field encryption (AES-256-GCM, nonce as IV, base64 output)
    # ------------------------------------------------------------------
    @staticmethod
    def _new_nonce():
        """Random 12-char alphanumeric nonce used as the AES-GCM IV."""
        return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

    def _encrypt(self, plaintext, nonce):
        """AES-256-GCM encrypt ``plaintext`` with the dashboard encryption key.

        The base64 key decodes to raw AES key bytes; the 12-char nonce is the
        IV. Output is base64(ciphertext || auth_tag), matching the WebCrypto
        AES-GCM output format Flutterwave expects.
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = base64.b64decode(self.encryption_key)
        ct = AESGCM(key).encrypt(nonce.encode('utf-8'), str(plaintext).encode('utf-8'), None)
        return base64.b64encode(ct).decode('ascii')

    def _card_fields(self, card):
        """Encrypt raw card details into the v4 card object."""
        nonce = self._new_nonce()
        encrypted = {
            'nonce': nonce,
            'encrypted_card_number': self._encrypt(card['card_number'], nonce),
            'encrypted_expiry_month': self._encrypt(card['expiry_month'], nonce),
            'encrypted_expiry_year': self._encrypt(card['expiry_year'], nonce),
            'encrypted_cvv': self._encrypt(card['cvv'], nonce),
        }
        if card.get('card_holder_name'):
            encrypted['card_holder_name'] = str(card['card_holder_name'])
        if card.get('billing_address'):
            encrypted['billing_address'] = card['billing_address']
        return encrypted

    def _payment_method_in(self, payment_method):
        """Build the wire ``payment_method`` object, encrypting card fields."""
        pm = dict(payment_method or {})
        pm_type = pm.get('type', 'card')
        if pm_type == 'card' and isinstance(pm.get('card'), dict):
            pm['card'] = self._card_fields(pm['card'])
        return pm

    @staticmethod
    def _normalize_phone(phone, currency=None):
        """Normalize loose phone input into v4's ``{country_code, number}``.

        v4 schema: ``number`` is ``^[0-9]{7,10}$`` (national number WITHOUT
        the country code), ``country_code`` is ``^[0-9]{1,3}$`` digits only
        — both required when a phone object is sent. Accepts a raw string
        ('+234803…', '0803…', '803…') or a dict; splits an explicit
        international prefix, strips the domestic trunk '0', and fills a
        missing country code from the charge currency. Returns ``None``
        when nothing valid can be constructed — phone is optional on the
        customer object, so an unparseable value is dropped rather than
        producing a ``customer.phone.*`` 400 upstream.
        """
        cc = ''
        if isinstance(phone, dict):
            cc = re.sub(r'\D', '', str(phone.get('country_code') or ''))
            num = re.sub(r'\D', '', str(phone.get('number') or ''))
        else:
            num = re.sub(r'\D', '', str(phone or ''))
        # '00' is the international call prefix in most countries.
        if num.startswith('00'):
            num = num[2:]

        # An explicit international prefix beats any fallback: <cc><number>.
        # The remainder is checked trunk-stripped — '+2340803…' keeps the
        # domestic '0' after the country code.
        for code in _KNOWN_DIAL_CODES:
            if num.startswith(code):
                rest = num[len(code):].lstrip('0')
                if 7 <= len(rest) <= 10:
                    cc = code
                    num = rest
                    break
        # Domestic trunk zero ('0803…' in NG/GH) is not part of the number.
        num = num.lstrip('0')
        cc = cc.lstrip('0')
        if not cc:
            cc = CURRENCY_DIAL_CODES.get((currency or '').upper(), '')
        if re.fullmatch(r'[0-9]{1,3}', cc or '') and re.fullmatch(r'[0-9]{7,10}', num or ''):
            return {'country_code': cc, 'number': num}
        return None

    def _name_parts(self, name):
        """Format a loose name string/dict into v4's {'first': ..., 'last': ...} object.

        Flutterwave v4 requires both `first` and `last` to be between 2 and 50 characters,
        containing only letters, spaces, hyphens, apostrophes, commas, and periods.
        If a single word is provided, it is repeated for both parts (matching _customer_in).
        """
        if not name:
            return None
        if isinstance(name, dict):
            first = str(name.get('first') or '').strip()
            last = str(name.get('last') or '').strip()
        else:
            parts = str(name).strip().split(None, 1)
            first = parts[0] if parts else ''
            last = parts[1] if len(parts) > 1 else ''
        if not first and not last:
            return None
        if not last:
            last = first
        if not first:
            first = last
        # Strip characters not accepted by Flutterwave names
        first = re.sub(r"[^a-zA-Z0-9\s\-\'\,\.]", '', first).strip()
        last = re.sub(r"[^a-zA-Z0-9\s\-\'\,\.]", '', last).strip()
        if len(first) < 2:
            first = self.name_fallback_first
        if len(last) < 2:
            last = self.name_fallback_last
        return {'first': first[:50], 'last': last[:50]}

    def _customer_in(self, customer, currency=None):
        """Normalize a loose customer dict into the v4 customer object."""
        customer = dict(customer or {})
        name = customer.get('name')
        out = {'email': customer.get('email', '')}
        if name:
            norm_name = self._name_parts(name)
            if norm_name:
                out['name'] = norm_name
        phone = customer.get('phone') or customer.get('phone_number')
        if phone:
            normalized = self._normalize_phone(phone, currency)
            if normalized:
                out['phone'] = normalized
        if customer.get('address'):
            out['address'] = customer['address']
        if customer.get('meta'):
            out['meta'] = customer['meta']
        return out

    # ------------------------------------------------------------------
    # Charge normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _charge_amount_value(amount):
        """Amounts are major units; send ints when integral to keep JSON clean."""
        amt = Decimal(str(amount))
        return int(amt) if amt == amt.to_integral() else float(amt)

    def _normalize_charge(self, data, mock=False):
        """Flatten a v4 charge object into the dict the service layer consumes."""
        data = data or {}
        pm = data.get('payment_method_details') or data.get('payment_method') or {}
        card = pm.get('card') or {}
        customer = data.get('customer')
        customer_id = customer.get('id') if isinstance(customer, dict) else customer
        card_country = (
            card.get('issuer_country') or card.get('issuing_country')
            or card.get('country') or (card.get('billing_address') or {}).get('country')
        )
        return {
            'charge_id': data.get('id'),
            'status': (data.get('status') or '').lower(),  # pending|succeeded|failed
            'reference': data.get('reference'),
            'amount': data.get('amount'),
            'currency': data.get('currency'),
            'next_action': data.get('next_action'),
            'customer_id': customer_id,
            'customer_email': (
                (customer.get('email') if isinstance(customer, dict) else None)
                or (data.get('billing_details') or {}).get('email') or ''
            ),
            'payment_method_id': pm.get('id'),
            'payment_method_type': pm.get('type'),
            'card_country': (card_country or '').strip().upper() or None,
            'card_last4': card.get('last4'),
            'card_network': card.get('network'),
            'fees': self._extract_fees(data.get('fees')),
            'processor_response': data.get('processor_response'),
            'redirect_url': data.get('redirect_url'),
            'mock': mock,
        }

    @staticmethod
    def _extract_fees(fees):
        """Best-effort total of the v4 ``fees`` field, in major units.

        The field may be a list of ``{type, amount, currency}`` entries or a
        single object; anything unparseable yields None and callers fall back
        to the estimate.
        """
        if not fees:
            return None
        try:
            if isinstance(fees, list):
                total = sum(Decimal(str(f.get('amount', 0))) for f in fees if isinstance(f, dict))
                return total.quantize(Decimal('0.01'))
            if isinstance(fees, dict):
                if 'amount' in fees:
                    return Decimal(str(fees['amount'])).quantize(Decimal('0.01'))
                total = sum(Decimal(str(v)) for v in fees.values() if isinstance(v, (int, float, str)))
                return total.quantize(Decimal('0.01'))
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------
    # v3 (legacy) direct charge — FLWSECK auth + 3DES-ECB payload encryption
    # ------------------------------------------------------------------
    def _v3_headers(self):
        return {
            'Authorization': f'Bearer {self.v3_secret_key}',
            'Content-Type': 'application/json',
        }

    def _v3_request(self, method, url, **kwargs):
        """Execute a v3 API request — bearer secret key, no OAuth refresh."""
        if not self.v3_secret_key:
            raise FlutterwaveConfigurationError(
                'FLUTTERWAVE_V3_SECRET_KEY is required for live v3 operations. '
                'Set mock=True explicitly for local mock responses.')
        func = getattr(requests, method.lower(), requests.request)
        headers = self._v3_headers()
        return func(url, headers=headers, **kwargs) if func != requests.request \
            else requests.request(method, url, headers=headers, **kwargs)

    def _v3_encrypt(self, payload):
        """3DES-ECB encrypt a v3 charge payload (PKCS7 pad, base64 out).

        Every ``POST /v3/charges`` body is ``{"client": <ciphertext>}`` — the
        key is the dashboard "Encryption Key" from API settings. OpenSSL-style
        key handling: the raw key string truncated/padded to 24 bytes.
        """
        import json
        try:
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
        except ImportError:  # cryptography < 43 keeps it at the old path
            from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        if not self.v3_encryption_key:
            raise RuntimeError(
                'FLUTTERWAVE_V3_ENCRYPTION_KEY is required for v3 charges.')
        key = str(self.v3_encryption_key).encode('utf-8')[:24].ljust(24, b'\0')
        data = json.dumps(payload).encode('utf-8')
        pad = 8 - (len(data) % 8)
        data += bytes([pad]) * pad
        ct = Cipher(TripleDES(key), modes.ECB()).encryptor().update(data)
        return base64.b64encode(ct).decode('ascii')

    def _v3_charge_payload(self, *, amount, currency, reference, customer,
                           payment_method, redirect_url=None, authorization=None,
                           idempotency_key=None):
        """Build ``(charge_type, plaintext payload)`` for POST /v3/charges?type=...

        ``authorization`` is the v3 auth block added on PIN/AVS resubmits:
        ``{'mode': 'pin', 'pin': ...}`` or ``{'mode': 'avs_noauth', ...}``.

        ``tx_ref`` rides the per-attempt idempotency key: v3 has no idempotency
        header — it dedupes on tx_ref itself, so a retry after failure or an
        OTP resend must mint a fresh reference or the API rejects it as a
        duplicate. PIN/AVS resubmits deliberately pass the ORIGINAL attempt's
        tx_ref (via ``reference``) to continue the same charge.
        """
        customer = dict(customer or {})
        pm = dict(payment_method or {})
        pm_type = pm.get('type', 'card')
        currency = (currency or 'NGN').upper()

        name = customer.get('name')
        if isinstance(name, dict):
            name = ' '.join(p for p in (name.get('first'), name.get('last')) if p)
        name = (str(name or '').strip()
                or f'{self.name_fallback_first} {self.name_fallback_last}')
        phone = customer.get('phone') or customer.get('phone_number')
        if isinstance(phone, dict):
            phone = phone.get('number')
        phone = str(phone or '').strip()

        payload = {
            'tx_ref': str(idempotency_key or reference),
            'amount': self._charge_amount_value(amount),
            'currency': currency,
            'email': str(customer.get('email') or ''),
            'fullname': name,
        }
        if phone:
            payload['phone_number'] = phone
        if redirect_url:
            payload['redirect_url'] = redirect_url

        if pm_type == 'card':
            card = pm.get('card') or {}
            payload.update({
                'card_number': str(card.get('card_number') or '').replace(' ', ''),
                'cvv': str(card.get('cvv') or ''),
                'expiry_month': str(card.get('expiry_month') or ''),
                'expiry_year': str(card.get('expiry_year') or ''),
            })
            if authorization:
                payload['authorization'] = authorization
            charge_type = 'card'
        elif pm_type == 'bank_transfer':
            charge_type = 'bank_transfer'
        elif pm_type == 'ussd':
            ussd = pm.get('ussd') or {}
            payload['account_bank'] = str(ussd.get('account_bank') or '')
            charge_type = 'ussd'
        elif pm_type == 'mobile_money':
            mm = pm.get('mobile_money') or {}
            charge_type = V3_MOMO_CHARGE_TYPES.get(currency)
            if not charge_type:
                raise ValueError(
                    f'Mobile money is not available for {currency} on the v3 API.')
            if charge_type != 'mpesa' and mm.get('network'):
                payload['network'] = str(mm['network']).upper()
            mm_phone = str(mm.get('phone_number') or '').strip()
            if mm_phone:
                payload['phone_number'] = mm_phone
        else:
            raise ValueError(
                f"Payment method '{pm_type}' is not supported by the v3 charge API.")
        return charge_type, payload

    def _orchestrate_charge_v3(self, *, amount, currency, reference, customer,
                               payment_method, redirect_url=None, meta=None,
                               idempotency_key=None):
        """One-shot charge via ``POST /v3/charges?type=...`` (encrypted body)."""
        charge_type, payload = self._v3_charge_payload(
            amount=amount, currency=currency, reference=reference,
            customer=customer, payment_method=payment_method,
            redirect_url=redirect_url, idempotency_key=idempotency_key)
        resp = self._v3_request(
            'POST', f'{self.v3_base_url}/charges',
            params={'type': charge_type},
            json={'client': self._v3_encrypt(payload)},
            timeout=30,
        )
        self._raise_for_charge_error(resp)
        return self._normalize_charge_v3(resp.json().get('data') or {})

    @staticmethod
    def _v3_base_reference(tx_ref):
        """Map a v3 tx_ref back to the payment's provider_reference.

        Retried v3 charges mint ``{reference}try{n}`` tx_refs (v3 dedupes on
        tx_ref, so retries can't reuse the base). Our references are
        prefix+hex — the ``try`` literal can't occur naturally,
        so stripping a trailing ``try{digits}`` always recovers the base.
        """
        return re.sub(r'try\d+$', '', str(tx_ref or ''))

    def _authorize_charge_v3(self, charge_id, authorization, *, payment_method=None,
                             flw_ref=None, charge_context=None):
        """v3 authorization — OTP validates via /validate-charge; PIN/AVS
        resubmit the card charge with an ``authorization`` block."""
        auth = dict(authorization or {})
        auth_type = auth.get('type', '')
        if auth_type == 'otp':
            resp = self._v3_request(
                'POST', f'{self.v3_base_url}/validate-charge',
                json={
                    'otp': str(auth.get('code') or auth.get('otp') or ''),
                    'flw_ref': str(flw_ref or charge_id),
                },
                timeout=30,
            )
            self._raise_for_charge_error(resp)
            return self._normalize_charge_v3(resp.json().get('data') or {})

        if auth_type not in ('pin', 'avs'):
            raise ValueError(f"Unsupported v3 authorization type '{auth_type}'.")
        card = (payment_method or {}).get('card')
        if not card:
            raise ValueError('Card details are required to authorize this charge.')
        ctx = charge_context or {}
        v3_auth = (
            {'mode': 'pin', 'pin': str(auth.get('pin') or '')}
            if auth_type == 'pin' else {
                'mode': 'avs_noauth',
                **{k: str(v) for k, v in (auth.get('avs') or {}).items() if v is not None},
            }
        )
        charge_type, payload = self._v3_charge_payload(
            amount=ctx.get('amount'), currency=ctx.get('currency'),
            reference=ctx.get('reference'), customer=ctx.get('customer'),
            payment_method={'type': 'card', 'card': card},
            redirect_url=ctx.get('redirect_url'),
            authorization=v3_auth)
        resp = self._v3_request(
            'POST', f'{self.v3_base_url}/charges',
            params={'type': charge_type},
            json={'client': self._v3_encrypt(payload)},
            timeout=30,
        )
        self._raise_for_charge_error(resp)
        # After a PIN charge the pending state means "validate with OTP" —
        # the issuer's code is already on its way.
        return self._normalize_charge_v3(
            resp.json().get('data') or {}, expect_otp=(auth_type == 'pin'))

    @staticmethod
    def _v3_next_action(data, expect_otp=False):
        """Translate v3 ``meta.authorization`` into the v4 next_action shape
        the checkout router understands."""
        meta_auth = ((data.get('meta') or {}).get('authorization')) or {}
        mode = str(meta_auth.get('mode') or '').lower()
        transfer_account = meta_auth.get('transfer_account')
        note = meta_auth.get('note') or meta_auth.get('transfer_note')

        if meta_auth.get('redirect'):
            return {'type': 'redirect', 'redirect_url': str(meta_auth['redirect'])}
        if mode == 'pin':
            return {'type': 'requires_pin'}
        if mode in ('avs_noauth', 'avs'):
            return {
                'type': 'requires_additional_fields',
                'fields': meta_auth.get('fields')
                          or ['city', 'address', 'state', 'country', 'zipcode'],
            }
        if mode == 'otp' or expect_otp or data.get('auth_required') == 'otp':
            return {'type': 'requires_otp'}
        if transfer_account or mode == 'transfer':
            # v3 emits "YYYY-MM-DD HH:MM:SS" — browsers other than Chrome
            # (notably Safari) won't Date.parse the space form; emit ISO-T.
            expiry = meta_auth.get('account_expiration') or meta_auth.get('expiry_datetime')
            if isinstance(expiry, str):
                expiry = expiry.replace(' ', 'T')
            return {
                'type': 'requires_bank_transfer',
                'requires_bank_transfer': {
                    'account_number': transfer_account,
                    'account_bank_name': meta_auth.get('transfer_bank'),
                    'bank_name': meta_auth.get('transfer_bank'),
                    'account_expiration_datetime': expiry,
                    'amount': meta_auth.get('transfer_amount'),
                    'note': note,
                },
            }
        if note:
            return {'type': 'payment_instruction', 'payment_instruction': {'note': note}}
        return None

    def _normalize_charge_v3(self, data, mock=False, expect_otp=False):
        """Flatten a v3 charge/transaction object into the dict the service
        layer consumes — same keys ``_normalize_charge`` emits for v4."""
        data = data or {}
        card = data.get('card') or {}
        raw_status = str(data.get('status') or '').lower()
        status = {'successful': 'succeeded'}.get(raw_status, raw_status)
        pm_type = data.get('payment_type') or data.get('payment_method')
        pm_type = str(pm_type or '')
        if 'mobile' in pm_type or pm_type == 'mpesa':
            pm_type = 'mobile_money'
        elif pm_type == 'banktransfer':
            pm_type = 'bank_transfer'
        customer = data.get('customer')
        customer_id = customer.get('id') if isinstance(customer, dict) else None
        return {
            'charge_id': str(data.get('id') or data.get('flw_ref') or ''),
            # validate-charge needs flw_ref — kept alongside charge_id.
            'flw_ref': data.get('flw_ref'),
            'status': status,  # pending|succeeded|failed
            # tx_ref may carry the retry suffix — report the base reference.
            'reference': self._v3_base_reference(
                data.get('tx_ref') or data.get('reference')),
            'amount': data.get('charged_amount') or data.get('amount'),
            'currency': data.get('currency'),
            'next_action': self._v3_next_action(data, expect_otp=expect_otp),
            'customer_id': customer_id,
            # v3 card.token powers recurring /tokenized-charges for subs.
            'payment_method_id': card.get('token'),
            'payment_method_type': pm_type or None,
            'card_country': (card.get('country') or '').strip().upper() or None,
            'card_last4': card.get('last_4digits'),
            'card_network': card.get('type'),
            'fees': data.get('app_fee'),
            'processor_response': data.get('processor_response'),
            'redirect_url': ((data.get('meta') or {}).get('authorization') or {}).get('redirect'),
            'mock': mock,
        }

    # ------------------------------------------------------------------
    # Collection — charge lifecycle (v4 default; v3 behind FLW_CHARGE_API)
    # ------------------------------------------------------------------
    def create_hosted_checkout(self, *, reference, amount, currency, email,
                               redirect_url, name='', phone='', metadata=None,
                               payment_plan=None):
        """Create a Flutterwave Standard hosted checkout link.

        The hosted checkout API is v3 even when direct charges use v4. The
        caller must persist the reference and expected amount before calling.
        """
        if not self.v3_secret_key and not self.mock:
            raise FlutterwaveConfigurationError(
                'FLUTTERWAVE_V3_SECRET_KEY is required for hosted checkout.')
        if self.mock:
            return {'reference': reference, 'link':
                    f'https://checkout.flutterwave.com/mock/{reference}', 'mock': True}
        payload = {
            'tx_ref': str(reference),
            'amount': str(Decimal(str(amount))),
            'currency': str(currency).upper(),
            'redirect_url': redirect_url,
            'customer': {'email': email, 'name': name, 'phonenumber': phone},
            'meta': metadata or {},
        }
        if payment_plan:
            payload['payment_plan'] = payment_plan
        resp = self._v3_request('POST', f'{self.v3_base_url}/payments',
                                json=payload, timeout=30)
        self._raise_for_charge_error(resp)
        body = resp.json()
        link = (body.get('data') or {}).get('link')
        if body.get('status') != 'success' or not link:
            raise RuntimeError('Flutterwave did not return a checkout link.')
        return {'reference': str(reference), 'link': link, 'mock': False}

    def create_payment_plan(self, *, name, amount, currency, interval):
        """Create a v3 recurring payment plan for an immutable price variant."""
        if self.mock:
            raise FlutterwaveConfigurationError('A mock plan cannot authorize recurring billing.')
        resp = self._v3_request(
            'POST', f'{self.v3_base_url}/payment-plans',
            json={'name': name, 'amount': str(Decimal(str(amount))),
                  'currency': currency, 'interval': interval}, timeout=30)
        self._raise_for_charge_error(resp)
        body = resp.json()
        data = body.get('data') or {}
        if body.get('status') != 'success' or not data.get('id'):
            raise RuntimeError('Flutterwave did not return a payment plan ID.')
        return str(data['id'])

    def get_subscription_for_transaction(self, transaction_id):
        """Find the subscription created by a verified first charge."""
        resp = self._v3_request(
            'GET', f'{self.v3_base_url}/subscriptions',
            params={'transaction_id': str(transaction_id)}, timeout=15)
        self._raise_for_charge_error(resp)
        body = resp.json()
        if body.get('status') != 'success':
            raise RuntimeError('Flutterwave subscription lookup failed.')
        rows = body.get('data') or []
        if isinstance(rows, dict):
            rows = [rows]
        if len(rows) != 1:
            raise RuntimeError('Expected exactly one subscription for the charge.')
        return rows[0]

    def cancel_subscription(self, subscription_id):
        """Cancel one v3 recurring mandate; keep local access until verified."""
        resp = self._v3_request(
            'PUT', f'{self.v3_base_url}/subscriptions/{subscription_id}/cancel',
            timeout=20)
        self._raise_for_charge_error(resp)
        body = resp.json()
        if body.get('status') != 'success':
            raise RuntimeError('Flutterwave did not confirm subscription cancellation.')
        data = body.get('data') or {}
        if str(data.get('id') or '') != str(subscription_id) or str(data.get('status') or '').lower() not in ('cancelled', 'canceled', 'deactivated'):
            raise RuntimeError('Flutterwave cancellation identity or state is unconfirmed.')
        return data

    def verify_hosted_checkout(self, transaction_id):
        """Verify a Standard checkout by provider transaction ID."""
        if self.mock:
            raise FlutterwaveConfigurationError(
                'A mock checkout cannot verify a financial payment.')
        resp = self._v3_request(
            'GET', f'{self.v3_base_url}/transactions/{transaction_id}/verify',
            timeout=15)
        self._raise_for_charge_error(resp)
        data = resp.json().get('data') or {}
        currency = data.get('currency') or ''
        # charged_amount can include processor fees; the merchant's agreed
        # order amount is the transaction amount returned by verification.
        amount = data.get('amount')
        customer = data.get('customer') or {}
        return {
            'status': str(data.get('status') or '').lower(),
            'reference': str(data.get('tx_ref') or ''),
            'transaction_id': str(data.get('id') or transaction_id),
            'amount_minor': _to_minor_units(amount, currency) if amount is not None else None,
            'currency': currency,
            'customer_email': customer.get('email') or '',
            'raw': data,
        }

    def find_hosted_checkout(self, reference):
        """Look up a v3 Standard charge by our merchant reference."""
        if self.mock:
            raise FlutterwaveConfigurationError(
                'A mock checkout cannot verify a financial payment.')
        resp = self._v3_request(
            'GET', f'{self.v3_base_url}/transactions/verify_by_reference',
            params={'tx_ref': reference}, timeout=15)
        if resp.status_code == 404:
            return None
        self._raise_for_charge_error(resp)
        body = resp.json()
        data = body.get('data') or {}
        if body.get('status') != 'success' or not data.get('id'):
            return None
        if str(data.get('tx_ref') or '') != str(reference):
            raise RuntimeError('Flutterwave returned a different transaction reference.')
        return str(data['id'])

    def refund_hosted_checkout(self, transaction_id, *, amount, reason=''):
        """Initiate a v3 Standard charge refund using its v3 transaction ID."""
        if self.mock:
            raise FlutterwaveConfigurationError(
                'A mock checkout cannot create a financial refund.')
        payload = {'amount': str(Decimal(str(amount))), 'comments': reason}
        resp = self._v3_request(
            'POST', f'{self.v3_base_url}/transactions/{transaction_id}/refund',
            json=payload, timeout=30)
        self._raise_for_charge_error(resp)
        body = resp.json()
        data = body.get('data') or {}
        if body.get('status') != 'success' or not data.get('id'):
            raise RuntimeError('Flutterwave did not confirm refund initiation.')
        return {
            'refund_id': str(data['id']),
            'transaction_id': str(data.get('tx_id') or transaction_id),
            'amount': Decimal(str(data.get('amount_refunded') or amount)),
            'status': str(data.get('status') or 'processing').lower(),
        }

    def list_hosted_refunds(self, transaction_id):
        """Read v3 refunds linked to one hosted transaction."""
        if self.mock:
            raise FlutterwaveConfigurationError(
                'A mock checkout cannot verify a financial refund.')
        resp = self._v3_request(
            'GET', f'{self.v3_base_url}/refunds',
            params={'id': str(transaction_id)}, timeout=15)
        self._raise_for_charge_error(resp)
        body = resp.json()
        if body.get('status') != 'success':
            raise RuntimeError('Flutterwave refund lookup failed.')
        rows = body.get('data') or []
        if isinstance(rows, dict):
            rows = [rows]
        return [
            {
                'refund_id': str(row.get('id') or ''),
                'transaction_id': str(row.get('tx_id') or row.get('TransactionId') or ''),
                'amount': Decimal(str(row.get('amount_refunded') or row.get('AmountRefunded') or '0')),
                'status': str(row.get('status') or '').lower(),
            }
            for row in rows if isinstance(row, dict)
            and str(row.get('tx_id') or row.get('TransactionId') or '') == str(transaction_id)
        ]

    def initialize_transaction(self, *, email, amount, currency='NGN', reference=None, metadata=None):
        """Prepare a collection. v4 has no server-side "initialize" call — the
        charge is created when the customer submits payment details — so this
        only mints the reference and reports mode/amounts for the response
        envelope."""
        ref = reference or f'{self.reference_prefix}flw{secrets.token_hex(10)}'
        return {
            'reference': ref,
            'mock': self.mock,
            'charged_amount': int(amount),
            'charged_currency': currency,
        }

    def orchestrate_charge(self, *, amount, currency, reference, customer, payment_method,
                           redirect_url=None, meta=None, idempotency_key=None):
        """One-shot charge via ``POST /orchestration/direct-charges``.

        Bundles customer + payment method + charge in a single request — the
        recommended flow for one-time payments. When ``FLW_CHARGE_API=v3`` the
        same call is served by the legacy v3 encrypted-charge endpoint
        instead; the normalized return shape is identical either way.
        """
        if self.mock:
            return self._normalize_charge({
                'id': f'chg_mock_{secrets.token_hex(8)}',
                'status': 'succeeded',
                'reference': reference,
                'amount': float(amount),
                'currency': currency,
            }, mock=True)
        if self.charge_api == 'v3':
            return self._orchestrate_charge_v3(
                amount=amount, currency=currency, reference=reference,
                customer=customer, payment_method=payment_method,
                redirect_url=redirect_url, meta=meta,
                idempotency_key=idempotency_key)

        payload = {
            'amount': self._charge_amount_value(amount),
            'currency': (currency or 'NGN').upper(),
            'reference': reference,
            'customer': self._customer_in(customer, currency),
            'payment_method': self._payment_method_in(payment_method),
            'meta': meta or {},
        }
        if redirect_url:
            payload['redirect_url'] = redirect_url
        resp = self._v4_request(
            'POST',
            f'{self.v4_base_url}/orchestration/direct-charges',
            json=payload,
            idempotency_key=idempotency_key or reference,
            timeout=30,
        )
        self._raise_for_charge_error(resp)
        return self._normalize_charge(resp.json().get('data', {}))

    def find_customer_by_email(self, email):
        """Search for a customer by email via v4 POST /customers/search or GET /customers."""
        if self.mock or not email:
            return None
        norm_email = str(email).strip().lower()
        # 1. Try POST /customers/search
        try:
            resp = self._v4_request(
                'POST', f'{self.v4_base_url}/customers/search',
                json={'email': norm_email}, timeout=15,
            )
            if resp.status_code == 200:
                body = resp.json()
                data = body.get('data')
                if isinstance(data, list) and data:
                    for item in data:
                        if isinstance(item, dict) and item.get('id'):
                            item_email = str(item.get('email') or '').strip().lower()
                            if item_email == norm_email:
                                return {'customer_id': item['id'], 'mock': False, 'raw': item}
                elif isinstance(data, dict):
                    cust_id = data.get('id') or data.get('customer_id')
                    item_email = str(data.get('email') or '').strip().lower()
                    if cust_id and item_email == norm_email:
                        return {'customer_id': cust_id, 'mock': False, 'raw': data}
                    customers = data.get('customers') or data.get('results') or []
                    if isinstance(customers, list) and customers:
                        for item in customers:
                            if not isinstance(item, dict):
                                continue
                            item_email = str(
                                item.get('email') or '').strip().lower()
                            if item.get('id') and item_email == norm_email:
                                return {
                                    'customer_id': item['id'],
                                    'mock': False,
                                    'raw': item,
                                }
        except Exception as exc:
            logger.info("Flutterwave POST /customers/search failed for %s: %s", email, exc)

        # 2. Try GET /customers with email filter or list
        try:
            resp = self._v4_request(
                'GET', f'{self.v4_base_url}/customers',
                params={'email': norm_email, 'size': 50}, timeout=15,
            )
            if resp.status_code == 200:
                body = resp.json()
                data = body.get('data') or []
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and str(item.get('email') or '').strip().lower() == norm_email:
                            if item.get('id'):
                                return {'customer_id': item['id'], 'mock': False, 'raw': item}
        except Exception as exc:
            logger.info("Flutterwave GET /customers fallback failed for %s: %s", email, exc)

        return None

    def create_customer(self, *, email, name=None, phone=None, address=None, meta=None):
        """Create a v4 customer (``POST /customers``). Only email is required."""
        if self.mock:
            return {'customer_id': f'cus_mock_{secrets.token_hex(6)}', 'mock': True}
        customer = self._customer_in({
            'email': email, 'name': name, 'phone': phone, 'address': address, 'meta': meta,
        })
        resp = self._v4_request(
            'POST', f'{self.v4_base_url}/customers',
            json=customer, idempotency_key=f'customer_{email}', timeout=15,
        )
        if resp.status_code >= 400:
            # Handle conflict / already existing customer gracefully
            err_text = ''
            try:
                body = resp.json()
                data = body.get('data') or {}
                if isinstance(data, dict) and (data.get('id') or data.get('customer_id')):
                    return {'customer_id': data.get('id') or data.get('customer_id'), 'mock': False, 'raw': data}
                err = body.get('error') or {}
                err_text = str(err.get('message') or body.get('message') or err or '').lower()
            except Exception:
                pass

            if 'exist' in err_text or resp.status_code == 409:
                existing = self.find_customer_by_email(email)
                if existing and existing.get('customer_id'):
                    return existing

            self._raise_for_charge_error(resp)

        data = resp.json().get('data', {})
        return {'customer_id': data.get('id'), 'mock': False, 'raw': data}

    def create_payment_method(self, *, pm_type='card', customer_id=None, card=None,
                              mobile_money=None, ussd=None, meta=None,
                              idempotency_key=None):
        """Create a reusable v4 payment method (``POST /payment-methods``).

        For cards, ``card`` is a raw dict — the sensitive fields are encrypted
        here before transmission. Pass a deterministic ``idempotency_key``
        (e.g. derived from customer+card) so retries replay instead of
        tokenizing the same card twice.
        """
        if self.mock:
            return {
                'payment_method_id': f'pmd_mock_{secrets.token_hex(6)}',
                'type': pm_type,
                'card': {'last4': str((card or {}).get('card_number', '0000'))[-4:], 'network': 'mock'},
                'mock': True,
            }
        pm_in = {'type': pm_type}
        if pm_type == 'card':
            pm_in['card'] = self._card_fields(card or {})
        elif pm_type == 'mobile_money' and mobile_money:
            pm_in['mobile_money'] = mobile_money
        elif pm_type == 'ussd' and ussd:
            pm_in['ussd'] = ussd
        if customer_id:
            pm_in['customer_id'] = customer_id
        if meta:
            pm_in['meta'] = meta
        resp = self._v4_request(
            'POST', f'{self.v4_base_url}/payment-methods',
            json=pm_in, idempotency_key=idempotency_key or uuid.uuid4().hex, timeout=30,
        )
        self._raise_for_charge_error(resp)
        data = resp.json().get('data', {})
        return {
            'payment_method_id': data.get('id'),
            'type': data.get('type', pm_type),
            'card': data.get('card') or {},
            'mock': False,
            'raw': data,
        }

    def create_charge(self, *, amount, currency, reference, customer_id=None,
                      payment_method_id=None, redirect_url=None, meta=None, recurring=False,
                      idempotency_key=None, customer_email=None):
        """Initiate a charge via ``POST /charges`` (general flow).

        Pass ``recurring=True`` for merchant-initiated renewals against a stored
        ``payment_method_id``. On v3 the same call maps to
        ``POST /tokenized-charges`` where ``payment_method_id`` is the card
        token captured at the initial charge.
        """
        if self.mock:
            return self._normalize_charge({
                'id': f'chg_mock_{secrets.token_hex(8)}',
                'status': 'succeeded',
                'reference': reference,
                'amount': float(amount),
                'currency': currency,
                'customer': customer_id,
                'payment_method': {'id': payment_method_id, 'type': 'card'},
            }, mock=True)
        if self.charge_api == 'v3':
            if not payment_method_id:
                raise ValueError(
                    'v3 recurring charges need a stored card token '
                    '(payment_method_id).')
            currency = (currency or 'NGN').upper()
            resp = self._v3_request(
                'POST', f'{self.v3_base_url}/tokenized-charges',
                json={
                    'token': payment_method_id,
                    'currency': currency,
                    'country': V3_CURRENCY_COUNTRY.get(currency, 'NG'),
                    'amount': self._charge_amount_value(amount),
                    'email': customer_email or '',
                    'tx_ref': str(reference),
                    'narration': f'{self.brand_name} subscription renewal',
                },
                timeout=30,
            )
            self._raise_for_charge_error(resp)
            return self._normalize_charge_v3(resp.json().get('data') or {})

        payload = {
            'amount': self._charge_amount_value(amount),
            'currency': (currency or 'NGN').upper(),
            'reference': reference,
            'meta': meta or {},
        }
        if customer_id:
            payload['customer_id'] = customer_id
        if payment_method_id:
            payload['payment_method_id'] = payment_method_id
        if redirect_url:
            payload['redirect_url'] = redirect_url
        if recurring:
            payload['recurring'] = True
        resp = self._v4_request(
            'POST', f'{self.v4_base_url}/charges',
            json=payload, idempotency_key=idempotency_key or reference, timeout=30,
        )
        self._raise_for_charge_error(resp)
        return self._normalize_charge(resp.json().get('data', {}))

    def authorize_charge(self, charge_id, authorization, *, payment_method=None,
                         flw_ref=None, charge_context=None):
        """Authorize a pending charge (``PUT /charges/{id}``).

        ``authorization`` uses raw values: ``{'type': 'pin', 'pin': '1234'}``,
        ``{'type': 'otp', 'code': '123456'}``, or
        ``{'type': 'avs', 'avs': {...}}``. PINs are encrypted here.

        On v3 the extra kwargs come into play: OTP calls
        ``POST /validate-charge`` against ``flw_ref``; PIN/AVS resubmit the
        card charge, so ``payment_method`` (the raw card) and
        ``charge_context`` (amount/currency/reference/customer) are required.
        """
        if self.mock:
            return self._normalize_charge({
                'id': charge_id, 'status': 'succeeded',
            }, mock=True)
        if self.charge_api == 'v3':
            return self._authorize_charge_v3(
                charge_id, authorization,
                payment_method=payment_method, flw_ref=flw_ref,
                charge_context=charge_context)

        auth_type = (authorization or {}).get('type', '')
        if auth_type == 'pin':
            nonce = self._new_nonce()
            auth_body = {
                'type': 'pin',
                'pin': {'nonce': nonce, 'encrypted_pin': self._encrypt(authorization['pin'], nonce)},
            }
        elif auth_type == 'otp':
            auth_body = {'type': 'otp', 'otp': {'code': str(authorization.get('code') or authorization.get('otp') or '')}}
        elif auth_type == 'avs':
            auth_body = {'type': 'avs', 'avs': authorization.get('avs') or {}}
        else:
            auth_body = authorization or {}

        resp = self._v4_request(
            'PUT', f'{self.v4_base_url}/charges/{charge_id}',
            json={'authorization': auth_body},
            idempotency_key=f'auth_{charge_id}_{auth_type}_{secrets.token_hex(4)}',
            timeout=30,
        )
        self._raise_for_charge_error(resp)
        return self._normalize_charge(resp.json().get('data', {}))

    def get_charge(self, charge_id):
        """Retrieve a charge (``GET /charges/{id}``; v3 verifies the
        transaction by id, which returns the same fields)."""
        if self.mock:
            return self._normalize_charge({
                'id': charge_id, 'status': 'succeeded',
            }, mock=True)
        if self.charge_api == 'v3':
            resp = self._v3_request(
                'GET', f'{self.v3_base_url}/transactions/{charge_id}/verify',
                timeout=15,
            )
            self._raise_for_charge_error(resp)
            return self._normalize_charge_v3(resp.json().get('data') or {})
        resp = self._v4_request(
            'GET', f'{self.v4_base_url}/charges/{charge_id}', timeout=15,
        )
        self._raise_for_charge_error(resp)
        return self._normalize_charge(resp.json().get('data', {}))

    def find_charge_by_reference(self, reference):
        """Recover a v4 charge ID after an initiation response was lost."""
        resp = self._v4_request(
            'GET', f'{self.v4_base_url}/charges',
            params={'reference': reference}, timeout=15,
        )
        self._raise_for_charge_error(resp)
        body = resp.json()
        if body.get('status') != 'success':
            raise RuntimeError('Flutterwave charge reference lookup failed.')
        data = body.get('data') or []
        if isinstance(data, dict):
            data = data.get('charges') or data.get('results') or [data]
        matches = [row for row in data if isinstance(row, dict)
                   and str(row.get('reference') or '') == str(reference)]
        if len(matches) > 1:
            raise RuntimeError('Multiple Flutterwave charges share one reference.')
        return str(matches[0]['id']) if matches else None

    def verify_transaction(self, transaction_id, reference=None):
        """Verify a collected charge.

        ``transaction_id`` is the v4 charge id (``chg_...``). The returned
        dict keeps the legacy normalized contract the services and tests
        consume: status 'success', amount/fees in minor units.
        """
        if self.mock or (self._is_debug()
                         and str(transaction_id).startswith(
                             (self.reference_prefix, 'chg_mock'))):
            return {
                'status': 'success',
                'reference': reference or str(transaction_id),
                'amount': 0,
                'currency': 'NGN',
                'customer_email': 'mock@example.com',
                'transaction_id': transaction_id,
                'mock': True,
            }

        if self.charge_api == 'v3':
            # GET /transactions/{id}/verify — id is the numeric transaction id
            # stored as charge_id from the original charge response.
            resp = self._v3_request(
                'GET', f'{self.v3_base_url}/transactions/{transaction_id}/verify',
                timeout=15,
            )
            self._raise_for_charge_error(resp)
            data = resp.json().get('data') or {}
            status_str = str(data.get('status') or '').lower()
            normalized = (
                'success' if status_str in ('successful', 'succeeded', 'success')
                else (status_str or 'failed'))
            currency = data.get('currency') or 'NGN'
            amount = data.get('charged_amount') or data.get('amount')
            card = data.get('card') or {}
            v3_customer = data.get('customer') or {}
            fee = data.get('app_fee')
            return {
                'status': normalized,
                'reference': self._v3_base_reference(
                    data.get('tx_ref')) or reference or '',
                'amount': _to_minor_units(amount, currency) if amount is not None else 0,
                'currency': currency,
                'customer_email': v3_customer.get('email') or '',
                'transaction_id': transaction_id,
                'fees': _to_minor_units(fee, currency) if fee is not None else None,
                'card_country': (card.get('country') or '').strip().upper() or None,
                'card_token': card.get('token'),
                'card_last4': card.get('last_4digits'),
                'card_network': card.get('type'),
                'mock': False,
            }

        charge = self.get_charge(transaction_id)
        status_str = charge['status']
        normalized = 'success' if status_str in ('succeeded', 'successful', 'success') else (status_str or 'failed')
        currency = charge.get('currency') or 'NGN'
        amount = charge.get('amount')
        fees = charge.get('fees')
        return {
            'status': normalized,
            'reference': charge.get('reference') or reference or '',
            'amount': _to_minor_units(amount, currency) if amount is not None else 0,
            'currency': currency,
            'customer_email': charge.get('customer_email') or '',
            'customer_id': charge.get('customer_id') or '',
            'payment_method_id': charge.get('payment_method_id') or '',
            'transaction_id': transaction_id,
            'fees': _to_minor_units(fees, currency) if fees is not None else None,
            'card_country': charge.get('card_country'),
            'mock': False,
        }

    # ------------------------------------------------------------------
    # Virtual accounts (pay-with-bank-transfer)
    # ------------------------------------------------------------------
    def create_virtual_account(self, *, customer_id, amount, currency='NGN',
                               reference=None, narration=None, expiry_minutes=None):
        """Create a dynamic virtual account for bank-transfer collection
        (``POST /virtual-accounts``)."""
        if narration is None:
            narration = f'{self.brand_name} tip'
        if self.mock:
            exp = (datetime.now(timezone.utc)
                   + timedelta(minutes=int(expiry_minutes or 60))).isoformat()
            return {
                'account_id': f'van_mock_{secrets.token_hex(6)}',
                'account_number': '9000000012',
                'bank_name': 'Mock Bank',
                'amount': float(amount),
                'currency': currency,
                'reference': reference,
                'expiry_datetime': exp,
                'mock': True,
            }
        payload = {
            'currency': (currency or 'NGN').upper(),
            'amount': self._charge_amount_value(amount),
            'customer_id': customer_id,
            'reference': reference or f'{self.reference_prefix}va{secrets.token_hex(8)}',
            'narration': narration,
            'account_type': 'dynamic',
        }
        if expiry_minutes:
            # v4 `expiry` is SECONDS (max 31536000, default 3600) — sending the
            # minutes value raw produced accounts that died in ~60 seconds.
            payload['expiry'] = int(expiry_minutes) * 60
        resp = self._v4_request(
            'POST', f'{self.v4_base_url}/virtual-accounts',
            json=payload, idempotency_key=payload['reference'], timeout=30,
        )
        self._raise_for_charge_error(resp)
        data = resp.json().get('data', {})
        return {
            'account_id': data.get('id'),
            'account_number': data.get('account_number'),
            'bank_name': data.get('account_bank_name') or data.get('bank_name'),
            'amount': data.get('amount'),
            'currency': data.get('currency'),
            'reference': data.get('reference') or payload['reference'],
            'expiry_datetime': data.get('account_expiration_datetime') or data.get('expiry_datetime'),
            'mock': False,
        }

    # ------------------------------------------------------------------
    # Banks & account resolution (v4)
    # ------------------------------------------------------------------
    def list_banks(self, country='NG'):
        """List banks for a country (v4 ``GET /banks?country=XX``)."""
        if self.payout_mock:
            return [
                {'id': 'bnk_044', 'code': '044', 'name': 'Access Bank'},
                {'id': 'bnk_057', 'code': '057', 'name': 'Zenith Bank'},
                {'id': 'bnk_058', 'code': '058', 'name': 'GTBank'},
                {'id': 'bnk_011', 'code': '011', 'name': 'First Bank of Nigeria'},
                {'id': 'bnk_033', 'code': '033', 'name': 'United Bank for Africa'},
                {'id': 'bnk_232', 'code': '232', 'name': 'Sterling Bank'},
                {'id': 'bnk_070', 'code': '070', 'name': 'Fidelity Bank'},
                {'id': 'bnk_035', 'code': '035', 'name': 'Wema Bank'},
            ]

        resp = self._v4_request(
            'GET',
            f'{self.v4_base_url}/banks',
            params={'country': country},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get('data', [])
        return [
            {
                'id': str(b.get('id') or ''),
                'code': str(b.get('code') or b.get('id') or ''),
                'name': b.get('name', ''),
            }
            for b in data
        ]

    def list_bank_branches(self, bank_id):
        """List branch codes for a bank (v4 ``GET /banks/{id}/branches``)."""
        if self.payout_mock:
            return [
                {'id': 'br_001', 'code': '001', 'name': 'Head Office / Main Branch'},
                {'id': 'br_002', 'code': '002', 'name': 'City Branch'},
            ]
        resp = self._v4_request(
            'GET',
            f'{self.v4_base_url}/banks/{bank_id}/branches',
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get('data', [])

    def resolve_account(self, account_number, bank_code, country='NG'):
        """Resolve an account number to the holder name.

        v4 ``POST /banks/account-resolve`` — a real name enquiry across
        supported corridors (the v3 API lacked a direct equivalent).
        """
        if self.payout_mock:
            return {
                'account_number': account_number,
                'account_name': f'MOCK ACCOUNT {account_number[-4:]}',
                'bank_code': bank_code,
                'mock': True,
            }

        currency = SUPPORTED_PAYOUT_COUNTRIES.get(country, {}).get('currency', 'NGN')
        idempotency_key = f'resolve_{country}_{bank_code}_{account_number}'
        try:
            resp = self._v4_request(
                'POST',
                f'{self.v4_base_url}/banks/account-resolve',
                json={'account': {'code': bank_code, 'number': account_number}, 'currency': currency},
                idempotency_key=idempotency_key,
                timeout=15,
            )
            self._raise_for_charge_error(resp)
            data = resp.json().get('data', {})
            return {
                'account_number': data.get('account_number') or account_number,
                'account_name': data.get('account_name', ''),
                'bank_code': data.get('bank_code') or bank_code,
                'mock': False,
            }
        except requests.exceptions.RequestException as exc:
            logger.warning("Flutterwave account-resolve failed: %s", exc)
            # Name enquiry unavailable — caller proceeds with client-supplied name.
            return {
                'account_number': account_number,
                'account_name': '',
                'bank_code': bank_code,
                'mock': False,
                'name_enquiry_unavailable': True,
            }

    # ------------------------------------------------------------------
    # Transfers / payouts (v4 direct-transfers)
    # ------------------------------------------------------------------
    def get_wallet_balances(self):
        """Fetch the payout-balance float per currency (v4 ``GET
        /wallets/balances``).

        Returns ``{currency: Decimal(available)}`` — the spendable balance
        transfers actually debit. The response shape is defensive-parsed:
        ``data`` may be a list of balance objects or a currency map, and the
        amount may live under ``available_balance``/``balance``/``amount``.
        """
        if self.payout_mock:
            return {}
        resp = self._v4_request(
            'GET', f'{self.v4_base_url}/wallets/balances', timeout=15)
        resp.raise_for_status()
        data = resp.json().get('data') or []

        def _amount(row):
            for key in ('available_balance', 'available', 'balance', 'amount'):
                val = row.get(key)
                if val is not None:
                    try:
                        return Decimal(str(val))
                    except (InvalidOperation, TypeError, ValueError):
                        continue
            return None

        balances = {}
        if isinstance(data, dict):
            items = [
                {'currency': c, 'balance': v} for c, v in data.items()
                if isinstance(v, (int, float, str, Decimal))
            ] or list(data.values())
        else:
            items = data
        for row in items:
            if not isinstance(row, dict):
                continue
            currency = (row.get('currency') or '').upper()
            amount = _amount(row)
            if currency and amount is not None:
                balances[currency] = amount
        return balances

    def get_wallet_statement(self, *, currency=None, from_date=None,
                             to_date=None, size=50, cursor=None):
        """Wallet transaction history (v4 ``GET /wallets/statement``).

        Returns the raw row dicts — the statement schema is not fully
        documented, so callers pick fields defensively (``tx_hash``/
        ``payment_information.proof``/``reference``/nested ``amount``).
        Rows are the platform's own book: matching an on-chain hash here
        means the funds actually landed in the float, unlike a raw chain
        lookup which says nothing about what Flutterwave credited.
        """
        if self.payout_mock:
            return []
        params = {'size': size}
        if currency:
            params['query_currency'] = str(currency).upper()
        if from_date:
            params['from_date'] = str(from_date)
        if to_date:
            params['to_date'] = str(to_date)
        if cursor:
            params['cursor_next'] = cursor
        resp = self._v4_request(
            'GET', f'{self.v4_base_url}/wallets/statement',
            params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json().get('data')
        if isinstance(data, dict):
            for key in ('transactions', 'results', 'items', 'statement', 'rows'):
                rows = data.get(key)
                if isinstance(rows, list):
                    return rows
            return [data]
        return data if isinstance(data, list) else []

    def list_settlements(self, *, from_dt=None, to_dt=None, size=50,
                         max_pages=20):
        """Settlement batches in a window (v4 ``GET /settlements``).

        A settlement is the provider-side disbursement of collected charges
        to the merchant destination — the event the wallet's settlement
        holds are actually waiting on. Returns the raw settlement dicts
        (``id``, ``status``, ``due_datetime``, ``charge_count``, …) across
        every page in the window; the per-charge linkage lives on
        ``GET /settlements/{id}`` (see get_settlement).
        """
        if self.payout_mock:
            return []
        params = {'size': max(10, min(int(size), 50))}
        if from_dt is not None:
            params['from'] = from_dt.isoformat()
        if to_dt is not None:
            params['to'] = to_dt.isoformat()
        settlements = []
        for page in range(1, int(max_pages) + 1):
            params['page'] = page
            resp = self._v4_request(
                'GET', f'{self.v4_base_url}/settlements',
                params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json().get('data')
            rows = data if isinstance(data, list) else ([data] if data else [])
            settlements.extend(r for r in rows if isinstance(r, dict))
            if len(rows) < params['size']:
                break
        return settlements

    def get_settlement(self, settlement_id, *, size=50, max_pages=20):
        """One settlement with its member charges (v4 ``GET /settlements/{id}``).

        The ``charges`` list is paginated on the detail endpoint — pages are
        walked until ``charge_count`` is covered or a short page ends the
        list. Returns the settlement dict with ``charges`` replaced by the
        full collected list.
        """
        if self.payout_mock:
            return {}
        base = {'size': max(10, min(int(size), 50))}
        settlement = None
        charges = []
        for page in range(1, int(max_pages) + 1):
            params = dict(base, page=page)
            resp = self._v4_request(
                'GET', f'{self.v4_base_url}/settlements/{settlement_id}',
                params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json().get('data') or {}
            if settlement is None:
                settlement = data
            rows = data.get('charges') or []
            charges.extend(r for r in rows if isinstance(r, dict))
            try:
                total = int(data.get('charge_count') or 0)
            except (TypeError, ValueError):
                total = 0
            if len(charges) >= total or len(rows) < base['size']:
                break
        if settlement is None:
            return {}
        settlement['charges'] = charges
        return settlement

    def get_crypto_transfer_fee(self, *, amount, currency, network=None):
        """Live provider fee for a stablecoin send (v3 ``GET /transfers/fee``
        — the documented quote surface for crypto transfers; the v4 API
        currently has no equivalent).

        Returns ``Decimal`` fee in the token currency, or ``None`` when the
        quote is unavailable (no v3 key configured, provider unreachable) —
        callers fall back to the flat $1.50 rate card figure.
        """
        if self.payout_mock:
            return Decimal('1.50')
        if not self.v3_secret_key:
            return None
        params = {
            'amount': str(amount),
            'currency': str(currency).upper(),
            'type': 'crypto',
        }
        if network:
            params['network'] = str(network).upper()
        try:
            resp = self._v3_request(
                'get', f'{self.v3_base_url}/transfers/fee',
                params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json().get('data')
            rows = data if isinstance(data, list) else [data]
            for row in rows:
                if isinstance(row, dict) and row.get('fee') is not None:
                    return Decimal(str(row['fee']))
        except Exception as exc:
            logger.warning("Flutterwave crypto transfer-fee quote failed: %s", exc)
        return None

    def get_transfer_rate(self, *, source_currency, destination_currency, amount):
        """Real-time FX quote for a cross-currency transfer (``POST
        /transfers/rates``).

        ``amount`` is the DESTINATION amount the recipient should receive;
        ``source.amount`` in the response is what the source wallet will
        actually be debited (Flutterwave's live rate + spread). Returns
        ``None`` when the quote is unavailable so callers can fall back to
        the static FX table.
        """
        if self.payout_mock:
            rates = self._get_fx_rates()
            src = Decimal(str(rates.get(source_currency, 1)))
            dst = Decimal(str(rates.get(destination_currency, 1)))
            source_amount = (Decimal(str(amount)) * dst / src).quantize(Decimal('0.01'))
            return {
                'id': 'rte_mock',
                'rate': str((dst / src).normalize()),
                'source_amount': str(source_amount),
                'source_currency': source_currency,
                'destination_amount': str(amount),
                'destination_currency': destination_currency,
                'mock': True,
            }
        try:
            resp = self._v4_request(
                'POST', f'{self.v4_base_url}/transfers/rates',
                json={
                    'source': {'currency': source_currency},
                    'destination': {'currency': destination_currency, 'amount': float(amount)},
                },
                idempotency_key=f'rate{source_currency}{destination_currency}{amount}',
                timeout=15,
            )
            self._raise_for_charge_error(resp)
            data = resp.json().get('data', {})
            source = data.get('source') or {}
            destination = data.get('destination') or {}
            return {
                'id': data.get('id'),
                'rate': str(data.get('rate', '')),
                'source_amount': str(source.get('amount', '')),
                'source_currency': source.get('currency', source_currency),
                'destination_amount': str(destination.get('amount', '')),
                'destination_currency': destination.get('currency', destination_currency),
                'mock': False,
            }
        except requests.exceptions.RequestException as exc:
            logger.warning("Flutterwave rate quote failed: %s", exc)
            return None

    def initiate_transfer(self, *, amount, account_bank=None, account_number=None,
                          currency, debit_currency=None, reference=None,
                          reason=None, recipient_name=None, branch_code=None,
                          international=False, bank_name=None, swift_code=None,
                          routing_number=None, recipient_email=None,
                          recipient_address=None, recipient_phone=None,
                          account_type='individual', crypto_network=None,
                          crypto_address=None):
        """Initiate a transfer via v4 ``POST /direct-transfers``.

        ``amount`` is in the destination currency's major units. ``currency``
        is the destination currency; ``debit_currency`` is the source balance
        (defaults to ``currency``). Cross-currency transfers are priced by
        Flutterwave via its transfer-rates feed.

        Domestic corridors use ``recipient.bank = {code, account_number,
        branch_code?}``. International corridors (``international=True``) carry
        the bank's free-text ``name`` plus ``routing_number``/``swift_code``
        and a recipient ``address`` — and for EUR/GBP destinations the
        transfer-sender entity is mandatory (``config.payout_sender``).

        ``crypto_network`` + ``crypto_address`` switch the transfer to
        ``type: 'crypto'`` — a stablecoin send where ``currency`` is the
        token (USDT/USDC/RLUSD) and the recipient is ``{crypto: {network,
        address}}``. Flutterwave only funds crypto transfers from
        NGN/USD/GBP/EUR/GHS fiat balances or the same stablecoin wallet —
        cross-stablecoin sends are rejected upstream.
        """
        if reason is None:
            reason = f'{self.brand_name} payout'
        source_curr = (debit_currency or currency or 'NGN').upper()
        dest_curr = (currency or 'NGN').upper()
        raw_ref = reference or f'{self.reference_prefix}payout{secrets.token_hex(8)}'
        clean_ref = self._alnum(raw_ref)
        if clean_ref != str(raw_ref):
            clean_ref = f"{clean_ref}{hashlib.sha256(str(raw_ref).encode()).hexdigest()[:8]}"
        ref = clean_ref or f'{self.reference_prefix}payout{secrets.token_hex(8)}'

        # Crypto sends validate the token/network/address/funding-currency
        # quad BEFORE the mock early-return so a bad corridor fails the same
        # way in tests as upstream would.
        is_crypto = bool(crypto_address or crypto_network)
        if is_crypto:
            if dest_curr not in STABLECOIN_SUPPORTED_NETWORKS:
                raise ValueError(f'{dest_curr} is not a supported stablecoin.')
            crypto_network = (crypto_network or '').upper()
            supported_nets = STABLECOIN_SUPPORTED_NETWORKS.get(dest_curr, frozenset())
            if crypto_network not in supported_nets:
                raise ValueError(
                    f'{dest_curr} is not supported on {crypto_network or "?"}.')
            if not validate_crypto_address(crypto_address, crypto_network):
                raise ValueError(
                    f'Invalid {crypto_network} address: {crypto_address}')
            if source_curr not in STABLECOIN_FUNDING_CURRENCIES and source_curr != dest_curr:
                raise ValueError(
                    f'Crypto transfers can only be funded from '
                    f'{"/".join(sorted(STABLECOIN_FUNDING_CURRENCIES))} or '
                    f'{dest_curr} — not {source_curr}.')

        if self.payout_mock:
            return {
                'reference': ref,
                'transfer_code': f'TRF_flw_mock_{secrets.token_hex(6)}',
                'status': 'success',
                'mock': True,
            }

        # Preserve decimal precision for non-zero-decimal currencies
        amt_dec = Decimal(str(amount))
        amt_val = int(amt_dec) if amt_dec == amt_dec.to_integral() else float(amt_dec)

        name_obj = self._name_parts(recipient_name)

        if is_crypto:
            recipient_payload = {
                'crypto': {
                    'network': crypto_network,
                    'address': str(crypto_address),
                },
            }
            if name_obj:
                recipient_payload['name'] = name_obj
            if recipient_email:
                recipient_payload['email'] = str(recipient_email)
        elif international:
            recipient_bank = {
                'name': str(bank_name or ''),
                'account_number': str(account_number),
            }
            if routing_number:
                recipient_bank['routing_number'] = str(routing_number)
                if dest_curr == 'USD' and not recipient_bank.get('code'):
                    recipient_bank['code'] = str(routing_number)
                elif dest_curr == 'GBP' and not recipient_bank.get('sort_code'):
                    recipient_bank['sort_code'] = str(routing_number)
            if swift_code:
                recipient_bank['swift_code'] = str(swift_code)
            if account_bank and not recipient_bank.get('code'):
                recipient_bank['code'] = str(account_bank)

            recipient_payload = {
                'bank': recipient_bank,
                'account_type': str(account_type or 'individual'),
            }
            if name_obj:
                recipient_payload['name'] = name_obj
            if recipient_email:
                recipient_payload['email'] = str(recipient_email)
            if recipient_phone:
                norm_phone = self._normalize_phone(recipient_phone, dest_curr)
                if norm_phone:
                    recipient_payload['phone'] = norm_phone
            if recipient_address:
                addr = {k: str(v) for k, v in recipient_address.items() if v}
                # Flutterwave v4 requires state on international recipient address
                if 'city' in addr and 'state' not in addr:
                    addr['state'] = addr['city']
                recipient_payload['address'] = addr
        else:
            recipient_bank = {
                'account_number': str(account_number),
                'code': str(account_bank),
            }
            if branch_code:
                recipient_bank['branch_code'] = str(branch_code)
            recipient_payload = {'bank': recipient_bank}
            if name_obj:
                recipient_payload['name'] = name_obj

        payment_instruction = {
            'source_currency': source_curr,
            'destination_currency': dest_curr,
            'amount': {
                'applies_to': 'destination_currency',
                'value': amt_val,
            },
            'recipient': recipient_payload,
        }

        # EUR/GBP/EGP/INR transfers require a sender entity — the platform's
        # operator details, configured via ``config.payout_sender``.
        if dest_curr in ('EUR', 'GBP', 'EGP', 'INR'):
            sender = self._get_payout_sender()
            if sender:
                payment_instruction['sender'] = sender
            else:
                logger.warning(
                    'International %s payout without a configured payout_sender — '
                    'Flutterwave requires a transfer sender for this corridor.',
                    dest_curr,
                )

        payload = {
            'action': 'instant',
            'type': 'crypto' if is_crypto else 'bank',
            'reference': ref,
            'narration': reason,
            'payment_instruction': payment_instruction,
        }
        try:
            resp = self._v4_request(
                'POST',
                f'{self.v4_base_url}/direct-transfers',
                json=payload,
                idempotency_key=reference or ref,
                timeout=30,
            )
            self._raise_for_charge_error(resp)
            data = resp.json().get('data', {})
            status_str = (data.get('status') or '').lower()
            return {
                'reference': data.get('reference') or ref,
                'transfer_code': str(data.get('id') or ''),
                'status': 'success' if status_str in ('success', 'successful', 'succeeded') else (status_str or 'pending'),
            }
        except requests.exceptions.RequestException as exc:
            logger.warning("Flutterwave v4 transfer failed (%s).", exc)
            raise

    def get_transfer(self, transfer_id):
        """Fetch a transfer's status (v4 ``GET /transfers/{id}``)."""
        if self.payout_mock:
            return {'status': 'success', 'reference': '', 'mock': True}
        resp = self._v4_request(
            'GET',
            f'{self.v4_base_url}/transfers/{transfer_id}',
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get('data', {})
        raw_amount = data.get('amount')
        if isinstance(raw_amount, dict):
            raw_amount = raw_amount.get('value')
        recipient = data.get('recipient') or {}
        return {
            'status': (data.get('status') or '').lower(),
            'reference': data.get('reference') or '',
            'transfer_id': str(data.get('id') or transfer_id),
            'amount': raw_amount,
            'currency': data.get('destination_currency') or '',
            'bank': recipient.get('bank') or data.get('bank') or {},
            'fee': data.get('fee'),
        }

    def find_transfer_by_reference(self, reference, *, from_date=None, to_date=None,
                                   max_pages=20):
        """Find one v4 transfer after a lost initiation response."""
        if self.payout_mock:
            raise FlutterwaveConfigurationError(
                'A mock transfer cannot verify a financial payout.')
        params = {'size': 50}
        if from_date:
            params['from'] = str(from_date)
        if to_date:
            params['to'] = str(to_date)
        found = set()
        cursor = None
        for _ in range(max_pages):
            resp = self._v4_request(
                'GET', f'{self.v4_base_url}/transfers',
                params=params, timeout=20)
            self._raise_for_charge_error(resp)
            data = resp.json().get('data') or {}
            rows = data.get('transfers') or []
            for row in rows:
                if row.get('reference') == reference and row.get('id'):
                    found.add(str(row['id']))
            if len(found) > 1:
                raise RuntimeError('Multiple Flutterwave transfers used one reference.')
            cursor = (data.get('cursor') or {}).get('next')
            if not cursor:
                break
            params['next'] = cursor
        if cursor and not found:
            raise RuntimeError('Transfer search reached its page limit before a match.')
        return next(iter(found), None)

    # ------------------------------------------------------------------
    # Refunds (v4 /refunds)
    # ------------------------------------------------------------------
    def refund_transaction(self, transaction_id, amount=None, currency='NGN', reason='',
                           *, idempotency_key=None):
        """Refund a charge via the v4 ``POST /refunds`` endpoint.

        Args:
            transaction_id: The v4 charge id (``chg_...``) — typically
                persisted when the charge was confirmed.
            amount: Optional partial refund amount in major units. If None,
                the charge is refunded in full by omitting the field.
            currency: The refund currency (must match the original charge).
            reason: Optional note attached to the refund.
            idempotency_key: A persisted key identifying this refund operation.
                Reuse it when retrying after an uncertain response. When
                omitted, a deterministic key is derived from the charge,
                amount, currency and reason.

        Returns dict with 'status', 'reference', 'amount', 'currency', 'mock'.
        """
        curr = (currency or 'NGN').upper()
        if self.mock:
            return {
                'status': 'succeeded',
                'reference': str(transaction_id),
                'amount': amount,
                'currency': curr,
                'mock': True,
            }
        payload = {'charge_id': str(transaction_id)}
        if amount is not None:
            amt_dec = Decimal(str(amount))
            payload['amount'] = int(amt_dec) if amt_dec == amt_dec.to_integral() else float(amt_dec)
        if reason:
            payload['reason'] = reason
        if idempotency_key is None:
            amount_key = 'full' if amount is None else format(
                Decimal(str(amount)).normalize(), 'f')
            operation = '|'.join((
                str(transaction_id), amount_key, curr, str(reason or ''),
            ))
            idempotency_key = 'refund' + hashlib.sha256(
                operation.encode('utf-8')).hexdigest()
        try:
            resp = self._v4_request(
                'POST', f'{self.v4_base_url}/refunds',
                json=payload,
                idempotency_key=idempotency_key,
                timeout=30,
            )
            self._raise_for_charge_error(resp)
            data = resp.json().get('data', {})
            return {
                'status': data.get('status', 'pending'),
                'reference': str(transaction_id),
                'amount': data.get('amount_refunded', amount),
                'currency': data.get('currency', curr),
                'refund_id': data.get('id'),
                'mock': False,
            }
        except requests.exceptions.RequestException as exc:
            logger.error("Flutterwave refund failed for charge %s: %s", transaction_id, exc)
            raise RuntimeError(f"Flutterwave refund failed: {exc}") from exc

    def list_refunds_for_charge(self, charge_id, *, max_pages=20):
        """Find v4 refunds for a charge after an uncertain initiation response."""
        matches = []
        for page in range(1, max_pages + 1):
            resp = self._v4_request(
                'GET', f'{self.v4_base_url}/refunds',
                params={'page': page, 'size': 50}, timeout=20,
            )
            self._raise_for_charge_error(resp)
            body = resp.json()
            if body.get('status') != 'success':
                raise RuntimeError('Flutterwave refund listing failed.')
            rows = body.get('data') or []
            matches.extend({
                'refund_id': str(row.get('id') or ''),
                'transaction_id': str(row.get('charge_id') or ''),
                'amount': Decimal(str(row.get('amount_refunded') or '0')),
                'status': str(row.get('status') or '').lower(),
            } for row in rows if isinstance(row, dict)
                and str(row.get('charge_id') or '') == str(charge_id))
            if len(rows) < 50:
                return matches
        raise RuntimeError('Flutterwave refund listing exceeded the page limit.')

    # ------------------------------------------------------------------
    # Bill payments (v3-only — debits the merchant wallet, like /otps)
    # ------------------------------------------------------------------
    # Bills never run through the charge rails: a bill payment is a
    # wallet debit executed against the platform float after the caller
    # has collected the funds by other means. Endpoints:
    #   GET  {V3}/bill-categories[?<flag>|biller_code=…]  flat item catalog
    #   GET  {V3}/bill-items/{item_code}/validate         customer enquiry
    #   POST {V3}/bills                                 execute payment
    #   GET  {V3}/bills/{reference}                       payment status
    # All authenticate with the v3 secret key — present whenever
    # charge_api='v3' or the OTP client is configured; bills mock on
    # that credential specifically (``self.bills_mock``). The legacy flat
    # flow is used deliberately: the newer orchestrated endpoints
    # (/top-bill-categories, /bills/{cat}/billers, /billers/{code}/items,
    # /billers/…/payment) return 400 "contact your account administrator"
    # on accounts without the newer bills product, while the legacy
    # surface below is what live NG accounts actually expose.

    _MOCK_BILLERS = {
        'AIRTIME': (
            ('BILMCK_MTN', 'MTN Airtime'), ('BILMCK_AIRTEL', 'Airtel Airtime'),
            ('BILMCK_GLO', 'Glo Airtime'), ('BILMCK_9MOB', '9mobile Airtime'),
        ),
        'MOBILEDATA': (
            ('BILMCK_MTND', 'MTN Data'), ('BILMCK_AIRD', 'Airtel Data'),
            ('BILMCK_GLOD', 'Glo Data'), ('BILMCK_9MD', '9mobile Data'),
        ),
        'CABLEBILLS': (
            ('BILMCK_DSTV', 'DSTV'), ('BILMCK_GOTV', 'GOtv'),
            ('BILMCK_STARTIMES', 'StarTimes'),
        ),
        'UTILITYBILLS': (
            ('BILMCK_EKEDC', 'EKEDC — Eko Electricity'),
            ('BILMCK_IKEDC', 'IKEDC — Ikeja Electric'),
            ('BILMCK_AEDC', 'AEDC — Abuja Electricity'),
        ),
    }

    @staticmethod
    def _mock_bill_items(biller_code):
        """Deterministic mock catalog items per biller lane."""
        if '_MTND' in biller_code or '_AIRD' in biller_code \
                or '_GLOD' in biller_code or '_9MD' in biller_code:
            label, regex, resolvable = 'Phone Number', r'^0[0-9]{10}$', False
            items = [
                ('DATA1G', '1GB — 30 days', '350'),
                ('DATA5G', '5GB — 30 days', '1600'),
                ('DATA10G', '10GB — 30 days', '3000'),
            ]
        elif '_DSTV' in biller_code:
            label, regex, resolvable = 'SmartCard Number', r'^[0-9]+$', True
            items = [
                ('CB_PADI', 'DStv Padi', '4400'),
                ('CB_YANGA', 'DStv Yanga', '7300'),
                ('CB_COMPACT', 'DStv Compact', '19000'),
            ]
        elif '_GOTV' in biller_code:
            label, regex, resolvable = 'SmartCard Number', r'^[0-9]+$', True
            items = [
                ('GOTV_SMALL', 'GOtv Smallie', '3600'),
                ('GOTV_JOLLY', 'GOtv Jolly', '5900'),
                ('GOTV_MAX', 'GOtv Max', '8600'),
            ]
        elif '_STARTIMES' in biller_code:
            label, regex, resolvable = 'SmartCard Number', r'^[0-9]+$', True
            items = [('ST_NOVA', 'StarTimes Nova', '3000'),
                     ('ST_CLASSIC', 'StarTimes Classic', '6200')]
        elif any(k in biller_code for k in ('EKEDC', 'IKEDC', 'AEDC', '_PWR')):
            label, regex, resolvable = 'Meter Number', r'^[0-9]{11,13}$', True
            items = [('PWR_PREPAID', 'Prepaid top-up', None),
                     ('PWR_POSTPAID', 'Postpaid bill', None)]
        else:
            # Airtime — open amount, phone identifier, no resolution.
            label, regex, resolvable = 'Phone Number', r'^0[0-9]{10}$', False
            items = [('VTU', 'Airtime top-up', None)]
        biller_name = next(
            (n for cat in FlutterwaveClient._MOCK_BILLERS.values()
             for c, n in cat if c == biller_code), '')
        out = []
        for code, name, amount in items:
            out.append({
                'item_code': code, 'name': name, 'short_name': name,
                'biller_code': biller_code, 'biller_name': biller_name,
                # Decimal — same wire type the live catalog produces after
                # JSON encoding (DRF renders Decimal as a JSON number).
                'amount': Decimal(amount) if amount is not None else None,
                'fee': '0', 'label_name': label, 'reg_expression': regex,
                'is_resolvable': resolvable, 'is_airtime': code == 'VTU',
                'is_data': code.startswith('DATA'),
                'group_name': '', 'category_name': '',
                'validity_period': None,
            })
        return out

    def resolve_card_bin(self, bin_digits):
        """Resolve a card BIN (first 6–8 digits) → issuer info.

        ``GET /v3/card-bins/{bin}`` returns ``issuing_country`` in the
        "NIGERIA NG" shape — normalized here to the ISO-2 tail. Returns
        ``{'bin', 'country', 'issuer', 'card_type'}`` or ``None`` when the
        lookup can't run or resolve — BIN checks are best-effort pricing
        input, never a hard dependency.
        """
        digits = ''.join(ch for ch in str(bin_digits or '') if ch.isdigit())[:8]
        if len(digits) < 6 or not self.v3_secret_key:
            return None
        if self._is_testing():
            return {'bin': digits, 'country': 'NG', 'issuer': None, 'card_type': None}
        try:
            resp = self._v3_request(
                'GET', f'{self.v3_base_url}/card-bins/{digits}', timeout=10)
        except Exception:
            logger.warning('Card BIN lookup request failed: %s', digits)
            return None
        if resp.status_code != 200:
            logger.warning(
                'Card BIN lookup returned %s for %s', resp.status_code, digits)
            return None
        data = (resp.json() or {}).get('data') or {}
        raw = str(data.get('issuing_country') or '').strip().upper()
        match = re.search(r'\b([A-Z]{2})\b\s*$', raw)
        return {
            'bin': digits,
            'country': match.group(1) if match else None,
            'issuer': data.get('issuer_info') or data.get('issuer'),
            'card_type': data.get('card_type'),
        }

    def _require_bills(self):
        """Gate a bills call on v3 credentials — bills can't run without
        the FLWSECK key (the /otps precedent: raise, never fake delivery)."""
        if not self.v3_secret_key:
            raise RuntimeError(
                'FLUTTERWAVE_V3_SECRET_KEY is required for bill payments.')

    def list_bill_categories(self, country='NG'):
        """Supported bill categories — the configured allowlist rendered
        with display names (``[{code, name, description}]``).

        The provider's own category endpoint (``/top-bill-categories``)
        requires the newer bills product and 400s on legacy-enabled
        accounts, so the categories are served from config — discovery
        always keys off ``BILL_CATEGORY_FILTERS`` on
        ``GET /bill-categories`` anyway.
        """
        if not self.bills_mock:
            self._require_bills()
        out = []
        for code in (self.bill_categories or DEFAULT_BILL_CATEGORIES):
            name, desc = BILL_CATEGORY_NAMES.get(
                BILL_CATEGORY_ALIASES.get(code, code),
                (code.replace('_', ' ').title(), ''))
            out.append({'code': code, 'name': name, 'description': desc})
        return out

    def _bill_catalog(self, flag=None, biller_code=None, country='NG'):
        """Fetch the flat ``GET /bill-categories`` item catalog.

        ``flag`` is a category query flag (``airtime``/``data_bundle``/
        ``power``/``cable``/…); ``biller_code`` scopes to one biller.
        Rows are filtered to ``country`` client-side.
        """
        self._require_bills()
        params = {}
        if flag:
            params[flag] = 1
        if biller_code:
            params['biller_code'] = str(biller_code)
        resp = self._v3_request(
            'GET', f'{self.v3_base_url}/bill-categories',
            params=params, timeout=20)
        self._raise_for_charge_error(resp)
        country = (country or 'NG').upper()
        return [r for r in (resp.json().get('data') or [])
                if str(r.get('country') or '').upper() == country]

    @staticmethod
    def _biller_display_name(biller_code, rows):
        """Display name for a flat-catalog biller group — the catalog has
        no biller-name field, so: curated hint → common leading token of
        the item names → ``Biller <code>``."""
        if biller_code in BILLER_NAME_HINTS:
            return BILLER_NAME_HINTS[biller_code]
        names = [str(r.get('name') or '').strip() for r in rows]
        names = [n for n in names if n]
        if not names:
            return f'Biller {biller_code}'
        if len(names) == 1:
            return names[0]
        lo, hi = min(names), max(names)
        i = 0
        while i < len(lo) and lo[i] == hi[i]:
            i += 1
        prefix = lo[:i].strip(' -–—:/').strip()
        # A prefix shorter than 2 chars isn't a name (e.g. DSTV items are
        # all package names sharing no lead token).
        return prefix if len(prefix) >= 2 else f'Biller {biller_code}'

    def list_billers(self, *, category, country='NG'):
        """Billers under a category — grouped from the flat
        ``GET /bill-categories?<flag>=1`` catalog.

        Returns ``[{biller_code, name, short_name, logo, item_count}]``.
        """
        category = str(category or '').upper()
        if self.bills_mock:
            return [
                {'biller_code': code, 'name': name, 'short_name': name,
                 'logo': None, 'item_count': None}
                for code, name in self._MOCK_BILLERS.get(category, ())
            ]

        flag = BILL_CATEGORY_FILTERS.get(category)
        if not flag:
            return []
        rows = self._bill_catalog(flag=flag, country=country)
        groups = {}
        for row in rows:
            groups.setdefault(str(row.get('biller_code') or ''), []).append(row)
        out = []
        for bc in sorted(groups):
            name = self._biller_display_name(bc, groups[bc])
            out.append({
                'biller_code': bc, 'name': name, 'short_name': name,
                'logo': None, 'item_count': len(groups[bc]),
            })
        return out

    # The flat catalog reuses ``item_code`` across rows (all four NG
    # airtime networks are BIL099/AT099) — the disambiguating suffix we
    # append is ours alone and must be stripped before the code reaches
    # a provider URL.
    @staticmethod
    def _base_item_code(item_code):
        return str(item_code or '').split('#', 1)[0]

    # Labels whose identifiers the biller can resolve via the
    # validate endpoint (meters, smartcards, decoder/account numbers) —
    # phone numbers are not resolvable upstream.
    _UNRESOLVABLE_LABELS = re.compile(r'phone|mobile|msisdn', re.I)

    def _normalize_bill_item(self, row, biller_code, biller_name, item_code):
        amount = row.get('amount')
        try:
            amount = Decimal(str(amount)) if amount not in (None, '') else None
        except (InvalidOperation, ValueError):
            amount = None
        if amount is not None and amount <= 0:
            amount = None
        label = str(row.get('label_name') or '')
        is_airtime = bool(row.get('is_airtime'))
        return {
            'item_code': item_code,
            'name': str(row.get('name') or row.get('short_name') or ''),
            'short_name': str(row.get('short_name') or ''),
            'biller_code': biller_code,
            'biller_name': biller_name,
            'amount': amount,
            'fee': str(row.get('fee') or '0'),
            'label_name': label,
            # The flat catalog carries no regex/is_resolvable flags —
            # derived from the identifier label instead.
            'reg_expression': '',
            'is_resolvable': bool(
                label and not is_airtime
                and not self._UNRESOLVABLE_LABELS.search(label)),
            'is_airtime': is_airtime,
            'is_data': None,
            'group_name': '',
            'category_name': str(row.get('biller_name') or ''),
            'validity_period': None,
        }

    def list_bill_items(self, biller_code):
        """Purchasable items/packages for a biller — the biller's rows in
        the flat ``GET /bill-categories?biller_code=`` catalog.

        Item fields the buyer flow needs: ``item_code``, ``name``,
        ``amount`` (None = open amount), ``fee``, ``label_name`` (the
        customer-identifier label, e.g. "Meter Number"),
        ``is_resolvable`` (whether ``validate_bill_customer`` applies) —
        derived from the label — and ``is_airtime``.
        """
        biller_code = str(biller_code or '').strip()
        if self.bills_mock:
            return self._mock_bill_items(biller_code)

        rows = self._bill_catalog(biller_code=biller_code)
        biller_name = self._biller_display_name(biller_code, rows)
        counts = {}
        for row in rows:
            base = self._base_item_code(row.get('item_code'))
            counts[base] = counts.get(base, 0) + 1
        out = []
        for row in rows:
            base = self._base_item_code(row.get('item_code'))
            item_code = base
            if counts.get(base, 0) > 1:
                slug = re.sub(r'[^A-Z0-9]+', '-',
                              str(row.get('name') or '').upper()).strip('-')
                item_code = f'{base}#{slug}'
            out.append(self._normalize_bill_item(
                row, biller_code, biller_name, item_code))
        return out

    def validate_bill_customer(self, *, item_code, customer, biller_code=None):
        """Resolve a customer identifier against a bill item (v3 ``GET
        /bill-items/{item_code}/validate``) — meter numbers, smartcards,
        internet accounts. Airtime/data items don't resolve (``is_resolvable``
        is false on the item); callers should skip this step for them.

        Returns ``{'name', 'customer', 'biller_code', 'product_code',
        'fee', 'minimum', 'maximum', 'address'}`` — ``name`` is the
        account holder to confirm in the UI.
        """
        if self.bills_mock:
            return {
                'name': 'MOCK CUSTOMER',
                'customer': str(customer),
                'biller_code': biller_code or '',
                'product_code': str(item_code),
                'response_code': '00',
                'fee': '0', 'minimum': None, 'maximum': None,
                'address': None,
            }

        self._require_bills()
        item_code = self._base_item_code(item_code)
        params = {'customer': str(customer)}
        if biller_code:
            params['code'] = str(biller_code)
        resp = self._v3_request(
            'GET',
            f'{self.v3_base_url}/bill-items/{item_code}/validate',
            params=params, timeout=15)
        self._raise_for_charge_error(resp)
        data = resp.json().get('data') or {}
        return {
            'name': data.get('name') or '',
            'customer': data.get('customer') or str(customer),
            'biller_code': data.get('biller_code') or biller_code or '',
            'product_code': data.get('product_code') or item_code,
            # '00' is the biller's accept code; anything else means the
            # identifier isn't recognized on that account.
            'response_code': str(data.get('response_code') or ''),
            'fee': str(data.get('fee') or '0'),
            'minimum': data.get('minimum'),
            'maximum': data.get('maximum'),
            'address': data.get('address'),
        }

    def _normalize_bill_payment(self, data, mock=False):
        """Flatten a v3 bill-payment object into the dict the service
        layer consumes. ``status`` normalizes to succeeded|pending|
        failed; ``token`` carries prepaid-electricity STS tokens.

        Legacy shapes differ per endpoint: the create response carries
        ``code`` ("200" = accepted/executed) and ``recharge_token``
        instead of ``status``/``token``; the status lookup returns the
        transaction record (``product``/``product_details``/
        ``transaction_date``) with no explicit status field — presence of
        that record means the bill executed."""
        data = data or {}
        raw = str(data.get('status') or '').lower()
        code = str(data.get('code') or '')
        if raw in ('successful', 'success', 'succeeded', 'completed') \
                or code in ('200', '00'):
            status = 'succeeded'
        elif raw in ('failed', 'cancelled', 'reversed'):
            status = 'failed'
        elif raw or code:
            # An explicit but unknown status/code — not proven failed;
            # reconciliation decides rather than reversing a live bill.
            status = 'pending'
        elif data.get('product') or data.get('product_details') \
                or data.get('transaction_date'):
            status = 'succeeded'
        else:
            # No status at all on the create response — the payment was
            # accepted for processing; the status endpoint/webhook settles.
            status = 'pending'
        return {
            'status': status,
            'reference': str(data.get('tx_ref') or data.get('reference') or ''),
            'flw_ref': str(data.get('flw_ref') or data.get('flw_reference')
                           or data.get('reference') or ''),
            'amount': data.get('amount'),
            'currency': data.get('currency') or 'NGN',
            'customer_id': (data.get('customer_id') or data.get('customer')
                            or data.get('phone_number') or ''),
            'token': (data.get('token') or data.get('recharge_token')
                      or data.get('extra')),
            'network': data.get('network') or data.get('product_name'),
            # Bill status responses expose both the provider fee and any
            # merchant commission. Preserve them for margin reconciliation.
            'provider_fee': data.get('fee') if 'fee' in data else None,
            'provider_commission': (
                data.get('commission') if 'commission' in data else None),
            'mock': mock,
            'raw': data,
        }

    def create_bill_payment(self, *, biller_code, item_code, customer_id, amount,
                            country='NG', reference=None, callback_url=None,
                            item_type=None):
        """Execute a bill payment (v3 ``POST /bills``) — debits the
        merchant wallet.

        The legacy endpoint identifies the product by ``type`` — the
        item's catalog ``name`` (e.g. "MTN VTU", "EKEDC PREPAID TOPUP",
        "Compact + Asia"), passed via ``item_type``; ``item_code``/
        ``biller_code`` stay on our side for catalog bookkeeping.
        ``customer_id`` is the biller-side identifier (phone / meter /
        smartcard). ``reference`` is our dedup key — pass the persisted
        merchant reference so retries and status polls line up.
        ``callback_url`` receives the status webhook.
        """
        ref = str(reference or f'{self.reference_prefix}bill{secrets.token_hex(8)}')
        if self.bills_mock:
            return self._normalize_bill_payment({
                'status': 'success',
                'tx_ref': ref,
                'flw_ref': f'FLW-MOCK-{secrets.token_hex(6)}',
                'amount': float(amount),
                'currency': 'NGN',
                'customer_id': customer_id,
                # Mock prepaid-electricity tokens so dev can exercise the
                # token-delivery UI without a live biller.
                'token': '1234-5678-9012-3456-7890'
                         if str(item_code).upper().startswith(('PWR_PRE', 'EKP'))
                         else None,
            }, mock=True)

        self._require_bills()
        amt = Decimal(str(amount))
        payload = {
            'country': (country or 'NG').upper(),
            'customer': str(customer_id),
            'amount': int(amt) if amt == amt.to_integral() else float(amt),
            'recurrence': 'ONCE',
            'type': str(item_type or self._base_item_code(item_code)),
            'reference': ref,
        }
        if callback_url:
            payload['callback_url'] = str(callback_url)
        resp = self._v3_request(
            'POST', f'{self.v3_base_url}/bills',
            json=payload, timeout=30)
        self._raise_for_charge_error(resp)
        return self._normalize_bill_payment(resp.json().get('data') or {})

    def get_bill_payment(self, reference, verbose=True):
        """Poll a bill payment's status (v3 ``GET /bills/{reference}``).

        ``verbose`` asks the provider to re-query the upstream biller
        rather than replay a cached state — always on for reconciliation.
        """
        if self.bills_mock:
            return self._normalize_bill_payment({
                'status': 'success', 'tx_ref': reference, 'mock_probe': True,
            }, mock=True)

        self._require_bills()
        resp = self._v3_request(
            'GET', f'{self.v3_base_url}/bills/{reference}',
            params={'verbose': 1 if verbose else 0}, timeout=15)
        self._raise_for_charge_error(resp)
        return self._normalize_bill_payment(resp.json().get('data') or {})

    # ------------------------------------------------------------------
    # Errors & webhook verification
    # ------------------------------------------------------------------
    @staticmethod
    def _raise_for_charge_error(resp):
        """Raise a descriptive error carrying Flutterwave's v4 error body."""
        status_code = getattr(resp, 'status_code', None)
        if status_code is None:
            # Test doubles without status_code fall back to raise_for_status.
            resp.raise_for_status()
            return
        if status_code < 400:
            return
        message = f'Flutterwave API error ({resp.status_code})'
        try:
            body = resp.json()
            err = body.get('error') or {}
            detail = err.get('message') or body.get('message')
            validation = (
                err.get('validation_errors')
                or body.get('validation_errors')
                or body.get('errors')
                or err.get('errors')
                or []
            )
            if detail:
                message = detail
                if validation:
                    if isinstance(validation, list) and validation:
                        first = validation[0]
                        if isinstance(first, dict):
                            field = first.get('field_name') or first.get('field') or first.get('param')
                            msg = first.get('message') or first.get('error')
                            val_str = f'{field}: {msg}' if field and msg else (msg or str(first))
                        else:
                            val_str = str(first)
                        message = f'{detail} ({val_str})'
                    elif isinstance(validation, dict):
                        first_key = next(iter(validation))
                        message = f'{detail} ({first_key}: {validation[first_key]})'
                    else:
                        message = f'{detail} ({validation})'
        except (ValueError, AttributeError):
            pass
        exc = requests.exceptions.HTTPError(message, response=resp)
        raise exc

    def verify_webhook_signature(self, payload_bytes, signature):
        """Verify a webhook signature.

        v4: ``flutterwave-signature`` header is base64(HMAC-SHA256(raw_body,
        secret_hash)). The signature argument is that header value. The legacy
        v3 ``verif-hash`` plaintext is also accepted for compatibility.

        Deliberately not keyed off ``self.mock``: a deployment with only v4
        credentials configured still must verify webhooks strictly. Webhooks
        are accepted without verification only in tests or DEBUG with no
        configured hash.
        """
        # In testing mode, accept unsigned requests (no signature header
        # present) so webhook handlers can be exercised end-to-end. A
        # supplied signature is still verified against the real hash so that
        # signature-verification unit tests remain meaningful.
        if self._is_testing() and not signature:
            return True
        if not self.webhook_hash:
            return self._is_testing() or self._is_debug()

        # v4: HMAC-SHA256(raw_body, secret_hash) -> base64
        digest = hmac.new(
            self.webhook_hash.encode(), payload_bytes, hashlib.sha256
        ).digest()
        v4_sig = base64.b64encode(digest).decode()
        if hmac.compare_digest(str(signature), v4_sig):
            return True
        # v3 legacy: verif-hash is the raw secret hash, compared directly.
        return hmac.compare_digest(str(signature), self.webhook_hash)
