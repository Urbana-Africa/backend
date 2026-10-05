"""Contact-eligibility enforcement for lead outreach (MKT-04).

Every send path — direct single email, broadcast, and campaign sweep — must
route recipients through :func:`lead_contactable`. A suppressed, unqualified,
or frequency-capped lead cannot be reached even if a UI filter is omitted.
"""
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.core import signing
from django.utils import timezone

from .models import DesignerLead, EmailLog, LeadSuppression

# Stages that have passed qualification/review and may receive outreach.
# 'Discovered', 'Needs Review', 'Rejected' and 'Suppressed' are excluded on
# purpose: discovered leads are not campaign-eligible until qualified.
CONTACTABLE_STATUSES = {
    'Qualified', 'Assigned', 'Contacted', 'Replied', 'Meeting',
    'Applied', 'Approved', 'Activated',
    # Legacy stages that already imply human involvement
    'In Discussion', 'Signed Up',
}

UNSUBSCRIBE_SALT = 'marketing-lead-unsubscribe'

# Max successful sends to one lead in a rolling 7-day window.
DEFAULT_MAX_SENDS_PER_7D = 2


def _frequency_cap() -> int:
    try:
        return int(getattr(settings, 'LEAD_EMAIL_MAX_PER_7D', DEFAULT_MAX_SENDS_PER_7D))
    except (TypeError, ValueError):
        return DEFAULT_MAX_SENDS_PER_7D


def unsubscribe_token_for(lead) -> str:
    return signing.dumps({'lead': lead.id}, salt=UNSUBSCRIBE_SALT)


def lead_from_unsubscribe_token(token: str):
    try:
        data = signing.loads(token, salt=UNSUBSCRIBE_SALT)
    except signing.BadSignature:
        return None
    return DesignerLead.objects.filter(id=data.get('lead')).first()


def unsubscribe_url_for(lead) -> str:
    base = getattr(settings, 'API_URL', '').rstrip('/') or 'https://api.urbanaafrica.com'
    return f"{base}/marketing/leads/unsubscribe/?token={unsubscribe_token_for(lead)}"


def lead_domains(lead) -> set:
    """All domains attributable to this lead: website, socials, email."""
    domains = set()
    urls = [lead.website or ''] + list((lead.social_media_links or {}).values())
    for u in urls:
        host = urlparse(str(u)).netloc.lower()
        if host.startswith('www.'):
            host = host[4:]
        if host:
            domains.add(host)
    if lead.email and '@' in lead.email:
        domains.add(lead.email.rsplit('@', 1)[1].lower())
    return domains


def find_suppression(lead):
    """Return the matching LeadSuppression row, or None."""
    email = (lead.email or '').strip()
    if email:
        row = LeadSuppression.objects.filter(email__iexact=email).first()
        if row:
            return row
    handle = (lead.instagram_handle or '').strip()
    if handle:
        row = LeadSuppression.objects.filter(handle__iexact=handle).first()
        if row:
            return row
    brand = (lead.brand_name or '').strip()
    if brand:
        row = LeadSuppression.objects.filter(brand_name__iexact=brand).first()
        if row:
            return row
    domains = lead_domains(lead)
    if domains:
        row = LeadSuppression.objects.filter(domain__in=domains).first()
        if row:
            return row
    return None


def lead_contactable(lead, *, now=None) -> tuple:
    """Return ``(ok, reasons)``. ``reasons`` explains every block so API
    responses and EmailLog rows carry an auditable cause."""
    now = now or timezone.now()
    reasons = []

    if not (lead.email or '').strip():
        reasons.append('no email address')
    if lead.needs_review:
        reasons.append('awaiting human review')
    if lead.status not in CONTACTABLE_STATUSES:
        reasons.append(f'status "{lead.status}" is not eligible for outreach')

    suppression = find_suppression(lead)
    if suppression:
        reasons.append(f'suppressed ({suppression.reason})')

    cap = _frequency_cap()
    if cap > 0:
        # Test sends are marked in `reason` and don't count toward outreach.
        recent = EmailLog.objects.filter(
            lead=lead, status='Sent',
            sent_at__gte=now - timedelta(days=7),
        ).exclude(reason__startswith='test_send').count()
        if recent >= cap:
            reasons.append(f'frequency cap: {recent} sends in the last 7 days')

    return (not reasons, reasons)


def split_contactable(leads) -> tuple:
    """Split an iterable of leads into ``(eligible, blocked)`` where blocked
    is a list of ``(lead, reasons)`` tuples."""
    eligible, blocked = [], []
    for lead in leads:
        ok, reasons = lead_contactable(lead)
        (eligible if ok else blocked).append(lead if ok else (lead, reasons))
    return eligible, blocked


def record_suppression(*, email='', domain='', brand_name='', handle='',
                       reason='manual', created_by=None):
    """Create a LeadSuppression row if an equivalent one doesn't exist.

    The ``unique_together`` on (brand_name, domain) only dedupes identical
    pairs — email/handle-only rows can repeat, so check first.
    """
    domain = (domain or '').lower().strip()
    if domain.startswith('www.'):
        domain = domain[4:]
    email = (email or '').strip()
    brand_name = (brand_name or '').strip()
    handle = (handle or '').strip().lstrip('@').lower()

    qs = LeadSuppression.objects.filter(reason=reason)
    if email:
        existing = qs.filter(email__iexact=email).first()
        if existing:
            return existing, False
    if handle:
        existing = qs.filter(handle__iexact=handle).first()
        if existing:
            return existing, False
    if domain:
        existing = qs.filter(domain__iexact=domain).first()
        if existing:
            return existing, False
    if brand_name and not (email or domain or handle):
        existing = qs.filter(brand_name__iexact=brand_name, domain='').first()
        if existing:
            return existing, False

    from django.db import IntegrityError
    try:
        row = LeadSuppression.objects.create(
            email=email,
            domain=domain,
            handle=handle,
            brand_name=brand_name or (email or domain or handle),
            reason=reason,
            created_by=created_by,
        )
        return row, True
    except IntegrityError:
        # (brand_name, domain) unique pair already exists — return it.
        return LeadSuppression.objects.filter(
            brand_name=brand_name or (email or domain or handle),
            domain=domain,
        ).first(), False
