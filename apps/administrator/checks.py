"""Daily data-health checks (PRD Phase 0 — instrument data health).

Canonical sources of truth:
- Money: ``pay.Payment`` with ``is_paid=True, is_deleted=False`` — provider-
  confirmed capture only (webhook/server verified).
- Orders: ``customers.Order`` linked to a paid ``Invoice.payment``.
- Events: ``analytics.Event`` (schema-validated, dead-lettered when malformed).

Each check writes a ``DataQualityCheck`` row; the payment↔order sweep also
writes a ``ReconciliationRun`` with one ``ReconciliationException`` per
unmatched row — the work queue finance uses to close gaps.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from .models import DataQualityCheck, ReconciliationException, ReconciliationRun

logger = logging.getLogger(__name__)

# Purchase events are behavioral evidence — undercounting against server
# orders is expected (adblockers), overcounting is a red flag.
PURCHASE_EVENT_OVERCOUNT_RATIO = Decimal('1.5')
DEAD_LETTER_WARN = 1
DEAD_LETTER_BREACH = 100
WEBHOOK_BACKLOG_WARN = 1
WEBHOOK_BACKLOG_BREACH = 50
METRIC_STALENESS_HOURS = 48


def _window(days: int):
    end = timezone.now()
    return end - timedelta(days=days), end


def _record_check(name, status, expected=None, observed=None, details=None,
                  window=None):
    return DataQualityCheck.objects.create(
        check_name=name,
        status=status,
        expected=expected or {},
        observed=observed or {},
        details=details or {},
        window_start=window[0] if window else None,
        window_end=window[1] if window else None,
    )


def reconcile_payments_vs_orders(*, days=30, actor=None) -> ReconciliationRun:
    """Compare provider-confirmed payments to orders in the window.

    Every paid Payment must back at least one order; every order must be
    backed by a paid Payment. Unmatched rows become open exceptions.
    """
    from apps.customers.models import Order
    from apps.pay.models import Invoice, Payment

    start, end = _window(days)
    run = ReconciliationRun.objects.create(
        name='payments_vs_orders',
        window_start=start, window_end=end,
        triggered_by=actor,
    )
    try:
        payments = Payment.objects.filter(
            is_paid=True, is_deleted=False,
        ).filter(
            Q(date_time_paid__gte=start, date_time_paid__lt=end)
            | Q(date_time_paid__isnull=True,
                date_time_added__gte=start, date_time_added__lt=end)
        )
        orders = Order.objects.filter(
            created_at__gte=start, created_at__lt=end,
        ).select_related('invoice__payment')

        payment_by_ref = {p.reference: p for p in payments}
        paid_refs_with_orders = set()
        exceptions = []

        for order in orders:
            payment = order.invoice.payment if order.invoice else None
            if payment and payment.is_paid and not payment.is_deleted:
                paid_refs_with_orders.add(payment.reference)
            else:
                exceptions.append(ReconciliationException(
                    run=run, entity_type='order', entity_id=order.order_id,
                    issue='order_without_paid_payment',
                    detail={
                        'order_status': order.status,
                        'payment_reference': getattr(payment, 'reference', None),
                        'payment_status': getattr(payment, 'status', None),
                        'total_amount': str(order.total_amount),
                    },
                ))

        for ref, payment in payment_by_ref.items():
            has_order = (
                ref in paid_refs_with_orders
                or Invoice.objects.filter(
                    payment=payment, invoices__isnull=False
                ).exists()
            )
            if not has_order:
                exceptions.append(ReconciliationException(
                    run=run, entity_type='payment', entity_id=ref,
                    issue='payment_without_order',
                    detail={
                        'amount': str(payment.amount),
                        'currency': payment.currency,
                        'processor': payment.processor,
                        'status': payment.status,
                    },
                ))

        if exceptions:
            ReconciliationException.objects.bulk_create(exceptions)

        payment_total = payments.aggregate(t=Sum('amount'))['t'] or 0
        order_total = sum(
            (o.total_amount for o in orders
             if o.invoice and o.invoice.payment
             and o.invoice.payment.is_paid and not o.invoice.payment.is_deleted),
            Decimal('0'),
        )
        run.summary = {
            'paid_payments': len(payment_by_ref),
            'orders_in_window': len(orders),
            'paid_payment_total': str(payment_total),
            'paid_order_total': str(order_total),
        }
        run.exception_count = len(exceptions)
        run.status = 'matched' if not exceptions else 'mismatch'
    except Exception as e:
        run.status = 'failed'
        run.error = str(e)
        logger.exception("payments_vs_orders reconciliation failed")
    run.finished_at = timezone.now()
    run.save()
    return run


def check_purchase_events_vs_orders(*, days=1):
    """Analytics 'purchase' events vs paid orders — drift flags tracking gaps.

    Events are client-originated, so undercounting is expected (adblockers).
    Breach only on overcount > 1.5x orders, or any events when orders exist
    but zero purchase events arrived (tracking silently dead).
    """
    from apps.analytics.models import Event
    from apps.customers.models import Order

    start, end = _window(days)
    events = Event.objects.filter(
        name='purchase', is_valid=True, received_at__gte=start,
        received_at__lt=end,
    ).count()
    paid_orders = Order.objects.filter(
        created_at__gte=start, created_at__lt=end,
        invoice__payment__is_paid=True,
        invoice__payment__is_deleted=False,
    ).count()

    status_ = 'ok'
    note = ''
    if paid_orders and events == 0:
        status_ = 'warn'
        note = 'paid orders exist but no purchase events were ingested'
    elif events and events > paid_orders * float(PURCHASE_EVENT_OVERCOUNT_RATIO):
        status_ = 'warn'
        note = 'purchase events exceed paid orders beyond tolerance'
    return _record_check(
        'purchase_events_vs_orders', status_,
        expected={'paid_orders': paid_orders},
        observed={'purchase_events': events},
        details={'note': note, 'basis': 'events are behavioral evidence only'},
        window=(start, end),
    )


def check_dead_letter_backlog(*, days=1):
    """Malformed analytics events must be visible, not silently dropped."""
    from apps.analytics.models import DeadLetterEvent

    start, end = _window(days)
    recent = DeadLetterEvent.objects.filter(created_at__gte=start)
    count = recent.count()
    status_ = 'ok'
    if count >= DEAD_LETTER_BREACH:
        status_ = 'breach'
    elif count >= DEAD_LETTER_WARN:
        status_ = 'warn'
    return _record_check(
        'dead_letter_backlog', status_,
        expected={'max': DEAD_LETTER_WARN - 1},
        observed={'count': count},
        details={'sample_reasons': list(
            recent.values_list('reason', flat=True)[:5]
        )},
        window=(start, end),
    )


def check_metric_freshness():
    """Stale rollups must surface as stale — never as zero."""
    from apps.analytics.models import DailyMetric, MetricDefinition

    stale_after = timezone.now() - timedelta(hours=METRIC_STALENESS_HOURS)
    defined = list(MetricDefinition.objects.values_list('name', flat=True))
    fresh = set(
        DailyMetric.objects.filter(updated_at__gte=stale_after)
        .values_list('metric_name', flat=True)
    )
    stale = sorted(set(defined) - fresh) if defined else []
    return _record_check(
        'metric_freshness',
        'warn' if stale else 'ok',
        expected={'metrics': defined, 'fresh_within_hours': METRIC_STALENESS_HOURS},
        observed={'fresh': sorted(fresh & set(defined)), 'stale': stale},
        details={'note': 'stale metrics must render as delayed, not zero'},
    )


def check_webhook_backlog(*, days=1):
    """Unprocessed payment-webhook rows are failed money evidence."""
    from apps.pay.models import PaymentWebhookLog

    start, end = _window(days)
    backlog = PaymentWebhookLog.objects.filter(
        processed=False, created_at__gte=start,
    )
    count = backlog.count()
    status_ = 'ok'
    if count >= WEBHOOK_BACKLOG_BREACH:
        status_ = 'breach'
    elif count >= WEBHOOK_BACKLOG_WARN:
        status_ = 'warn'
    return _record_check(
        'webhook_backlog', status_,
        expected={'max': WEBHOOK_BACKLOG_WARN - 1},
        observed={'count': count},
        details={'sample_refs': list(
            backlog.values_list('reference', flat=True)[:5]
        )},
        window=(start, end),
    )


def run_all_checks(*, days=30, actor=None) -> dict:
    """Execute every health check; returns a small summary for the caller."""
    run = reconcile_payments_vs_orders(days=days, actor=actor)
    checks = [
        check_purchase_events_vs_orders(),
        check_dead_letter_backlog(),
        check_metric_freshness(),
        check_webhook_backlog(),
    ]
    # Mirror the reconciliation outcome as a named check so dashboards only
    # need DataQualityCheck rows.
    _record_check(
        'payments_vs_orders',
        'ok' if run.status == 'matched' else 'breach',
        expected=run.summary,
        observed={'status': run.status, 'exceptions': run.exception_count},
        details={'run_id': run.id, 'error': run.error},
        window=(run.window_start, run.window_end),
    )
    return {
        'reconciliation': {'id': run.id, 'status': run.status,
                           'exceptions': run.exception_count},
        'checks': [{'name': c.check_name, 'status': c.status} for c in checks],
    }
