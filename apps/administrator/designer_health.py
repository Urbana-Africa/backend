"""DES-03 / DES-04 — designer activation funnel and health score.

Every stage/component is derived from source records (products, order
items, payments, returns, disputes) — never from manual labels.
"""
from datetime import timedelta

from django.utils import timezone


def activation_funnel(designer) -> dict:
    """DES-03 — approved → first published product → first paid order →
    first fulfilled order, each derived from source records."""
    from apps.core.models import Product
    from apps.customers.models import OrderItem

    user = designer.user
    first_product = (
        Product.objects.filter(user=user, is_published=True)
        .order_by('created_at').first())
    first_paid = (
        OrderItem.objects
        .filter(designer=user,
                order__invoice__payment__is_paid=True,
                order__invoice__payment__is_deleted=False)
        .order_by('created_at').first())
    first_fulfilled = (
        OrderItem.objects
        .filter(designer=user, status='delivered')
        .order_by('delivered_at').first())

    steps = {
        'approved': {
            'done': designer.status == 'approved',
            'at': str(designer.status_updated_at)
                  if designer.status == 'approved' else None,
        },
        'first_published_product': {
            'done': bool(first_product),
            'at': str(first_product.created_at) if first_product else None,
            'product': str(first_product.pk) if first_product else None,
        },
        'first_paid_order': {
            'done': bool(first_paid),
            'at': str(first_paid.created_at) if first_paid else None,
            'order_item': first_paid.item_id if first_paid else None,
        },
        'first_fulfilled_order': {
            'done': bool(first_fulfilled),
            'at': str(first_fulfilled.delivered_at)
                  if first_fulfilled else None,
            'order_item': first_fulfilled.item_id if first_fulfilled else None,
        },
    }
    first_open = next(
        (k for k, v in steps.items() if not v['done']), None)
    return {'designer': str(designer.pk), 'steps': steps,
            'stalled_at': first_open, 'activated': first_open is None}


def health_score(designer) -> dict:
    """DES-04 — component-scored 0–100 health, with staff override support.

    Components (weights): fulfillment reliability 35, return rate 20,
    dispute pressure 15, catalog quality 15, recent sales 15. An active
    override replaces the computed score but the components stay visible.
    """
    from apps.core.models import Product
    from apps.customers.models import Dispute, OrderItem

    user = designer.user
    items = OrderItem.objects.filter(designer=user)
    total = items.count()
    delivered = items.filter(status='delivered').count()
    returned = items.filter(status='returned').count()
    disputes = Dispute.objects.filter(
        return_request__order_item__designer=user,
        status__in=['opened', 'under_review', 'escalated']).count()
    products = Product.objects.filter(user=user)
    product_count = products.count()
    with_media = products.filter(media__isnull=False).distinct().count()
    recent_sales = items.filter(
        created_at__gte=timezone.now() - timedelta(days=30),
        order__invoice__payment__is_paid=True).count()

    fulfillment = delivered / total if total else 0
    return_rate = returned / delivered if delivered else 0
    catalog_quality = with_media / product_count if product_count else 0

    components = {
        'fulfillment_reliability': {
            'weight': 35, 'value': fulfillment,
            'detail': f'{delivered}/{total} items delivered'},
        'return_rate': {
            'weight': 20, 'value': 1 - min(return_rate, 1),
            'detail': f'{returned}/{delivered} delivered items returned'},
        'dispute_pressure': {
            'weight': 15, 'value': 1 if disputes == 0 else
                    (0.5 if disputes < 3 else 0),
            'detail': f'{disputes} open disputes'},
        'catalog_quality': {
            'weight': 15, 'value': catalog_quality,
            'detail': f'{with_media}/{product_count} products with media'},
        'recent_sales': {
            'weight': 15, 'value': min(recent_sales / 5, 1),
            'detail': f'{recent_sales} paid items in last 30d'},
    }
    computed = round(sum(
        c['weight'] * c['value'] for c in components.values()), 1)

    override = designer.health_override or {}
    override_active = bool(
        override.get('score') is not None and override.get('expires_at')
        and str(override['expires_at']) > timezone.now().isoformat())
    return {
        'designer': str(designer.pk),
        'score': override.get('score') if override_active else computed,
        'computed': computed,
        'override': override if override_active else None,
        'components': components,
        'status': designer.status,
    }
