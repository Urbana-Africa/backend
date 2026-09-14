"""
pay/services/cost_tracking.py

Centralized cost tracking for all variable-cost third-party API calls.

Every AI generation, image processing, or other metered external service
MUST route through ``record_api_cost`` so the platform can calculate the
true cost of serving each customer and each request.

Pricing constants below are ESTIMATES based on publicly listed provider
pricing as of 2025. They MUST be verified against the actual provider
invoices and updated when providers change their pricing.

EXTERNAL PRICE VERIFICATION REQUIRED for all values in PROVIDER_UNIT_COSTS.
"""

from decimal import Decimal
from django.utils import timezone
from apps.pay.models import ApiCostRecord


# ──────────────────────────────────────────────────────────────────────────
# Provider unit-cost estimates (USD per unit)
# EXTERNAL PRICE VERIFICATION REQUIRED — update with actual provider pricing.
# ──────────────────────────────────────────────────────────────────────────
PROVIDER_UNIT_COSTS = {
    # Google Gemini
    # Gemini 2.0 Flash (text/vision): ~$0.10 / 1M input tokens, ~$0.40 / 1M output
    # Gemini 2.5 Flash Image (Nano Banana): ~$0.039 / image (listed)
    "gemini-2.0-flash": {
        "per_input_token": Decimal("0.00000010"),
        "per_output_token": Decimal("0.00000040"),
        "per_image": Decimal("0.00"),  # text model, no image cost
    },
    "gemini-2.5-flash-image-preview": {
        "per_input_token": Decimal("0.00000010"),
        "per_output_token": Decimal("0.00000040"),
        "per_image": Decimal("0.039"),
    },
    "gemini-2.5-flash-image": {
        "per_input_token": Decimal("0.00000010"),
        "per_output_token": Decimal("0.00000040"),
        "per_image": Decimal("0.039"),
    },
    # fal.ai IDM-VTON (disabled but tracked for future)
    "fal-idm-vton": {
        "per_image": Decimal("0.05"),  # EXTERNAL VERIFICATION REQUIRED
    },
    # Replicate IDM-VTON (disabled but tracked for future)
    "replicate-idm-vton": {
        "per_image": Decimal("0.035"),  # EXTERNAL VERIFICATION REQUIRED
    },
}

# Default fallback cost when a model is not in the table
DEFAULT_PER_REQUEST_COST = Decimal("0.01")


def estimate_api_cost(provider: str, model: str, input_tokens=0, output_tokens=0, images=0) -> Decimal:
    """
    Estimate the USD cost of a single API call based on provider pricing.

    All values are estimates and should be reconciled against actual invoices.
    """
    key = model or ""
    pricing = PROVIDER_UNIT_COSTS.get(key, {})

    if not pricing:
        return DEFAULT_PER_REQUEST_COST

    cost = Decimal("0")
    cost += Decimal(str(input_tokens)) * pricing.get("per_input_token", Decimal("0"))
    cost += Decimal(str(output_tokens)) * pricing.get("per_output_token", Decimal("0"))
    cost += Decimal(str(images)) * pricing.get("per_image", Decimal("0"))

    # If we got nothing but the model exists, use default
    if cost == 0:
        return DEFAULT_PER_REQUEST_COST

    return cost.quantize(Decimal("0.000001"))


def record_api_cost(
    *,
    service: str,
    provider: str,
    model: str = "",
    user=None,
    session_key: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    images: int = 0,
    units: str = "1",
    related_request_id: str = "",
    related_payment=None,
    success: bool = True,
    error_message: str = "",
    estimated_cost_usd: Decimal = None,
) -> ApiCostRecord:
    """
    Record a single variable-cost API call to the immutable cost ledger.

    This function NEVER raises — cost tracking is best-effort and must not
    break the user-facing request. All exceptions are swallowed.

    Returns the ApiCostRecord (or None on failure).
    """
    try:
        if estimated_cost_usd is None:
            estimated_cost_usd = estimate_api_cost(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                images=images,
            )

        return ApiCostRecord.objects.create(
            user=user,
            session_key=session_key,
            service=service,
            provider=provider,
            model=model,
            units=units,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
            related_request_id=related_request_id,
            related_payment=related_payment,
            success=success,
            error_message=error_message,
        )
    except Exception:
        # Cost tracking must never break the request flow.
        return None


def get_user_api_costs(user, since=None) -> Decimal:
    """Total estimated API cost for a user (optionally since a datetime)."""
    qs = ApiCostRecord.objects.filter(user=user, success=True)
    if since:
        qs = qs.filter(created_at__gte=since)
    from django.db.models import Sum
    return qs.aggregate(total=Sum("estimated_cost_usd"))["total"] or Decimal("0")


def get_session_api_costs(session_key: str, since=None) -> Decimal:
    """Total estimated API cost for an anonymous session."""
    if not session_key:
        return Decimal("0")
    qs = ApiCostRecord.objects.filter(session_key=session_key, success=True)
    if since:
        qs = qs.filter(created_at__gte=since)
    from django.db.models import Sum
    return qs.aggregate(total=Sum("estimated_cost_usd"))["total"] or Decimal("0")
