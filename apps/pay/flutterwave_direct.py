"""Flutterwave direct-charge integration for Urbana invoices.

Bridges the portable ``flutterwave`` package (``backend/flutterwave``) to
Urbana's Invoice / PaymentAttempt models. The storefront checkout
(``frontend/store/lib/flutterwave`` → ``createDirectChargeApi``) talks to the
three views below:

    POST /pay/flutterwave/charge/     start a charge for an initialized attempt
    POST /pay/flutterwave/authorize/  continue a charge (PIN / OTP / AVS)
    GET  /pay/flutterwave/status/     poll / reconcile (?reference=...)

Flow: ``POST /pay/init/flutterwave/`` mints a PaymentAttempt (reference +
server-authoritative ``payment_methods``), then the checkout charges that
reference. Amount and currency always come from the invoice — never from the
client. A charge is only reported ``succeeded`` after the provider transaction
has been re-verified server-side and the attempt finalized.

Rail selection: when ``FLUTTERWAVE_CLIENT_ID``/``FLUTTERWAVE_CLIENT_SECRET``
are set the v4 API is used; otherwise the legacy v3 direct-charge rail runs on
the existing ``FLUTTERWAVE_[TEST_]SECRET_KEY`` / ``ENCRYPTION_KEY`` pair.
``FLW_MOCK=true`` returns canned provider responses for local development.
"""
import logging
from decimal import Decimal
from urllib.parse import urlparse

import requests
from decouple import config
from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from flutterwave import (
    FlutterwaveClient,
    FlutterwaveConfig,
    FlutterwaveConfigurationError,
)
from flutterwave.constants import (
    CURRENCY_CHARGE_METHODS,
    SUPPORTED_CHARGE_METHODS,
    WALLET_METHODS,
)
from flutterwave.currency import to_minor_units

from .config import ENV, get_flutterwave_keys
from .confirm import finalize_successful_attempt
from .models import Invoice, PaymentAttempt

logger = logging.getLogger(__name__)

PROCESSOR = "flutterwave"
INVOICE_CURRENCY = "USD"  # see initialize.get_invoice_currency

# Rails the legacy v3 charge payload can express (see
# FlutterwaveClient._v3_charge_payload) — wallets/bank_account are v4-only.
V3_CHARGE_METHODS = frozenset({"card", "bank_transfer", "ussd", "mobile_money"})


# ───────────────────────────────
# Client / configuration
# ───────────────────────────────
def build_flutterwave_config():
    """FlutterwaveConfig from Urbana's .env (python-decouple)."""
    client_id = config("FLUTTERWAVE_CLIENT_ID", default="")
    client_secret = config("FLUTTERWAVE_CLIENT_SECRET", default="")
    use_v4 = bool(client_id and client_secret)
    v3_keys = {}
    if not use_v4:
        try:
            v3_keys = get_flutterwave_keys()
        except Exception:  # missing keys → client fails closed at request time
            v3_keys = {}
    return FlutterwaveConfig.from_dict({
        "FLUTTERWAVE_CLIENT_ID": client_id,
        "FLUTTERWAVE_CLIENT_SECRET": client_secret,
        "FLUTTERWAVE_V4_BASE_URL": config("FLUTTERWAVE_V4_BASE_URL", default=""),
        "FLUTTERWAVE_ENCRYPTION_KEY": config(
            "FLUTTERWAVE_V4_ENCRYPTION_KEY", default="") or v3_keys.get("encryption_key", ""),
        "FLUTTERWAVE_WEBHOOK_HASH": config("FLUTTERWAVE_WEBHOOK_HASH", default=""),
        "FLW_CHARGE_API": "v4" if use_v4 else "v3",
        "FLUTTERWAVE_V3_SECRET_KEY": v3_keys.get("secret_key", ""),
        "FLUTTERWAVE_V3_ENCRYPTION_KEY": v3_keys.get("encryption_key", ""),
        "FLW_MOCK": config("FLW_MOCK", default=""),
        "FLW_DEBUG": "true" if getattr(settings, "DEBUG", False) else "",
        "FLW_BRAND_NAME": "Urbana",
        "FLW_REFERENCE_PREFIX": "urbana",
    })


def get_client():
    return FlutterwaveClient(build_flutterwave_config())


def payment_methods_for(currency, client=None):
    """Server-authoritative rails for a currency on the active charge API."""
    client = client or get_client()
    offered = CURRENCY_CHARGE_METHODS.get(
        (currency or "").upper(), frozenset({"card"}) | WALLET_METHODS)
    if client.charge_api == "v3" and not client.mock:
        offered = offered & V3_CHARGE_METHODS
    return [m for m in SUPPORTED_CHARGE_METHODS if m in offered]


