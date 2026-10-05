"""Durable email-campaign execution (MKT-05).

A campaign row is the unit of work: the APS sweep picks up campaigns in
``sending`` status and processes recipients in bounded batches. Progress,
pause/resume and idempotency live in the database (EmailLog rows), so a
restart never re-sends and a paused campaign stops between batches.

Eligibility is re-checked per recipient inside the sweep — suppressions and
status changes that happen after a campaign is queued still take effect.
"""
import logging

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from .eligibility import CONTACTABLE_STATUSES, lead_contactable
from .models import DesignerLead, EmailCampaign, EmailLog

logger = logging.getLogger(__name__)

# Successful sends per sweep run per campaign — keeps throughput bounded and
# lets pause/cancel take effect promptly between batches.
DEFAULT_SWEEP_BATCH = 25


def _sweep_batch() -> int:
    try:
        return int(getattr(settings, 'CAMPAIGN_SWEEP_BATCH', DEFAULT_SWEEP_BATCH))
    except (TypeError, ValueError):
        return DEFAULT_SWEEP_BATCH


def audience_queryset(campaign):
    """Leads this campaign targets.

    Explicit ``target_leads`` win; otherwise ``audience_filter`` defines the
    segment. Filtered audiences are restricted to qualified-stage leads with
    an email — suppression and review state are re-checked at send time.
    """
    if campaign.target_leads.exists():
        return campaign.target_leads.all()

    qs = DesignerLead.objects.exclude(email__isnull=True).exclude(email__exact='')
    filt = campaign.audience_filter or {}
    search = (filt.get('search') or '').strip()
    statuses = filt.get('statuses') or sorted(CONTACTABLE_STATUSES)
    if statuses:
        qs = qs.filter(status__in=statuses)
    if search:
        qs = qs.filter(
            Q(brand_name__icontains=search)
            | Q(designer_name__icontains=search)
            | Q(email__icontains=search)
            | Q(instagram_handle__icontains=search)
        )
    return qs.order_by('date_discovered')


def audience_preview(campaign) -> dict:
    """Counts used by the approve/preview UI: eligible vs blocked recipients."""
    audience = audience_queryset(campaign)
    eligible, blocked = [], 0
    for lead in audience.iterator():
        ok, _ = lead_contactable(lead)
        if ok:
            eligible.append(lead)
        else:
            blocked += 1
    return {
        'audience_size': audience.count(),
        'eligible': len(eligible),
        'blocked': blocked,
        'sample': [
            {'id': l.id, 'brand_name': l.brand_name, 'email': l.email}
            for l in eligible[:10]
        ],
    }


def process_campaign_batch(campaign) -> dict:
    """Send one bounded batch for a campaign in ``sending`` status.

    Idempotent: any EmailLog row (Sent/Failed/Suppressed/Skipped) marks a
    recipient as handled, so re-runs never double-send.
    """
    campaign.refresh_from_db()
    if campaign.status != 'sending':
        return {'status': campaign.status}

    template = campaign.template
    subject = campaign.subject or (template.subject if template else '')
    body = campaign.html_body or (template.html_body if template else '')
    if not (subject and body):
        campaign.status = 'cancelled'
        campaign.save(update_fields=['status'])
        logger.error("Campaign %s has no subject/body — cancelled", campaign.id)
        return {'status': 'cancelled', 'error': 'missing content'}

    from .email_services import compile_and_send_lead_email

    processed_ids = set(
        EmailLog.objects.filter(campaign=campaign)
        .values_list('lead_id', flat=True)
    )
    batch_left = _sweep_batch()
    sent = failed = skipped = 0

    for lead in audience_queryset(campaign).exclude(id__in=processed_ids).iterator():
        if batch_left <= 0:
            break
        campaign.refresh_from_db(fields=['status', 'send_cap', 'sent_count'])
        if campaign.status != 'sending':
            break
        if campaign.send_cap and campaign.sent_count + sent >= campaign.send_cap:
            break

        batch_left -= 1
        ok, reasons = lead_contactable(lead)
        if not ok:
            EmailLog.objects.create(
                campaign=campaign, lead=lead, subject=subject,
                status='Suppressed' if any('suppress' in r for r in reasons) else 'Skipped',
                reason='; '.join(reasons)[:255],
            )
            skipped += 1
            continue

        if compile_and_send_lead_email(
            lead, template,
            custom_html_body=campaign.html_body or None,
            custom_subject=campaign.subject or None,
            campaign=campaign,
        ):
            sent += 1
            if lead.status in ('Qualified', 'Assigned'):
                DesignerLead.objects.filter(id=lead.id).update(status='Contacted')
        else:
            failed += 1

    EmailCampaign.objects.filter(id=campaign.id).update(
        sent_count=F('sent_count') + sent,
        failed_count=F('failed_count') + failed,
        skipped_count=F('skipped_count') + skipped,
    )
    campaign.refresh_from_db()

    remaining = (
        audience_queryset(campaign)
        .exclude(id__in=EmailLog.objects.filter(campaign=campaign).values('lead_id'))
        .exists()
    )
    cap_hit = bool(campaign.send_cap and campaign.sent_count >= campaign.send_cap)
    if campaign.status == 'sending' and (cap_hit or not remaining):
        campaign.status = 'completed'
        campaign.is_active = False
        campaign.save(update_fields=['status', 'is_active'])

    return {
        'status': campaign.status,
        'sent': sent, 'failed': failed, 'skipped': skipped,
        'cap_hit': cap_hit,
    }


def process_due_campaigns() -> dict:
    """Sweep entry point called by the APS scheduler.

    Starts scheduled campaigns whose time has come, then processes one batch
    for each campaign in ``sending`` status.
    """
    now = timezone.now()
    started = EmailCampaign.objects.filter(
        status='approved', scheduled_at__lte=now,
    ).update(status='sending', is_active=True)

    results = {}
    for campaign in EmailCampaign.objects.filter(status='sending').order_by('date_created'):
        try:
            results[str(campaign.id)] = process_campaign_batch(campaign)
        except Exception as e:
            logger.exception("Campaign sweep failed for %s", campaign.id)
            results[str(campaign.id)] = {'status': 'error', 'error': str(e)}
    return {'started': started, 'campaigns': results}
