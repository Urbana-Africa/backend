"""
Staff-only visitor-analytics reports powering the admin /analytics console.

All endpoints are read-only and require `IsStaffAdmin` (any admin role).
Responses use the standard `{status, data, meta}` envelope; every report
carries `data.range = {days, start, end}` (inclusive of today). Money-ish
fields are serialized as string decimals; a `null` conversion rate means the
base was zero.

Urbana mapping of the funnel: Landing visit (pageview on `/`) -> Signup ->
Login -> Checkout initiated -> Purchase. "Creators" are designers, joined via
`Designer.slug`/`Designer.id` to storefront paths (`/d/<slug-or-id>`) and to
delivered `OrderItem` rows for revenue.
"""

import json
from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PageView, VisitorEvent
from .permissions import IsStaffAdmin

PROPS_SAMPLE_LIMIT = 5000
CONVERSION_CATEGORY = 'conversion'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ok(data, meta=None):
    return Response({'status': 'success', 'data': data, 'meta': meta or {}})


def _days_param(request, default=30):
    try:
        days = int(request.query_params.get('days', default))
    except (TypeError, ValueError):
        days = default
    return max(1, min(days, 365))


def _limit_param(request, default=20, max_limit=100):
    try:
        limit = int(request.query_params.get('limit', default))
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, max_limit))


