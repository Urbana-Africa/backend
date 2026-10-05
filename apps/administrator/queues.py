"""Work-queue derivation (PRD Phase 1 — unified work queues).

``sync_work_queues`` materializes WorkItems from source records. Semantics:

- One WorkItem per (queue, entity_type, entity_id) — idempotent upsert.
- When the source condition clears, an open item is auto-closed
  (``resolution_note = 'source resolved'``) so queues can't silently
  fill with stale work.
- A manually resolved/closed item is NEVER auto-reopened — a human
  decision is itself a recorded resolution.
- SLA due dates come from ``settings.WORK_QUEUE_SLA_HOURS`` per queue.

Each source returns dicts: {key, title, detail, priority, source_status, due_at}.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import WorkItem

logger = logging.getLogger(__name__)

# SLA hours per queue (overridable in settings).
DEFAULT_SLA_HOURS = {
    'order_pending': 24,
    'dispatch_late': 72,
    'delivery_exception': 24,
    'support_case': 24,
    'designer_onboarding': 72,
    'catalog_moderation': 48,
    'reconciliation': 48,
    'payout_approval': 24,
    'return_action': 24,
    'incident': 4,
}

# SUP-04 — auto-escalation thresholds (overridable in settings).
ESCALATION_DEFAULTS = {
    'order_value': 500,     # paid orders/items above this auto-escalate
    'repeat_defects': 3,    # open disputes per designer → defect pattern
}

# SUP-02 active case states — resolved/closed drop out of the queue.
TICKET_ACTIVE_STATUSES = [
    'open', 'triaged', 'in_progress', 'investigating',
    'awaiting_party', 'waiting', 'reopened',
]

# Business rules: how stale a condition must be to enter a queue.
DISPATCH_SLA_DAYS = 3      # paid item still not dispatched by the designer
ORDER_ACK_HOURS = 24       # paid order still 'pending'
TRANSIT_STALE_DAYS = 14    # shipped item with no delivery after N days


def _sla(queue):
    conf = getattr(settings, 'WORK_QUEUE_SLA_HOURS', {}) or {}
    return int(conf.get(queue, DEFAULT_SLA_HOURS.get(queue, 48)))


def _due(queue, base):
    return base + timedelta(hours=_sla(queue))


# ---------------------------------------------------------------------------
# Source extractors — each yields candidate dicts.
# ---------------------------------------------------------------------------

def _from_support_tickets():
    from apps.core.models import SupportTicket
    for t in SupportTicket.objects.filter(
        status__in=TICKET_ACTIVE_STATUSES
    ).iterator():
        yield {
            'queue': 'support_case',
            'entity_type': 'SupportTicket',
            'entity_id': str(t.id),
            'title': f"{t.reference} — {t.subject[:160]}",
            'detail': {
                'category': t.category, 'priority': t.priority,
                'status': t.status,
                'agents': t.assigned_agents.count(),
            },
            'priority': t.priority if t.priority in ('low', 'medium', 'high', 'urgent') else 'medium',
            'source_status': t.status,
            'due_at': _due('support_case', t.created_at),
        }


def _from_designer_onboarding():
    from apps.designers.models import Designer
    for d in Designer.objects.filter(status='pending').select_related('user').iterator():
        yield {
            'queue': 'designer_onboarding',
            'entity_type': 'Designer',
            'entity_id': str(d.id),
            'title': f"Onboarding: {d.brand_name or d.user.email}",
            'detail': {
                'brand': d.brand_name, 'email': d.user.email,
                'products': d.user.products.count() if hasattr(d.user, 'products') else None,
            },
            'priority': 'medium',
            'source_status': 'pending',
            'due_at': _due('designer_onboarding', d.created_at),
        }


def _from_catalog_moderation():
    from apps.core.models import Product
    for p in Product.objects.filter(
        is_active=True, is_published=False,
    ).select_related('user').iterator():
        yield {
            'queue': 'catalog_moderation',
            'entity_type': 'Product',
            'entity_id': str(p.id),
            'title': f"Review product: {p.name[:160]}",
            'detail': {
                'designer': getattr(getattr(p, 'user', None), 'email', ''),
                'stock': p.stock, 'price': str(p.price),
            },
            'priority': 'medium',
            'source_status': 'unpublished',
            'due_at': _due('catalog_moderation', p.created_at),
        }


def _from_orders():
    from apps.customers.models import Order, OrderItem

    # Paid orders still awaiting acknowledgement.
    ack_cutoff = timezone.now() - timedelta(hours=ORDER_ACK_HOURS)
    for o in Order.objects.filter(
        status='pending', created_at__lte=ack_cutoff,
        invoice__payment__is_paid=True, invoice__payment__is_deleted=False,
    ).iterator():
        yield {
            'queue': 'order_pending',
            'entity_type': 'Order',
            'entity_id': o.order_id,
            'title': f"Unacknowledged paid order {o.order_id}",
            'detail': {'total': str(o.total_amount), 'age_hours': ORDER_ACK_HOURS},
            'priority': 'high',
            'source_status': 'pending',
            'due_at': _due('order_pending', o.created_at),
        }

    # Paid items the designer hasn't dispatched in DISPATCH_SLA_DAYS.
    dispatch_cutoff = timezone.now() - timedelta(days=DISPATCH_SLA_DAYS)
    for item in OrderItem.objects.filter(
        status='pending', created_at__lte=dispatch_cutoff,
        order__invoice__payment__is_paid=True,
        order__invoice__payment__is_deleted=False,
    ).select_related('order', 'designer').iterator():
        yield {
            'queue': 'dispatch_late',
            'entity_type': 'OrderItem',
            'entity_id': str(item.item_id),
            'title': f"Late dispatch: item {item.item_id} (order {item.order.order_id})",
            'detail': {
                'order': item.order.order_id,
                'designer': getattr(getattr(item, 'designer', None), 'email', ''),
                'days_pending': DISPATCH_SLA_DAYS,
            },
            'priority': 'high',
            'source_status': item.status,
            'due_at': _due('dispatch_late', item.created_at),
        }

    # Shipped items that never reached delivered — possible stuck transit.
    transit_cutoff = timezone.now() - timedelta(days=TRANSIT_STALE_DAYS)
    for item in OrderItem.objects.filter(
        status='shipped', created_at__lte=transit_cutoff,
        order__invoice__payment__is_paid=True,
    ).select_related('order').iterator():
        yield {
            'queue': 'delivery_exception',
            'entity_type': 'OrderItem',
            'entity_id': str(item.item_id),
            'title': f"Stuck in transit: item {item.item_id} (order {item.order.order_id})",
            'detail': {
                'order': item.order.order_id,
                'tracking': item.tracking_number or '',
                'days_shipped': TRANSIT_STALE_DAYS,
            },
            'priority': 'high',
            'source_status': item.status,
            'due_at': _due('delivery_exception', item.created_at),
        }


def _from_reconciliation():
    from .models import ReconciliationException
    for exc in ReconciliationException.objects.filter(status='open').iterator():
        yield {
            'queue': 'reconciliation',
            'entity_type': 'ReconciliationException',
            'entity_id': str(exc.id),
            'title': f"{exc.issue}: {exc.entity_type} {exc.entity_id}",
            'detail': {'issue': exc.issue, 'entity': exc.entity_id, **(exc.detail or {})},
            'priority': 'high',
            'source_status': 'open',
            'due_at': _due('reconciliation', exc.created_at),
        }


def _from_withdrawals():
    from apps.pay.models import Withdrawal
    for w in Withdrawal.objects.filter(status='pending').iterator():
        yield {
            'queue': 'payout_approval',
            'entity_type': 'Withdrawal',
            'entity_id': str(w.id),
            'title': f"Withdrawal {w.id} — {w.user.email} ${w.amount}",
            'detail': {'amount': str(w.amount), 'payout_amount': str(w.payout_amount)},
            'priority': 'high',
            'source_status': 'pending',
            'due_at': _due('payout_approval', w.created_at),
        }


def _from_return_requests():
    """OPS-02 — returns awaiting an action (approval, transit, refund)."""
    from apps.customers.models import ReturnRequest
    for rr in ReturnRequest.objects.filter(
        status__in=['pending', 'reviewing', 'return_in_transit',
                    'return_received', 'refund_pending',
                    'dispute_opened', 'dispute_under_review'],
    ).select_related('order_item__order', 'order_item__designer').iterator():
        yield {
            'queue': 'return_action',
            'entity_type': 'ReturnRequest',
            'entity_id': str(rr.return_id),
            'title': f"Return {rr.return_id} — {rr.status}",
            'detail': {
                'order': rr.order_item.order.order_id,
                'item': rr.order_item.item_id,
                'reason': rr.reason,
                'designer': getattr(rr.order_item.designer, 'email', ''),
                'amount': str(rr.order_item.sub_total),
            },
            'priority': 'high' if 'dispute' in rr.status else 'medium',
            'source_status': rr.status,
            'due_at': _due('return_action', rr.created_at),
        }


def _from_disputes():
    """SUP-04 — open disputes/chargebacks are escalated support work."""
    from apps.customers.models import Dispute
    for d in Dispute.objects.filter(
        status__in=['opened', 'under_review', 'escalated'],
    ).select_related('return_request__order_item__order').iterator():
        yield {
            'queue': 'support_case',
            'entity_type': 'Dispute',
            'entity_id': str(d.dispute_id),
            'title': f"Dispute {d.dispute_id} — {d.status}",
            'detail': {
                'order': d.return_request.order_item.order.order_id,
                'item': d.return_request.order_item.item_id,
                'opened_by': getattr(d.opened_by, 'email', ''),
            },
            'priority': 'urgent' if d.status == 'escalated' else 'high',
            'source_status': d.status,
            'due_at': _due('support_case', d.created_at),
        }


def _from_incidents():
    """GOV-04 — open sev1–sev3 incidents are command-center work."""
    from .models import Incident
    for inc in Incident.objects.filter(
        status__in=['open', 'mitigating'],
        severity__in=['sev1', 'sev2', 'sev3'],
    ).iterator():
        yield {
            'queue': 'incident',
            'entity_type': 'Incident',
            'entity_id': str(inc.id),
            'title': f"{inc.severity.upper()}: {inc.title[:160]}",
            'detail': {'severity': inc.severity,
                       'owner': getattr(inc.owner, 'email', '')},
            'priority': 'urgent' if inc.severity == 'sev1' else 'high',
            'source_status': inc.status,
            'due_at': _due('incident', inc.created_at),
        }


SOURCES = (
    _from_support_tickets,
    _from_designer_onboarding,
    _from_catalog_moderation,
    _from_orders,
    _from_reconciliation,
    _from_withdrawals,
    _from_return_requests,
    _from_disputes,
    _from_incidents,
)


def _escalation_conf():
    conf = getattr(settings, 'ESCALATION_RULES', {}) or {}
    return {**ESCALATION_DEFAULTS, **conf}


def apply_escalation_rules() -> int:
    """SUP-04 — mark open work items escalated with the triggering rule.

    Rules: SLA breach, high-value order, urgent ticket, dispute/chargeback,
    repeated designer defects (≥N open disputes), sev1 incident. Idempotent:
    re-running never re-flags an already-escalated item and never clears a
    human's escalation."""
    from apps.customers.models import Dispute
    conf = _escalation_conf()
    now = timezone.now()

    # Designers with a pattern of open disputes.
    defect_designers = set(
        Dispute.objects
        .filter(status__in=['opened', 'under_review', 'escalated'])
        .values_list('return_request__order_item__designer_id', flat=True)
    )
    # Count per designer to apply the threshold.
    from collections import Counter
    counts = Counter(defect_designers)
    defect_designers = {
        d for d, n in counts.items() if n >= conf['repeat_defects']
    }

    escalated = 0
    for item in WorkItem.objects.filter(
        status__in=['open', 'in_progress'], escalated=False,
    ).select_related('assigned_to').iterator():
        reason = None
        if item.due_at and item.due_at < now:
            reason = 'sla_breach'
        if item.queue == 'support_case' and item.priority == 'urgent':
            reason = 'urgent_ticket'
        if item.entity_type == 'Dispute':
            reason = 'dispute_escalation'
        if item.queue == 'incident':
            reason = 'incident'
        if item.queue in ('order_pending', 'dispatch_late',
                          'delivery_exception', 'return_action'):
            try:
                if float((item.detail or {}).get('total')
                         or (item.detail or {}).get('amount') or 0
                         ) >= conf['order_value']:
                    reason = 'high_value'
            except (TypeError, ValueError):
                pass
        if item.entity_type == 'ReturnRequest':
            # repeated designer defects — the return's designer is flagged
            from apps.customers.models import ReturnRequest
            rr = ReturnRequest.objects.filter(
                return_id=item.entity_id).select_related(
                    'order_item').first()
            if rr and rr.order_item.designer_id in defect_designers:
                reason = 'repeat_defects'
        if reason:
            item.escalated = True
            item.escalated_reason = reason
            if item.priority in ('low', 'medium'):
                item.priority = 'high'
            item.save(update_fields=[
                'escalated', 'escalated_reason', 'priority', 'updated_at'])
            escalated += 1
    return escalated


