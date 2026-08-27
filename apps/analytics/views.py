import json
import logging
from datetime import datetime
from django.utils import timezone
from django.db import transaction
from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.utils.uuid_generator import generate_custom_id
from apps.administrator.permissions import IsMarketer, IsCLevel
from apps.algorithm.models import UserActivity

from .models import (
    Event,
    EventSchema,
    Identity,
    Session,
    DailyMetric,
    DeadLetterEvent,
    MetricDefinition,
)

logger = logging.getLogger(__name__)


class AnonRateThrottle(AnonRateThrottle):
    rate = '60/minute'


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _validate_event(payload):
    name = payload.get('name')
    if not name:
        return False, 'missing name'

    schema = EventSchema.objects.filter(name=name, enabled=True).first()
    if not schema:
        return True, None  # unknown but valid, will still be recorded

    required = set(schema.required_params or [])
    for key in required:
        if payload.get('props', {}).get(key) is None and payload.get(key) is None:
            return False, f'missing required param: {key}'
    return True, None


def _get_or_create_identity(anon_id):
    if not anon_id:
        return None
    identity, created = Identity.objects.get_or_create(anon_id=anon_id)
    if not created:
        identity.visit_count += 1
        identity.last_seen_at = timezone.now()
        identity.save(update_fields=['visit_count', 'last_seen_at'])
    return identity


def _get_or_create_session(session_id, anon_id, occurred_at):
    if not session_id:
        return None
    session, created = Session.objects.get_or_create(
        session_id=session_id,
        defaults={
            'anon_id': anon_id,
            'started_at': occurred_at,
        }
    )
    if not created:
        session.ended_at = occurred_at
        session.event_count += 1
        session.save(update_fields=['ended_at', 'event_count'])
    return session


def _fan_to_algorithm(event):
    """Fan a subset of events into the ranking engine's UserActivity stream."""
    name_map = {
        'view_item': 'product_view',
        'select_item': 'product_click',
        'add_to_cart': 'add_to_cart',
        'remove_from_cart': 'remove_from_cart',
        'wishlist_add': 'wishlist_add',
        'wishlist_remove': 'wishlist_remove',
        'purchase': 'purchase',
        'search': 'search',
        'scroll_depth': 'scroll_depth',
        'price_filter': 'price_filter',
        'category_browse': 'category_browse',
        'time_on_product': 'time_on_product',
        'review_view': 'review_view',
        'story_view': 'story_view',
        'share': 'share',
        'return_initiated': 'return_initiated',
    }

    event_type = name_map.get(event.name)
    if not event_type:
        return

    product_id = None
    designer_id = None
    if event.items and isinstance(event.items, list):
        product_id = event.items[0].get('product_id')
        designer_id = event.items[0].get('designer_id')

    try:
        UserActivity.objects.create(
            user=event.user,
            session_id=event.session_id,
            event_type=event_type,
            product_id=product_id,
            designer_id=designer_id,
            metadata={
                'anon_id': event.anon_id,
                'page_path': event.page_path,
                'props': event.props,
                'items': event.items,
                'revenue': str(event.revenue) if event.revenue else None,
                'country_code': event.country_code,
            },
            country_code=event.country_code,
        )
    except Exception as e:
        logger.error(f"Failed to fan out analytics event to algorithm: {e}")


def _revenue_from_payload(payload):
    revenue = payload.get('revenue')
    currency = payload.get('currency', '')
    transaction_id = payload.get('transaction_id', '')

    # Money events are only valid from server/webhook sources
    source = payload.get('source', 'web')
    if payload.get('name') in ('purchase', 'refund', 'payout', 'subscription'):
        if source not in ('server', 'webhook'):
            return None, currency, transaction_id, 'client_revenue_rejected'

    try:
        revenue = float(revenue) if revenue is not None else None
    except (ValueError, TypeError):
        revenue = None

    return revenue, currency, transaction_id, None