def _range(days):
    end = timezone.now()
    start = (end - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def _range_payload(days, start, end):
    return {'days': days, 'start': start.date().isoformat(), 'end': end.date().isoformat()}


def _pageviews(start, end):
    return PageView.objects.filter(created_at__range=(start, end))


def _events(start, end):
    # `pageview`-category rows are excluded from every event count.
    return VisitorEvent.objects.filter(created_at__range=(start, end)).exclude(category='pageview')


def _date_series(days, start):
    return [(start + timedelta(days=i)).date() for i in range(days)]


def _page_timeseries(pvs, days, start):
    rows = (pvs.annotate(day=TruncDate('created_at'))
               .values('day')
               .annotate(pageviews=Count('id'),
                         visitors=Count('session_hash', distinct=True)))
    by_day = {r['day']: r for r in rows}
    return [
        {
            'date': d.isoformat(),
            'pageviews': by_day.get(d, {}).get('pageviews', 0),
            'visitors': by_day.get(d, {}).get('visitors', 0),
        }
        for d in _date_series(days, start)
    ]


def _event_timeseries(evs, days, start):
    rows = (evs.annotate(day=TruncDate('created_at'))
               .values('day')
               .annotate(count=Count('id'),
                         sessions=Count('session_hash', distinct=True)))
    by_day = {r['day']: r for r in rows}
    return [
        {
            'date': d.isoformat(),
            'count': by_day.get(d, {}).get('count', 0),
            'sessions': by_day.get(d, {}).get('sessions', 0),
        }
        for d in _date_series(days, start)
    ]


def _devices(pvs):
    return {r['device']: r['count'] for r in pvs.values('device').annotate(count=Count('id'))}


def _top_referrers(pvs, limit=10):
    return [
        {'referrer': r['referrer'], 'count': r['count']}
        for r in (pvs.exclude(referrer='')
                     .values('referrer')
                     .annotate(count=Count('id'))
                     .order_by('-count')[:limit])
    ]


def _avg_ms(qs):
    return qs.filter(duration_ms__gt=0).aggregate(avg=Avg('duration_ms'))['avg']


def _dec(value):
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------

class VisitorOverviewView(APIView):
    permission_classes = [IsStaffAdmin]

    def get(self, request):
        days = _days_param(request)
        start, end = _range(days)
        pvs = _pageviews(start, end)
        evs = _events(start, end)

        return _ok({
            'range': _range_payload(days, start, end),
            'totals': {
                'pageviews': pvs.count(),
                'unique_visitors': pvs.aggregate(n=Count('session_hash', distinct=True))['n'],
                'events': evs.count(),
            },
            'timeseries': _page_timeseries(pvs, days, start),
            'devices': _devices(pvs),
            'top_referrers': _top_referrers(pvs),
        })


class VisitorPagesView(APIView):
    permission_classes = [IsStaffAdmin]

    def get(self, request):
        days = _days_param(request)
        limit = _limit_param(request, default=20, max_limit=100)
        start, end = _range(days)
        pvs = _pageviews(start, end)
        evs = _events(start, end)

        rows = (pvs.values('path')
                   .annotate(views=Count('id'),
                             visitors=Count('session_hash', distinct=True),
                             avg_duration_ms=Avg('duration_ms', filter=Q(duration_ms__gt=0)))
                   .order_by('-views', 'path')[:limit])

        event_counts = {
            r['path']: r['count']
            for r in evs.exclude(path='').values('path').annotate(count=Count('id'))
        }

        return _ok({
            'range': _range_payload(days, start, end),
            'pages': [
                {
                    'path': r['path'],
                    'views': r['views'],
                    'visitors': r['visitors'],
                    'avg_duration_ms': int(r['avg_duration_ms'] or 0),
                    'events': event_counts.get(r['path'], 0),
                }
                for r in rows
            ],
        })


class VisitorEventsView(APIView):
    permission_classes = [IsStaffAdmin]

    def get(self, request):
        days = _days_param(request)
        limit = _limit_param(request, default=20, max_limit=100)
        category = request.query_params.get('category')
        start, end = _range(days)
        evs = _events(start, end)
        if category:
            evs = evs.filter(category=category[:50])

        rows = (evs.values('name', 'category')
                   .annotate(count=Count('id'), value_total=Sum('value'))
                   .order_by('-count', 'name')[:limit])

        return _ok({
            'range': _range_payload(days, start, end),
            'events': [
                {
                    'name': r['name'],
                    'category': r['category'],
                    'count': r['count'],
                    'value_total': _dec(r['value_total']),
                }
                for r in rows
            ],
        })


FUNNEL_STAGES = [
    ('landing', 'Landing visit'),
    ('signup', 'Signup'),
    ('login', 'Login'),
    ('checkout_initiated', 'Checkout initiated'),
    ('purchase', 'Purchase'),
]


class VisitorFunnelView(APIView):
    permission_classes = [IsStaffAdmin]

    def get(self, request):
        days = _days_param(request)
        start, end = _range(days)
        pvs = _pageviews(start, end)
        evs = _events(start, end)

        funnel = []
        previous = None
        for key, label in FUNNEL_STAGES:
            if key == 'landing':
                count = pvs.filter(path='/').aggregate(
                    n=Count('session_hash', distinct=True))['n']
            else:
                count = evs.filter(name=key).aggregate(
                    n=Count('session_hash', distinct=True))['n']
            funnel.append({
                'stage': label,
                'key': key,
                'count': count,
                'conversion_rate': round(count / previous * 100, 1) if previous else None,
            })
            previous = count

        return _ok({'range': _range_payload(days, start, end), 'funnel': funnel})


class VisitorPageDetailView(APIView):
    permission_classes = [IsStaffAdmin]

    def get(self, request):
        path = request.query_params.get('path', '')[:500]
        if not path:
            return Response(
                {'status': 'error', 'message': 'path parameter is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        days = _days_param(request)
        start, end = _range(days)
        pvs = _pageviews(start, end).filter(path=path)
        evs = _events(start, end)
        sessions_on_path = pvs.values('session_hash').distinct()

        initiated = evs.filter(name='checkout_initiated', session_hash__in=sessions_on_path) \
            .aggregate(n=Count('session_hash', distinct=True))['n']
        completed = evs.filter(name='purchase', session_hash__in=sessions_on_path) \
            .aggregate(n=Count('session_hash', distinct=True))['n']
        visitors = pvs.aggregate(n=Count('session_hash', distinct=True))['n']

        events = [
            {
                'name': r['name'],
                'category': r['category'],
                'count': r['count'],
                'sessions': r['sessions'],
                'value_total': _dec(r['value_total']),
            }
            for r in (evs.filter(path=path)
                         .values('name', 'category')
                         .annotate(count=Count('id'),
                                   sessions=Count('session_hash', distinct=True),
                                   value_total=Sum('value'))
                         .order_by('-count', 'name'))
        ]

        return _ok({
            'range': _range_payload(days, start, end),
            'path': path,
            'totals': {
                'views': pvs.count(),
                'visitors': visitors,
                'avg_duration_ms': int(_avg_ms(pvs) or 0),
                'initiated': initiated,
                'completed': completed,
                'conversion_rate': round(completed / visitors * 100, 1) if visitors else None,
            },
            'timeseries': _page_timeseries(pvs, days, start),
            'devices': _devices(pvs),
            'top_referrers': _top_referrers(pvs),
            'events': events,
        })


class VisitorEventDetailView(APIView):
    permission_classes = [IsStaffAdmin]

    def get(self, request):
        name = request.query_params.get('name', '')[:100]
        if not name:
            return Response(
                {'status': 'error', 'message': 'name parameter is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        days = _days_param(request)
        start, end = _range(days)
        evs = _events(start, end).filter(name=name)

        agg = evs.aggregate(
            count=Count('id'),
            sessions=Count('session_hash', distinct=True),
            value_total=Sum('value'),
            value_avg=Avg('value'),
            value_min=Min('value'),
            value_max=Max('value'),
        )

        counters = defaultdict(Counter)
        for props in evs.order_by('-created_at').values_list('props', flat=True)[:PROPS_SAMPLE_LIMIT]:
            if not isinstance(props, dict):
                continue
            for key, value in props.items():
                if value is None or isinstance(value, (dict, list)):
                    continue
                counters[key][str(value)] += 1
        props_breakdown = {
            key: [{'value': v, 'count': c} for v, c in counter.most_common(10)]
            for key, counter in counters.items()
        }

        return _ok({
            'range': _range_payload(days, start, end),
            'name': name,
            'category': evs.values_list('category', flat=True).first() or 'custom',
            'totals': {
                'count': agg['count'],
                'sessions': agg['sessions'],
                'value_total': _dec(agg['value_total']),
                'value_avg': _dec(agg['value_avg']),
                'value_min': _dec(agg['value_min']),
                'value_max': _dec(agg['value_max']),
            },
            'timeseries': _event_timeseries(evs, days, start),
            'top_paths': [
                {'path': r['path'], 'count': r['count']}
                for r in (evs.exclude(path='')
                             .values('path')
                             .annotate(count=Count('id'))
                             .order_by('-count')[:10])
            ],
            'props': props_breakdown,
        })


class VisitorDesignersView(APIView):
    """Per-designer storefront traffic joined to delivered OrderItem revenue."""

    permission_classes = [IsStaffAdmin]

    def get(self, request):
        from apps.customers.models import OrderItem
        from apps.designers.models import Designer

        days = _days_param(request)
        start, end = _range(days)
        pvs = _pageviews(start, end)
        evs = _events(start, end)

        designers = list(
            Designer.objects.select_related('user')
            .values('id', 'slug', 'brand_name', 'is_active', 'user__username', 'user_id')
        )

        path_to_designer = {}
        for d in designers:
            for candidate in {f'/d/{d["slug"]}' if d['slug'] else '', f'/d/{d["id"]}'}:
                if candidate:
                    path_to_designer[candidate] = d

        paths = list(path_to_designer)
        pv_stats = {}
        if paths:
            for r in (pvs.filter(path__in=paths)
                         .values('path')
                         .annotate(views=Count('id'),
                                   visitors=Count('session_hash', distinct=True),
                                   avg_duration_ms=Avg('duration_ms', filter=Q(duration_ms__gt=0)))):
                pv_stats[r['path']] = r

            path_sessions = defaultdict(set)
            for path, session in pvs.filter(path__in=paths).values_list('path', 'session_hash').distinct():
                path_sessions[path].add(session)
        else:
            path_sessions = defaultdict(set)

        initiated_sessions = set(evs.filter(name='checkout_initiated')
                                    .values_list('session_hash', flat=True).distinct())
        purchase_sessions = set(evs.filter(name='purchase')
                                   .values_list('session_hash', flat=True).distinct())

        user_ids = [d['user_id'] for d in designers]
        orders = defaultdict(lambda: {'count': 0, 'revenue': defaultdict(float)})
        if user_ids:
            for r in (OrderItem.objects
                         .filter(designer_id__in=user_ids, status='delivered',
                                 created_at__range=(start, end))
                         .values('designer_id', 'order__invoice__payment__currency')
                         .annotate(count=Count('id'), total=Sum('sub_total'))):
                cur = r['order__invoice__payment__currency'] or 'USD'
                entry = orders[r['designer_id']]
                entry['count'] += r['count']
                entry['revenue'][cur] += float(r['total'] or 0)

        rows = []
        for d in designers:
            paths_for = [p for p, owner in path_to_designer.items() if owner['id'] == d['id']]
            views = sum(pv_stats[p]['views'] for p in paths_for if p in pv_stats)
            sessions = set().union(*(path_sessions[p] for p in paths_for)) if paths_for else set()
            visitors = len(sessions)
            durations = [pv_stats[p]['avg_duration_ms'] for p in paths_for
                         if p in pv_stats and pv_stats[p]['avg_duration_ms']]
            avg_duration_ms = int(sum(durations) / len(durations)) if durations else 0
            initiated = len(sessions & initiated_sessions)
            completed = len(sessions & purchase_sessions)
            order_stats = orders[d['user_id']]

            rows.append({
                'slug': d['slug'] or '',
                'path': f"/d/{d['slug'] or d['id']}",
                'name': d['brand_name'] or d['user__username'],
                'username': d['user__username'],
                'is_active': bool(d['is_active']),
                'views': views,
                'visitors': visitors,
                'avg_duration_ms': avg_duration_ms,
                'initiated': initiated,
                'completed': completed,
                'conversion_rate': round(completed / visitors * 100, 1) if visitors else None,
                'orders_count': order_stats['count'],
                'revenue': {cur: f'{amt:.2f}' for cur, amt in order_stats['revenue'].items()},
            })

        rows.sort(key=lambda r: (-r['views'], -r['orders_count'], r['name'] or ''))
        return _ok({'range': _range_payload(days, start, end), 'designers': rows})


class VisitorSessionsView(APIView):
    permission_classes = [IsStaffAdmin]

    def get(self, request):
        days = _days_param(request, default=7)
        limit = _limit_param(request, default=50, max_limit=200)
        start, end = _range(days)
        pvs = _pageviews(start, end)
        evs = _events(start, end)

        recent = list(
            pvs.values('session_hash')
               .annotate(last_seen=Max('created_at'))
               .order_by('-last_seen')[:limit]
        )
        hashes = [r['session_hash'] for r in recent]
        if not hashes:
            return _ok({'range': _range_payload(days, start, end), 'sessions': []})

        per_session = {}
        for row in (pvs.filter(session_hash__in=hashes)
                       .values('session_hash', 'path', 'device', 'created_at')
                       .order_by('created_at')):
            entry = per_session.setdefault(row['session_hash'], {
                'first_seen': row['created_at'], 'last_seen': row['created_at'],
                'entry_path': row['path'], 'exit_path': row['path'],
                'device': row['device'], 'views': 0,
            })
            entry['views'] += 1
            entry['last_seen'] = row['created_at']
            entry['exit_path'] = row['path']
            entry['device'] = row['device']
            if row['created_at'] < entry['first_seen']:
                entry['first_seen'] = row['created_at']
                entry['entry_path'] = row['path']

        event_stats = {
            r['session_hash']: r
            for r in (evs.filter(session_hash__in=hashes)
                         .values('session_hash')
                         .annotate(events=Count('id'),
                                   conversions=Count('id', filter=Q(category=CONVERSION_CATEGORY))))
        }

        sessions = []
        for r in recent:
            session = r['session_hash']
            info = per_session.get(session)
            if not info:
                continue
            stats = event_stats.get(session, {})
            sessions.append({
                'session': session,
                'device': info['device'],
                'entry_path': info['entry_path'],
                'exit_path': info['exit_path'],
                'views': info['views'],
                'events': stats.get('events', 0),
                'converted': bool(stats.get('conversions')),
                'first_seen': info['first_seen'].isoformat(),
                'last_seen': info['last_seen'].isoformat(),
                'duration_ms': int((info['last_seen'] - info['first_seen']).total_seconds() * 1000),
            })

        return _ok({'range': _range_payload(days, start, end), 'sessions': sessions})


class VisitorSessionDetailView(APIView):
    permission_classes = [IsStaffAdmin]

    def get(self, request):
        session = request.query_params.get('session', '')[:64]
        if not session:
            return Response(
                {'status': 'error', 'message': 'session parameter is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        days = _days_param(request, default=7)
        start, end = _range(days)
        pvs = _pageviews(start, end).filter(session_hash=session).order_by('created_at', 'id')
        evs = _events(start, end).filter(session_hash=session).order_by('created_at', 'id')

        if not pvs.exists() and not evs.exists():
            return Response(
                {'status': 'error', 'message': 'session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        timeline = []
        for pv in pvs:
            timeline.append({
                'type': 'pageview',
                'ts': pv.created_at.isoformat(),
                'path': pv.path,
                'title': pv.title,
                'referrer': pv.referrer,
                'duration_ms': pv.duration_ms,
            })
        for ev in evs:
            timeline.append({
                'type': 'event',
                'ts': ev.created_at.isoformat(),
                'name': ev.name,
                'category': ev.category,
                'props': ev.props,
                'value': _dec(ev.value),
                'path': ev.path,
            })
        timeline.sort(key=lambda item: item['ts'])

        first = pvs.first()
        last = pvs.last()
        first_seen = min(qs.first().created_at for qs in (pvs, evs) if qs.exists())
        last_seen = max(qs.last().created_at for qs in (pvs, evs) if qs.exists())
        device = last.device if last else evs.last().device

        return _ok({
            'range': _range_payload(days, start, end),
            'session': session,
            'device': device,
            'views': pvs.count(),
            'events': evs.count(),
            'entry_path': first.path if first else '',
            'first_seen': first_seen.isoformat(),
            'last_seen': last_seen.isoformat(),
            'duration_ms': int((last_seen - first_seen).total_seconds() * 1000),
            'timeline': timeline,
        })


class VisitorReferrersView(APIView):
    permission_classes = [IsStaffAdmin]

    def get(self, request):
        days = _days_param(request)
        start, end = _range(days)
        pvs = _pageviews(start, end).exclude(referrer='')
        evs = _events(start, end)

        top = list(
            pvs.values('referrer')
               .annotate(views=Count('id'),
                         visitors=Count('session_hash', distinct=True))
               .order_by('-views')[:50]
        )
        referrers = [r['referrer'] for r in top]
        if not referrers:
            return _ok({'range': _range_payload(days, start, end), 'referrers': []})

        # First pageview per (referrer, session) => top landing path; also
        # collect each referrer's session set for the conversion join.
        sessions_by_ref = defaultdict(set)
        entry_paths = defaultdict(Counter)
        seen_sessions = set()
        rows = (pvs.filter(referrer__in=referrers)
                   .values('referrer', 'session_hash', 'path', 'created_at')
                   .order_by('created_at')[:20000])
        for row in rows:
            key = (row['referrer'], row['session_hash'])
            sessions_by_ref[row['referrer']].add(row['session_hash'])
            if key not in seen_sessions:
                seen_sessions.add(key)
                entry_paths[row['referrer']][row['path']] += 1

        conversion_sessions = set(
            evs.filter(category=CONVERSION_CATEGORY)
               .values_list('session_hash', flat=True).distinct()
        )

        return _ok({
            'range': _range_payload(days, start, end),
            'referrers': [
                {
                    'referrer': r['referrer'],
                    'views': r['views'],
                    'visitors': r['visitors'],
                    'top_landing_path': (entry_paths[r['referrer']].most_common(1) or [[None]])[0][0],
                    'conversions': len(sessions_by_ref[r['referrer']] & conversion_sessions),
                }
                for r in top
            ],
        })