def checkout_meta(currency):
    """Extra fields the init endpoint returns for the direct-charge checkout."""
    client = get_client()
    return {
        "payment_methods": payment_methods_for(currency, client),
        "charge_api": client.charge_api,
        "test_mode": bool(client.mock or ENV != "prod"),
        "mock": bool(client.mock),
    }


# ───────────────────────────────
# Helpers
# ───────────────────────────────
def _allowed_redirect_origins():
    origins = set(getattr(settings, "CORS_ALLOWED_ORIGINS", []) or [])
    store_url = getattr(settings, "STORE_URL", "")
    if store_url:
        parsed = urlparse(store_url)
        origins.add(f"{parsed.scheme}://{parsed.netloc}")
    extra = config("FLW_REDIRECT_ORIGINS", default="")
    origins.update(o.strip() for o in extra.split(",") if o.strip())
    return {o.rstrip("/").lower() for o in origins}


def _safe_redirect_url(url):
    """Accept a 3DS return URL only if it points back at a trusted storefront."""
    if not url:
        return None
    parsed = urlparse(str(url))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}".lower()
    return str(url) if origin in _allowed_redirect_origins() else None


def _load_attempt(request, reference):
    """Resolve the caller's Flutterwave attempt + invoice or an error Response."""
    if not reference:
        return None, None, Response({"error": "reference is required"}, status=400)
    attempt = PaymentAttempt.objects.filter(
        reference=reference, processor=PROCESSOR).first()
    if not attempt:
        return None, None, Response({"error": "Payment attempt not found."}, status=404)
    invoice = Invoice.objects.filter(payment_attempts=attempt, is_deleted=False).first()
    if not invoice or invoice.user_id != request.user.id:
        return None, None, Response({"error": "Payment attempt not found."}, status=404)
    return attempt, invoice, None


def _customer_for(user):
    return {
        "email": user.email,
        "name": (user.get_full_name() or "").strip() or None,
        "phone": getattr(user, "phone", None) or getattr(user, "phone_number", None),
    }


def _charge_context(attempt, invoice, user, redirect_url=None):
    return {
        "amount": Decimal(invoice.amount),
        "currency": INVOICE_CURRENCY,
        "reference": attempt.reference,
        "customer": _customer_for(user),
        "redirect_url": redirect_url,
    }


def _provider_error(exc):
    if isinstance(exc, FlutterwaveConfigurationError):
        logger.error("Flutterwave misconfigured: %s", exc)
        return Response({"error": "Card payments are temporarily unavailable."},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    if isinstance(exc, ValueError):
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, requests.HTTPError):
        return Response({"error": str(exc) or "Payment was declined."},
                        status=status.HTTP_400_BAD_REQUEST)
    logger.exception("Flutterwave charge error")
    return Response({"error": "Unable to reach the payment provider. Please retry."},
                    status=status.HTTP_502_BAD_GATEWAY)


def _verified(client, charge_id, attempt, invoice):
    """Re-verify a provider-reported success against the invoice amount."""
    try:
        result = client.verify_transaction(charge_id, reference=attempt.reference)
    except Exception as exc:
        logger.warning("Flutterwave verify failed for %s: %s", attempt.reference, exc)
        return False
    if result.get("status") != "success":
        return False
    if result.get("mock"):
        return True
    currency = str(result.get("currency") or "").upper()
    expected = to_minor_units(invoice.amount, INVOICE_CURRENCY)
    return currency == INVOICE_CURRENCY and int(result.get("amount") or 0) >= expected


def _finalize(attempt, charge_id):
    """Idempotently finalize — safe against webhook / poll races."""
    with transaction.atomic():
        locked = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status == "success":
            return list(Invoice.objects.filter(payment_attempts=locked))
        return finalize_successful_attempt(locked, PROCESSOR, charge_id)


def _apply_result(client, attempt, invoice, result):
    """Persist a normalized charge result and build the API response body."""
    charge_id = result.get("charge_id") or ""
    provider_status = (result.get("status") or "").lower()

    if charge_id and charge_id != attempt.processor_payment_id:
        attempt.processor_payment_id = charge_id
        attempt.save(update_fields=["processor_payment_id"])

    body = {
        "reference": attempt.reference,
        "charge_id": charge_id or None,
        "flw_ref": result.get("flw_ref"),
        "next_action": result.get("next_action"),
        "redirect_url": result.get("redirect_url"),
        "payment_method_type": result.get("payment_method_type"),
        "processor_response": result.get("processor_response"),
        "mock": bool(result.get("mock")),
    }

    if provider_status in ("succeeded", "successful", "success"):
        if charge_id and _verified(client, charge_id, attempt, invoice):
            invoices = _finalize(attempt, charge_id)
            body.update(status="succeeded", invoice_ids=[i.id for i in invoices])
            return body
        # Provider said yes but verification disagreed — stay pending so the
        # webhook / status poll can reconcile instead of failing a real charge.
        body["status"] = "pending"
        return body

    if provider_status in ("failed", "cancelled", "voided"):
        attempt.status = "failed"
        attempt.is_successful = False
        attempt.save(update_fields=["status", "is_successful"])
        body["status"] = "failed"
        return body

    if attempt.status != "pending":
        attempt.status = "pending"
        attempt.save(update_fields=["status"])
    body["status"] = "pending"
    return body


