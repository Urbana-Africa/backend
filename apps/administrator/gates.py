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


def refund_context(dispute) -> dict:
    """SUP-03 — the financial consequence preview for a dispute refund.

    ``remaining`` is the collected merchandise value of the disputed order
    item minus refunds already recorded against the same item — a refund
    can never exceed what was actually collected nor be issued twice.
    """
    from decimal import Decimal
    item = dispute.return_request.order_item
    order = item.order
    payment = getattr(getattr(order, 'invoice', None), 'payment', None)
    paid = bool(payment and getattr(payment, 'is_paid', False)
                and not getattr(payment, 'is_deleted', False))
    collected = item.sub_total if paid else Decimal('0')

    from apps.customers.models import Dispute
    prior = (
        Dispute.objects
        .filter(return_request__order_item=item,
                status=Dispute.Status.RESOLVED,
                refund_amount__isnull=False)
        .exclude(pk=dispute.pk)
        .aggregate(total=models_sum('refund_amount'))['total']
    ) or Decimal('0')
    escrow = getattr(item, 'escrow', None)
    return {
        'order_item': str(item.pk),
        'order': str(order.pk),
        'payment_confirmed': paid,
        'collected_amount': str(collected),
        'previously_refunded': str(prior),
        'remaining_refundable': str(collected - prior),
        'escrow_status': escrow.status if escrow else 'none',
        'escrow_amount': str(escrow.amount) if escrow else '0',
        'platform_commission': str(escrow.platform_commission) if escrow else '0',
        'return_window_open': dispute.return_request.is_return_eligible,
        'currency': order.invoice.payment.currency if paid and getattr(
            payment, 'currency', None) else 'USD',
    }


def models_sum(field):
    from django.db.models import Sum
    return Sum(field)