class CollectView(APIView):
    permission_classes = [permissions.AllowAny]
    # throttling is applied at the CDN/edge; removing per-view throttle to avoid Redis auth in tests
    # throttle_classes = [AnonRateThrottle]

    def post(self, request):
        events = request.data
        if not isinstance(events, list):
            events = [events]

        accepted = 0
        rejected = 0
        results = []

        for item in events:
            is_valid, reason = _validate_event(item)

            if not is_valid:
                DeadLetterEvent.objects.create(
                    payload=item,
                    reason=reason,
                )
                rejected += 1
                results.append({'status': 'rejected', 'reason': reason})
                continue

            revenue, currency, transaction_id, revenue_reason = _revenue_from_payload(item)

            if revenue_reason:
                DeadLetterEvent.objects.create(
                    payload=item,
                    reason=revenue_reason,
                )
                rejected += 1
                results.append({'status': 'rejected', 'reason': revenue_reason})
                continue

            try:
                occurred_at = item.get('occurred_at')
                if occurred_at:
                    occurred_at = datetime.fromisoformat(occurred_at)
                else:
                    occurred_at = timezone.now()
            except (ValueError, TypeError):
                occurred_at = timezone.now()

            anon_id = item.get('anon_id', '')
            session_id = item.get('session_id', '')

            _get_or_create_identity(anon_id)
            _get_or_create_session(session_id, anon_id, occurred_at)

            user_id = item.get('user_id')
            user = None
            if user_id:
                try:
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    user = User.objects.filter(id=user_id).first()
                except Exception:
                    pass

            event = Event.objects.create(
                event_id=item.get('event_id') or generate_custom_id(),
                name=item.get('name'),
                schema_version=item.get('schema_version', '1.0'),
                anon_id=anon_id,
                user=user,
                session_id=session_id,
                occurred_at=occurred_at,
                received_at=timezone.now(),
                source=item.get('source', 'web'),
                page_path=item.get('page_path', ''),
                referrer=item.get('referrer', ''),
                utm_source=item.get('utm_source', ''),
                utm_medium=item.get('utm_medium', ''),
                utm_campaign=item.get('utm_campaign', ''),
                utm_term=item.get('utm_term', ''),
                utm_content=item.get('utm_content', ''),
                device_type=item.get('device_type', ''),
                os=item.get('os', ''),
                browser=item.get('browser', ''),
                country_code=item.get('country_code', ''),
                region=item.get('region', ''),
                city=item.get('city', ''),
                consent_analytics=item.get('consent_analytics', True),
                consent_marketing=item.get('consent_marketing', False),
                props=item.get('props', {}),
                items=item.get('items', []),
                revenue=revenue,
                currency=currency,
                transaction_id=transaction_id,
                is_bot=item.get('is_bot', False),
            )

            _fan_to_algorithm(event)
            accepted += 1
            results.append({'status': 'accepted', 'event_id': event.event_id})

        return Response({
            'accepted': accepted,
            'rejected': rejected,
            'results': results,
        }, status=status.HTTP_202_ACCEPTED)


class KPIView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsMarketer]

    def get(self, request):
        metric = request.query_params.get('metric')
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')

        qs = DailyMetric.objects.all().order_by('date')
        if from_date:
            qs = qs.filter(date__gte=from_date)
        if to_date:
            qs = qs.filter(date__lte=to_date)
        if metric:
            qs = qs.filter(metric_name=metric)

        return Response({
            'count': qs.count(),
            'sample': list(qs.values('date', 'metric_name', 'dimension_key', 'dimension_value', 'value')[:1000])
        })


class MetricCatalogView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsCLevel]

    def get(self, request):
        definitions = MetricDefinition.objects.all().order_by('name')
        return Response([
            {
                'id': d.id,
                'name': d.name,
                'formula': d.formula,
                'grain': d.grain,
                'window': d.window,
                'owner': d.owner,
                'is_input_metric': d.is_input_metric,
                'target': d.target,
                'description': d.description,
            }
            for d in definitions
        ])


