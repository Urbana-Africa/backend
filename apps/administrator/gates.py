"""Decision gates — evidence checks a privileged transition must pass, or
explicitly override with a documented exception (PRD DES-02 / CAT-01).

Each evaluator returns ``{'passed': bool, 'checks': {name: bool}}`` — a
failed check blocks the transition unless the caller supplies an exception
reason, which is recorded in the audit event alongside the failures.
"""


def evaluate_designer_readiness(designer) -> dict:
    """DES-02 — mandatory onboarding evidence before approval.

    Derived entirely from application fields; approval with failures
    requires a documented exception (``status_reasons``).
    """
    social = designer.social_media_links or {}
    checks = {
        'brand_identity': bool((designer.brand_name or '').strip()),
        'application_detail': bool(
            (designer.bio or '').strip() or (designer.story or '').strip()
        ),
        'contact_or_social': bool(
            (designer.instagram or '').strip()
            or (designer.website or '').strip()
            or (designer.phone or '').strip()
            or any(v for v in social.values())
        ),
        'shipping_capability': bool(
            (designer.ships_internationally or '').strip()
        ),
    }
    return {'passed': all(checks.values()), 'checks': checks}


def evaluate_product_moderation(product) -> dict:
    """CAT-01 — moderation checks before publishing a product live.

    Publish with failures requires ``exception_reason`` which is recorded
    (rule + actor land in the audit trail).
    """
    checks = {
        'media': product.media.exists(),
        'price_set': product.price is not None and product.price > 0,
        'description': bool((product.description or '').strip()),
        'categorized': bool(product.category_id) or product.categories.exists(),
    }
    return {'passed': all(checks.values()), 'checks': checks}