# ───────────────────────────────
# Views
# ───────────────────────────────
class FlutterwaveDirectChargeView(APIView):
    """Start a v4/v3 direct charge for an initialized PaymentAttempt."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        attempt, invoice, error = _load_attempt(request, request.data.get("reference"))
        if error:
            return error
        if attempt.status == "success" or (invoice.payment and invoice.payment.status == "success"):
            return Response({"error": "This invoice has already been paid."}, status=409)
        if attempt.processor_payment_id:
            # v3 dedupes on tx_ref — each charge needs a fresh attempt.
            return Response(
                {"error": "This payment session was already used. Please restart checkout."},
                status=409)

        payment_method = request.data.get("payment_method") or {}
        if not isinstance(payment_method, dict) or not payment_method.get("type"):
            return Response({"error": "payment_method.type is required"}, status=400)

        client = get_client()
        if payment_method["type"] not in payment_methods_for(INVOICE_CURRENCY, client):
            return Response({"error": "That payment method is not available."}, status=400)

        redirect_url = _safe_redirect_url(request.data.get("redirect_url"))
        ctx = _charge_context(attempt, invoice, request.user, redirect_url)

        attempt.user = attempt.user or request.user
        attempt.amount = ctx["amount"]
        attempt.currency = INVOICE_CURRENCY
        attempt.status = "pending"
        attempt.save(update_fields=["user", "amount", "currency", "status"])

        try:
            result = client.orchestrate_charge(
                amount=ctx["amount"],
                currency=ctx["currency"],
                reference=attempt.reference,
                customer=ctx["customer"],
                payment_method=payment_method,
                redirect_url=redirect_url,
                meta={"invoice_id": str(invoice.id)},
            )
        except Exception as exc:
            attempt.status = "failed"
            attempt.save(update_fields=["status"])
            return _provider_error(exc)

        return Response(_apply_result(client, attempt, invoice, result))


class FlutterwaveDirectAuthorizeView(APIView):
    """Continue a pending charge with PIN / OTP / AVS details."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        attempt, invoice, error = _load_attempt(request, request.data.get("reference"))
        if error:
            return error
        if attempt.status == "success":
            return Response({"reference": attempt.reference, "status": "succeeded"})

        authorization = request.data.get("authorization") or {}
        if not isinstance(authorization, dict) or not authorization.get("type"):
            return Response({"error": "authorization.type is required"}, status=400)

        client = get_client()
        charge_id = attempt.processor_payment_id or request.data.get("flw_ref") or ""
        if not charge_id and client.charge_api == "v4":
            return Response({"error": "No charge to authorize."}, status=409)

        redirect_url = _safe_redirect_url(request.data.get("redirect_url"))
        try:
            result = client.authorize_charge(
                charge_id,
                authorization,
                # v3 PIN/AVS resubmits the card — held in browser memory only,
                # never persisted server-side.
                payment_method=request.data.get("payment_method"),
                flw_ref=request.data.get("flw_ref"),
                charge_context=_charge_context(attempt, invoice, request.user, redirect_url),
            )
        except Exception as exc:
            return _provider_error(exc)

        return Response(_apply_result(client, attempt, invoice, result))


class FlutterwaveDirectStatusView(APIView):
    """Poll a charge — used after 3DS redirects and for async rails."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        attempt, invoice, error = _load_attempt(request, request.query_params.get("reference"))
        if error:
            return error
        if attempt.status == "success":
            return Response({"reference": attempt.reference, "status": "succeeded",
                             "invoice_ids": [invoice.id]})
        if attempt.status == "failed":
            return Response({"reference": attempt.reference, "status": "failed"})
        if not attempt.processor_payment_id:
            return Response({"reference": attempt.reference, "status": "pending"})

        client = get_client()
        try:
            result = client.get_charge(attempt.processor_payment_id)
        except Exception as exc:
            return _provider_error(exc)
        return Response(_apply_result(client, attempt, invoice, result))


class FlutterwaveUnsupportedView(APIView):
    """Endpoints the portable checkout contract names but Urbana doesn't
    offer (stablecoin rail, fee quotes, subscription verify). Fails loudly."""
    permission_classes = [IsAuthenticated]

    def _unsupported(self, request, feature):
        return Response({"error": f"'{feature}' is not supported by this store."},
                        status=status.HTTP_501_NOT_IMPLEMENTED)

    get = post = _unsupported