def sync_work_queues() -> dict:
    """Upsert work items from every source; auto-close stale opens."""
    now = timezone.now()
    stats = {'created': 0, 'updated': 0, 'auto_closed': 0, 'sources': {}}

    # Index live items by (queue, entity_type, entity_id).
    live = {
        (i.queue, i.entity_type, i.entity_id): i
        for i in WorkItem.objects.all().iterator()
    }
    seen = set()

    for source in SOURCES:
        count = 0
        try:
            for cand in source():
                key = (cand['queue'], cand['entity_type'], cand['entity_id'])
                seen.add(key)
                count += 1
                existing = live.get(key)
                if existing is None:
                    WorkItem.objects.create(
                        queue=cand['queue'],
                        entity_type=cand['entity_type'],
                        entity_id=cand['entity_id'],
                        title=cand['title'],
                        detail=cand['detail'],
                        priority=cand['priority'],
                        source_status=cand['source_status'],
                        due_at=cand['due_at'],
                    )
                    stats['created'] += 1
                    continue
                if existing.status in ('resolved', 'closed'):
                    continue  # human decision stands
                changed = False
                if existing.source_status != cand['source_status'] \
                        or existing.title != cand['title'] \
                        or existing.priority != cand['priority']:
                    existing.source_status = cand['source_status']
                    existing.title = cand['title']
                    existing.priority = cand['priority']
                    existing.detail = cand['detail']
                    changed = True
                if changed:
                    existing.save(update_fields=[
                        'source_status', 'title', 'priority', 'detail', 'updated_at',
                    ])
                    stats['updated'] += 1
        except Exception:
            logger.exception("Work-queue source %s failed", source.__name__)
        stats['sources'][source.__name__] = count

    # Conditions that cleared: auto-close items whose source no longer yields
    # them (except queues whose sources legitimately yield nothing between
    # runs — those stay until human or explicit close).
    for key, item in live.items():
        if key in seen or item.status in ('resolved', 'closed'):
            continue
        item.status = 'closed'
        item.resolved_at = now
        item.resolution_note = 'source resolved'
        item.save(update_fields=['status', 'resolved_at', 'resolution_note',
                                 'updated_at'])
        stats['auto_closed'] += 1

    stats['escalated'] = apply_escalation_rules()
    return stats
