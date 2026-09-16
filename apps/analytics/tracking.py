"""
Anonymous visitor-analytics collector.

`POST /analytics/track` accepts a batch payload from the web apps:

    {
        "source": "web",
        "pageviews": [{"path": "/", "title": "...", "referrer": "https://x.com",
                        "duration_ms": 1234, "ts": "iso8601"}],
        "events": [{"name": "signup", "category": "conversion",
                     "props": {"method": "email"}, "value": "12.50",
                     "path": "/auth/signup", "ts": "iso8601"}]
    }

Privacy rules enforced server-side:
- session_hash is derived here (HMAC of IP | UA | date | salt, truncated to 16
  chars). Clients never send it, so it cannot be spoofed or shared. It rotates
  daily — a session never spans midnight.
- Paths have query strings stripped and are capped at 500 chars.
- Referrers are stored only when external to our own frontends, and never keep
  their query string.
- No user IDs, emails, or IP addresses are persisted.
"""

import hashlib
import hmac
import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PageView, VisitorEvent

logger = logging.getLogger(__name__)

MAX_BATCH = 100
MAX_PROPS_BYTES = 4096


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or ''


def _session_salt():
    return getattr(settings, 'ANALYTICS_SALT', None) or settings.SECRET_KEY


def _session_hash(ip, ua, day=None):
    day = day or timezone.now().date()
    digest = hmac.new(
        _session_salt().encode(),
        f'{ip}|{ua}|{day.isoformat()}'.encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest[:16]


def _device(ua):
    ua = (ua or '').lower()
    if any(tok in ua for tok in ('iphone', 'ipod', 'android', 'blackberry', 'iemobile', 'opera mini')):
        return 'mobile'
    if any(tok in ua for tok in ('ipad', 'tablet')):
        return 'tablet'
    return 'desktop'


def _internal_hosts():
    """Hosts that count as internal navigation (our own frontends + API host)."""
    hosts = set()
    for origin in getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or []:
        try:
            netloc = urlsplit(origin).netloc
        except Exception:
            continue
        if netloc:
            hosts.add(netloc)
            # subdomains of an apex domain are all "us"
            if netloc.count('.') >= 2 and not netloc.startswith('www.'):
                hosts.add(netloc.split('.', 1)[1])
    return hosts


def _clean_path(raw):
    if not isinstance(raw, str) or not raw.strip():
        return ''
    path = urlsplit(raw.strip()).path or '/'
    return path[:500]


def _clean_referrer(raw, request):
    """Keep only external http(s) referrers, and never store query strings."""
    if not isinstance(raw, str) or not raw.strip():
        return ''
    raw = raw.strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ''
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return ''  # relative / same-app path — internal navigation
    host = parsed.netloc.lower()

    # The Origin header identifies the calling app — its host is always
    # internal navigation (covers dev hosts that aren't in CORS_ALLOWED_ORIGINS)
    origin = request.META.get('HTTP_ORIGIN', '')
    try:
        origin_host = urlsplit(origin).netloc.lower()
    except ValueError:
        origin_host = ''
    if origin_host and host == origin_host:
        return ''

    internal = _internal_hosts()
    if host in internal or any(host.endswith('.' + h) for h in internal):
        return ''
    return f'{parsed.scheme}://{host}{parsed.path}'[:500]


def _clean_props(raw):
    if not isinstance(raw, dict):
        return {}
    try:
        if len(json.dumps(raw, default=str)) > MAX_PROPS_BYTES:
            return {}
    except (TypeError, ValueError):
        return {}
    return raw


def _parse_ts(raw):
    """Client-provided ISO timestamp, clamped to a sane window."""
    now = timezone.now()
    if not raw:
        return now
    try:
        ts = timezone.datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return now
    if timezone.is_naive(ts):
        ts = timezone.make_aware(ts)
    if ts < now - timedelta(hours=24) or ts > now + timedelta(minutes=5):
        return now
    return ts


def _parse_value(raw):
    if raw is None or raw == '':
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _clean_str(raw, max_len, default=''):
    if not isinstance(raw, str):
        return default
    return raw.strip()[:max_len]


class TrackView(APIView):
    permission_classes = [permissions.AllowAny]
    # Public collector: no auth machinery, so expired JWT cookies or missing
    # CSRF can never reject a batch.
    authentication_classes = []

    def post(self, request):
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return Response(
                {'status': 'error', 'message': 'invalid JSON body'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(payload, dict):
            return Response(
                {'status': 'error', 'message': 'expected object payload'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        source = _clean_str(payload.get('source'), 20, 'web') or 'web'
        pageviews = payload.get('pageviews') or []
        events = payload.get('events') or []
        if not isinstance(pageviews, list) or not isinstance(events, list):
            return Response(
                {'status': 'error', 'message': 'pageviews and events must be lists'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ip = _client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')
        session_hash = _session_hash(ip, ua)
        device = _device(ua)

        pv_rows = []
        for item in pageviews[:MAX_BATCH]:
            if not isinstance(item, dict):
                continue
            path = _clean_path(item.get('path'))
            if not path:
                continue
            try:
                duration_ms = max(0, int(item.get('duration_ms') or 0))
            except (ValueError, TypeError):
                duration_ms = 0
            pv_rows.append(PageView(
                session_hash=session_hash,
                path=path,
                title=_clean_str(item.get('title'), 300),
                referrer=_clean_referrer(item.get('referrer'), request),
                device=device,
                duration_ms=duration_ms,
                source=source,
                created_at=_parse_ts(item.get('ts')),
            ))

        ev_rows = []
        for item in events[:MAX_BATCH]:
            if not isinstance(item, dict):
                continue
            name = _clean_str(item.get('name'), 100)
            if not name:
                continue
            ev_rows.append(VisitorEvent(
                session_hash=session_hash,
                name=name,
                category=_clean_str(item.get('category'), 50) or 'custom',
                props=_clean_props(item.get('props')),
                value=_parse_value(item.get('value')),
                path=_clean_path(item.get('path')),
                device=device,
                source=source,
                created_at=_parse_ts(item.get('ts')),
            ))

        if pv_rows:
            PageView.objects.bulk_create(pv_rows)
        if ev_rows:
            VisitorEvent.objects.bulk_create(ev_rows)

        return Response({
            'status': 'success',
            'data': {
                'pageviews_recorded': len(pv_rows),
                'events_recorded': len(ev_rows),
            },
            'meta': {},
        }, status=status.HTTP_202_ACCEPTED)
