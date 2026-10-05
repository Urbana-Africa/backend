"""``record_audit`` — write an immutable AuditEvent for a privileged action.

Captures actor identity, effective role, IP/device metadata and a request id
from the incoming request. When called outside a request (system/scheduler
paths), pass ``actor`` explicitly and leave ``request`` as None.

Never put secrets, tokens, or full payment details in ``before``/``after`` —
only ids, statuses and small field diffs.
"""
import logging
import uuid

from .models import AuditEvent

logger = logging.getLogger(__name__)


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or ''


def record_audit(*, request=None, actor=None, action: str,
                 entity=None, entity_type='', entity_id='',
                 before=None, after=None, reason='', approval='') -> AuditEvent:
    """Create one AuditEvent row.

    ``entity`` may be a model instance (type/id derived automatically);
    ``entity_type``/``entity_id`` can be given explicitly instead.
    """
    user = actor
    if user is None and request is not None:
        user = getattr(request, 'user', None)

    if entity is not None:
        entity_type = entity_type or entity.__class__.__name__
        entity_id = entity_id or str(getattr(entity, 'pk', '') or '')

    if request is not None:
        request_id = (
            request.META.get('HTTP_X_REQUEST_ID')
            or request.headers.get('X-Request-ID', '')
        )
        ip = _client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')[:300]
    else:
        request_id, ip, ua = '', '', ''

    try:
        return AuditEvent.objects.create(
            actor=user if getattr(user, 'is_authenticated', False) else None,
            actor_email=getattr(user, 'email', '') or '',
            actor_role=getattr(user, 'admin_role', '') or '',
            action=action[:100],
            entity_type=entity_type[:100],
            entity_id=str(entity_id)[:100],
            before=before or {},
            after=after or {},
            reason=reason[:2000],
            approval=approval[:255],
            request_id=request_id or uuid.uuid4().hex,
            ip_address=ip,
            user_agent=ua,
        )
    except Exception:
        # Auditing must never break the action it records — but failures are
        # loud in logs so a broken audit sink is detectable.
        logger.exception("Failed to write AuditEvent for action=%s", action)
        return None