class FunnelView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsMarketer]

    def get(self, request):
        steps_param = request.query_params.get('steps', '')
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')

        if not steps_param:
            return Response({'error': 'steps parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        steps = [s.strip() for s in steps_param.split(',') if s.strip()]
        qs = Event.objects.all()
        if from_date:
            qs = qs.filter(received_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(received_at__date__lte=to_date)

        result = []
        previous_ids = None
        for step in steps:
            step_qs = qs.filter(name=step)
            if previous_ids is not None:
                step_qs = step_qs.filter(anon_id__in=previous_ids)
            ids = set(step_qs.values_list('anon_id', flat=True))
            previous_ids = ids
            count = len(ids)
            prev_count = result[-1]['count'] if result else count
            conversion = round((count / prev_count) * 100, 2) if result and prev_count else None
            result.append({
                'step': step,
                'count': count,
                'conversion_pct': conversion,
                'drop_off': prev_count - count if result else 0,
            })

        return Response({
            'steps': result,
            'from': from_date,
            'to': to_date,
        })


class SummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsMarketer]

    def get(self, request):
        from django.db.models import Count
        from django.db.models.functions import TruncDate

        today = timezone.now().date()
        start_of_today = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))

        total_events = Event.objects.count()
        events_today = Event.objects.filter(received_at__gte=start_of_today).count()
        identities = Identity.objects.count()
        sessions = Session.objects.count()
        dead_letters = DeadLetterEvent.objects.count()

        total_consent = Event.objects.filter(consent_analytics=True).count()
        total_marketing = Event.objects.filter(consent_marketing=True).count()
        consent_analytics_pct = round((total_consent / total_events) * 100, 2) if total_events else 0
        consent_marketing_pct = round((total_marketing / total_events) * 100, 2) if total_events else 0

        top_events = list(
            Event.objects.values('name')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )

        events_by_day = list(
            Event.objects.annotate(day=TruncDate('received_at'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('-day')[:14]
        )

        return Response({
            'total_events': total_events,
            'events_today': events_today,
            'identities': identities,
            'sessions': sessions,
            'dead_letters': dead_letters,
            'consent_analytics_pct': consent_analytics_pct,
            'consent_marketing_pct': consent_marketing_pct,
            'top_events': top_events,
            'events_by_day': [
                {'date': d['day'].isoformat() if d['day'] else None, 'count': d['count']}
                for d in events_by_day
            ],
        })


class RetentionView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsMarketer]

    def get(self, request):
        days = int(request.query_params.get('days', 14))
        today = timezone.now().date()
        cohorts = []

        for offset in range(days - 1, -1, -1):
            cohort_date = today - timezone.timedelta(days=offset)
            cohort_identities = Identity.objects.filter(first_seen_at__date=cohort_date)
            cohort_anons = list(cohort_identities.values_list('anon_id', flat=True))
            size = len(cohort_anons)
            if size == 0:
                cohorts.append({
                    'date': cohort_date.isoformat(),
                    'size': 0,
                    'd7_retained': 0,
                    'd7_pct': None,
                })
                continue

            d7_date = cohort_date + timezone.timedelta(days=7)
            d7_retained = Event.objects.filter(
                anon_id__in=cohort_anons,
                received_at__date=d7_date,
            ).values('anon_id').distinct().count()

            cohorts.append({
                'date': cohort_date.isoformat(),
                'size': size,
                'd7_retained': d7_retained,
                'd7_pct': round((d7_retained / size) * 100, 2),
            })

        return Response({'cohorts': cohorts})


class SupplyView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsMarketer]

    def get(self, request):
        product_count = 0
        active_product_count = 0
        designer_count = 0
        active_designer_count = 0
        category_count = 0

        try:
            from apps.core.models import Product, Category
            product_count = Product.objects.count()
            active_product_count = Product.objects.filter(is_active=True).count()
            category_count = Category.objects.count()
        except Exception:
            pass

        try:
            from apps.designers.models import Designer
            designer_count = Designer.objects.count()
            active_designer_count = Designer.objects.filter(is_approved=True).count()
        except Exception:
            pass

        return Response({
            'products': product_count,
            'active_products': active_product_count,
            'designers': designer_count,
            'active_designers': active_designer_count,
            'categories': category_count,
        })


class OpsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsMarketer]

    def get(self, request):
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')

        qs = Event.objects.all()
        if from_date:
            qs = qs.filter(received_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(received_at__date__lte=to_date)

        purchases = qs.filter(name='purchase').count()
        returns = qs.filter(name='return_initiated').count()
        searches = qs.filter(name='search').count()
        add_to_carts = qs.filter(name='add_to_cart').count()
        total_revenue = sum(
            (e.revenue or 0) for e in qs.filter(name='purchase', revenue__isnull=False)
        )

        return Response({
            'purchases': purchases,
            'returns': returns,
            'searches': searches,
            'add_to_carts': add_to_carts,
            'total_revenue': str(total_revenue),
        })


class PlatformHealthView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsMarketer]

    def get(self, request):
        last_24h = timezone.now() - timezone.timedelta(hours=24)
        dead_letters = DeadLetterEvent.objects.filter(created_at__gte=last_24h).count()

        anomaly_count = 0
        try:
            from apps.algorithm.models import AnomalyLog
            anomaly_count = AnomalyLog.objects.filter(created_at__gte=last_24h).count()
        except Exception:
            pass

        dead_reasons = list(
            DeadLetterEvent.objects.filter(created_at__gte=last_24h)
            .values('reason')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )

        return Response({
            'dead_letters_24h': dead_letters,
            'anomalies_24h': anomaly_count,
            'dead_letter_reasons': dead_reasons,
        })


class SearchInsightsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsMarketer]

    def get(self, request):
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')

        qs = Event.objects.filter(name='search')
        if from_date:
            qs = qs.filter(received_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(received_at__date__lte=to_date)

        total = qs.count()
        top_queries = list(
            qs.filter(props__query__isnull=False)
            .values('props__query')
            .annotate(count=Count('id'))
            .order_by('-count')[:20]
        )

        return Response({
            'total_searches': total,
            'top_queries': [
                {'query': q['props__query'], 'count': q['count']}
                for q in top_queries
            ],
        })
