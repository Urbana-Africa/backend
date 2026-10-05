import logging
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.administrator.audit import record_audit
from apps.administrator.capabilities import HasCapability
from apps.administrator.permissions import IsMarketer
from .campaigns import audience_preview
from .eligibility import (
    CONTACTABLE_STATUSES,
    lead_contactable,
    lead_from_unsubscribe_token,
    record_suppression,
)
from .models import (
    DesignerLead,
    EmailCampaign,
    EmailLog,
    EmailTemplate,
    LeadQualificationDecision,
    LeadSuppression,
    ScrapeCall,
    ScrapeJob,
    ScrapeProviderConfig,
)
from .qualification import HARD_REJECT_TYPES, classify_existing_lead
from .serializers import (
    DesignerLeadSerializer,
    EmailCampaignSerializer,
    EmailLogSerializer,
    EmailTemplateSerializer,
    LeadQualificationDecisionSerializer,
    LeadSuppressionSerializer,
    ScrapeCallSerializer,
    ScrapeJobSerializer,
    ScrapeProviderConfigSerializer,
)

logger = logging.getLogger(__name__)

# Hard ceiling for a single broadcast/campaign audience — a UI bug must not
# be able to email the entire CRM in one go.
MAX_CAMPAIGN_SEND_CAP = 5000

# Audiences above this size require a different approver than the creator
# (maker-checker for large sends).
def _maker_checker_threshold() -> int:
    try:
        return int(getattr(settings, 'CAMPAIGN_MAKER_CHECKER_THRESHOLD', 100))
    except (TypeError, ValueError):
        return 100


def _lead_domain(lead) -> str:
    host = urlparse(lead.website or '').netloc.lower()
    return host[4:] if host.startswith('www.') else host


class DesignerLeadViewSet(viewsets.ModelViewSet):
    queryset = DesignerLead.objects.all().order_by('-date_discovered')
    serializer_class = DesignerLeadSerializer
    permission_classes = [IsMarketer, HasCapability]
    required_capability = 'marketing.view'
    action_capabilities = {
        'send_email': 'marketing.send',
        'send_broadcast': 'marketing.send',
        'qualify': 'marketing.review',
        'reject': 'marketing.review',
        'suppress': 'marketing.review',
        'assign': 'marketing.review',
        'merge': 'marketing.review',
        'requalify': 'marketing.review',
        'create': 'marketing.review',
        'update': 'marketing.review',
        'partial_update': 'marketing.review',
        'destroy': 'marketing.review',
    }

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params

        needs_review = params.get('needs_review')
        if needs_review is not None:
            value = needs_review.lower()
            if value in ('true', '1', 'yes'):
                qs = qs.filter(needs_review=True)
            elif value in ('false', '0', 'no'):
                qs = qs.filter(needs_review=False)

        status_filter = params.get('status')
        if status_filter:
            qs = qs.filter(status__in=[s.strip() for s in status_filter.split(',')])

        qtype = params.get('qualification_type')
        if qtype:
            qs = qs.filter(qualification_type__in=[s.strip() for s in qtype.split(',')])

        if params.get('pending_review', '').lower() in ('true', '1', 'yes'):
            qs = qs.filter(
                Q(needs_review=True)
                | Q(status__in=('Discovered', 'Needs Review'))
            )

        q = (params.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(brand_name__icontains=q)
                | Q(designer_name__icontains=q)
                | Q(email__icontains=q)
                | Q(instagram_handle__icontains=q)
            )
        return qs

    @action(detail=True, methods=['post'])
    def send_email(self, request, pk=None):
        lead = self.get_object()
        template_id = request.data.get('template_id')
        custom_subject = request.data.get('subject')
        custom_html_body = request.data.get('html_body')

        if not template_id and not custom_html_body:
            return Response({'error': 'Template ID or custom body is required'}, status=status.HTTP_400_BAD_REQUEST)

        template = None
        if template_id:
            template = EmailTemplate.objects.filter(id=template_id).first()
            if not template:
                return Response({'error': 'Template not found'}, status=status.HTTP_404_NOT_FOUND)

        ok, reasons = lead_contactable(lead)
        if not ok:
            return Response(
                {'error': 'Lead is not eligible for outreach', 'reasons': reasons},
                status=status.HTTP_403_FORBIDDEN,
            )

        from .email_services import compile_and_send_lead_email
        success = compile_and_send_lead_email(lead, template, custom_html_body, custom_subject)

        record_audit(
            request=request, action='lead.send_email', entity=lead,
            after={'success': success, 'subject': custom_subject or
                   getattr(template, 'subject', '')},
        )
        if success:
            if lead.status in ('Qualified', 'Assigned'):
                lead.status = 'Contacted'
                lead.save(update_fields=['status', 'date_updated'])
            return Response({'message': 'Email sent successfully'})
        return Response({'error': 'Failed to send email. Check logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def send_broadcast(self, request):
        """Queue a durable campaign instead of fire-and-forget threads.

        Eligibility is enforced per-recipient inside the sweep — the response
        reports the qualified segment size, not the raw match count.
        """
        template_id = request.data.get('template_id')
        custom_subject = request.data.get('subject')
        custom_html_body = request.data.get('html_body')
        search_query = (request.data.get('search') or '').strip()

        if not template_id and not custom_html_body:
            return Response({'error': 'Template ID or custom body is required'}, status=status.HTTP_400_BAD_REQUEST)

        template = None
        if template_id:
            template = EmailTemplate.objects.filter(id=template_id).first()
            if not template:
                return Response({'error': 'Template not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            send_cap = int(request.data.get('send_cap') or 0)
        except (TypeError, ValueError):
            return Response({'error': 'send_cap must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        send_cap = max(0, min(send_cap, MAX_CAMPAIGN_SEND_CAP))

        now = timezone.now()
        campaign = EmailCampaign.objects.create(
            name=(request.data.get('name') or '').strip()
                or f"Broadcast {now:%Y-%m-%d %H:%M}",
            template=template,
            subject=custom_subject or (template.subject if template else ''),
            html_body=custom_html_body or (template.html_body if template else ''),
            audience_filter={'search': search_query} if search_query else {},
            send_cap=send_cap,
            status='draft',
            created_by=request.user,
        )

        preview = audience_preview(campaign)
        if preview['eligible'] == 0:
            campaign.status = 'cancelled'
            campaign.is_active = False
            campaign.save(update_fields=['status', 'is_active'])
            return Response(
                {'error': 'No eligible leads in this audience (qualified leads with email, not suppressed)'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Maker-checker: large audiences need a different marketer to approve.
        if preview['eligible'] > _maker_checker_threshold():
            record_audit(
                request=request, action='campaign.broadcast', entity=campaign,
                after={'status': 'draft', 'eligible': preview['eligible'],
                       'requires_approval': True},
            )
            return Response({
                'message': (
                    f"Audience has {preview['eligible']} eligible leads — above the "
                    f"{_maker_checker_threshold()}-lead threshold. Campaign saved as draft; "
                    "another marketer must approve and start it."
                ),
                'campaign_id': campaign.id,
                'audience': preview,
                'requires_approval': True,
            }, status=status.HTTP_202_ACCEPTED)

        campaign.status = 'sending'
        campaign.is_active = True
        campaign.approved_by = request.user
        campaign.approved_at = now
        campaign.save(update_fields=['status', 'is_active', 'approved_by', 'approved_at'])
        record_audit(
            request=request, action='campaign.broadcast', entity=campaign,
            after={'status': 'sending', 'eligible': preview['eligible']},
        )

        return Response({
            'message': f"Campaign queued for {preview['eligible']} eligible lead(s)",
            'campaign_id': campaign.id,
            'audience': preview,
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def qualify(self, request, pk=None):
        """Human review pass — makes the lead outreach-eligible."""
        lead = self.get_object()
        note = (request.data.get('note') or '').strip()
        lead.status = 'Qualified'
        lead.needs_review = False
        lead.reviewed_by = request.user
        lead.qualified_at = timezone.now()
        if lead.qualification_type in ('unclassified', 'uncertain'):
            lead.qualification_type = 'direct_designer'
            lead.qualification_reason = note or 'qualified by human review'
        lead.save(update_fields=[
            'status', 'needs_review', 'reviewed_by', 'qualified_at',
            'qualification_type', 'qualification_reason', 'date_updated',
        ])
        LeadQualificationDecision.objects.create(
            lead=lead, decision='qualified', decided_by='human',
            actor=request.user, reasons=[note] if note else [],
        )
        record_audit(request=request, action='lead.qualify', entity=lead,
                     reason=note)
        return Response(DesignerLeadSerializer(lead).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        lead = self.get_object()
        reason = (request.data.get('reason') or '').strip()
        lead.status = 'Rejected'
        lead.needs_review = False
        lead.reviewed_by = request.user
        lead.save(update_fields=['status', 'needs_review', 'reviewed_by', 'date_updated'])
        LeadQualificationDecision.objects.create(
            lead=lead, decision='rejected', decided_by='human',
            actor=request.user, reasons=[reason] if reason else [],
        )
        record_audit(request=request, action='lead.reject', entity=lead,
                     reason=reason,
                     after={'suppress': bool(request.data.get('suppress'))})
        if request.data.get('suppress'):
            record_suppression(
                brand_name=lead.brand_name,
                email=lead.email or '',
                domain=_lead_domain(lead),
                handle=lead.instagram_handle or '',
                reason='rejected',
                created_by=request.user,
            )
        return Response(DesignerLeadSerializer(lead).data)

    @action(detail=True, methods=['post'])
    def suppress(self, request, pk=None):
        lead = self.get_object()
        reason = (request.data.get('reason') or 'manual').strip()
        record_suppression(
            brand_name=lead.brand_name,
            email=lead.email or '',
            domain=_lead_domain(lead),
            handle=lead.instagram_handle or '',
            reason=reason if reason in dict(LeadSuppression.REASON_CHOICES) else 'manual',
            created_by=request.user,
        )
        lead.status = 'Suppressed'
        lead.needs_review = False
        lead.reviewed_by = request.user
        lead.save(update_fields=['status', 'needs_review', 'reviewed_by', 'date_updated'])
        LeadQualificationDecision.objects.create(
            lead=lead, decision='suppressed', decided_by='human',
            actor=request.user, reasons=[reason],
        )
        record_audit(request=request, action='lead.suppress', entity=lead,
                     reason=reason)
        return Response(DesignerLeadSerializer(lead).data)

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        lead = self.get_object()
        user_id = request.data.get('user_id')
        from django.contrib.auth import get_user_model
        assignee = get_user_model().objects.filter(id=user_id).first()
        if user_id and not assignee:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
        lead.assigned_to = assignee
        if lead.status in ('Discovered', 'Needs Review', 'Qualified'):
            lead.status = 'Assigned'
        lead.save(update_fields=['assigned_to', 'status', 'date_updated'])
        record_audit(request=request, action='lead.assign', entity=lead,
                     after={'assigned_to': getattr(assignee, 'email', None)})
        return Response(DesignerLeadSerializer(lead).data)

    @action(detail=True, methods=['post'])
    def merge(self, request, pk=None):
        """Consolidate this duplicate INTO `target_id` without losing history.

        The target absorbs missing contact fields, social links, tags, the
        source's email logs, and any future dedupe checks; this lead is kept
        (marked Rejected + merged_into) so its provenance and decision trail
        survive.
        """
        source = self.get_object()
        target = DesignerLead.objects.filter(
            id=request.data.get('target_id')
        ).first()
        if not target:
            return Response({'error': 'target_id lead not found'},
                            status=status.HTTP_404_NOT_FOUND)
        if target.id == source.id:
            return Response({'error': 'Cannot merge a lead into itself'},
                            status=status.HTTP_400_BAD_REQUEST)
        if source.merged_into_id:
            return Response(
                {'error': 'This lead is already merged — merge into the canonical lead instead'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target.status in ('Suppressed', 'Rejected') or target.merged_into_id:
            return Response(
                {'error': f'Target lead is {target.status.lower()} — choose a live lead'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            for field in ('email', 'phone_number', 'website',
                          'instagram_handle', 'country_code', 'designer_name'):
                if not getattr(target, field) and getattr(source, field):
                    setattr(target, field, getattr(source, field))
            target.social_media_links = {
                **(source.social_media_links or {}),
                **(target.social_media_links or {}),
            }
            target.category_tags = sorted(set(
                (target.category_tags or []) + (source.category_tags or [])
            ))
            target.followers_count = max(
                target.followers_count or 0, source.followers_count or 0
            )
            target.save()
            EmailLog.objects.filter(lead=source).update(lead=target)

            source.merged_into = target
            source.status = 'Rejected'
            source.needs_review = False
            source.reviewed_by = request.user
            source.save(update_fields=[
                'merged_into', 'status', 'needs_review',
                'reviewed_by', 'date_updated',
            ])
            LeadQualificationDecision.objects.create(
                lead=source, decision='merged', decided_by='human',
                actor=request.user,
                reasons=[f'merged into {target.brand_name} ({target.id})'],
            )
            record_audit(
                request=request, action='lead.merge', entity=source,
                after={'merged_into': target.id},
            )
        return Response(DesignerLeadSerializer(target).data)

    @action(detail=False, methods=['post'])
    def requalify(self, request):
        """Backfill classification for historical (unclassified) leads.

        Hard-reject types are marked Rejected; ambiguous leads are queued for
        human review; likely direct designers stay at their current stage
        (qualification still requires a human 'qualify' before outreach).
        """
        qs = DesignerLead.objects.exclude(
            status__in=('Suppressed', 'Rejected')
        ).select_related('enrichment')
        if not request.data.get('all'):
            qs = qs.filter(qualification_type='unclassified')

        counts = {'hard_reject': 0, 'uncertain': 0, 'direct_designer': 0, 'unchanged': 0}
        for lead in qs.iterator():
            qtype, reasons = classify_existing_lead(lead)
            fields = ['qualification_type', 'qualification_reason', 'date_updated']
            lead.qualification_type = qtype
            lead.qualification_reason = '; '.join(reasons)[:1000]
            if qtype in HARD_REJECT_TYPES:
                lead.status = 'Rejected'
                lead.needs_review = False
                fields += ['status', 'needs_review']
                counts['hard_reject'] += 1
            elif qtype == 'uncertain':
                lead.needs_review = True
                if lead.status == 'Discovered':
                    lead.status = 'Needs Review'
                    fields.append('status')
                fields.append('needs_review')
                counts['uncertain'] += 1
            else:
                counts['direct_designer'] += 1
            lead.save(update_fields=fields)
            LeadQualificationDecision.objects.create(
                lead=lead, decision=qtype, decided_by='rules', reasons=reasons,
            )
        record_audit(request=request, action='lead.requalify',
                     entity_type='DesignerLead', after=counts)
        return Response({'processed': sum(counts.values()), **counts})

    # Public one-click unsubscribe — linked from every marketing email.
    @action(detail=False, methods=['get'], permission_classes=[AllowAny],
            url_path='unsubscribe', authentication_classes=[])
    def unsubscribe(self, request):
        token = request.query_params.get('token', '')
        lead = lead_from_unsubscribe_token(token)
        if not lead:
            return HttpResponse(
                '<p>This unsubscribe link is invalid.</p>',
                status=status.HTTP_400_BAD_REQUEST,
                content_type='text/html',
            )
        record_suppression(
            brand_name=lead.brand_name,
            email=lead.email or '',
            domain=_lead_domain(lead),
            handle=lead.instagram_handle or '',
            reason='unsubscribe',
        )
        DesignerLead.objects.filter(id=lead.id).update(
            status='Suppressed', needs_review=False,
        )
        LeadQualificationDecision.objects.create(
            lead=lead, decision='suppressed', decided_by='human',
            reasons=['unsubscribe link'],
        )
        record_audit(request=request, action='lead.unsubscribe', entity=lead,
                     reason='unsubscribe link')
        return HttpResponse(
            '<p>You have been unsubscribed from Urbana Africa outreach.</p>',
            content_type='text/html',
        )


class LeadSuppressionViewSet(viewsets.ModelViewSet):
    queryset = LeadSuppression.objects.all().order_by('-created_at')
    serializer_class = LeadSuppressionSerializer
    permission_classes = [IsMarketer, HasCapability]
    required_capability = 'marketing.view'
    action_capabilities = {
        'create': 'marketing.review',
        'update': 'marketing.review',
        'partial_update': 'marketing.review',
        'destroy': 'marketing.review',
    }

    def get_queryset(self):
        qs = super().get_queryset()
        q = (self.request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(email__icontains=q) | Q(domain__icontains=q)
                | Q(brand_name__icontains=q) | Q(handle__icontains=q)
            )
        reason = self.request.query_params.get('reason')
        if reason:
            qs = qs.filter(reason=reason)
        return qs

    def perform_create(self, serializer):
        suppression = serializer.save(created_by=self.request.user)
        record_audit(
            request=self.request, action='suppression.create',
            entity=suppression,
            after={'reason': suppression.reason,
                   'email': bool(suppression.email),
                   'domain': bool(suppression.domain),
                   'handle': bool(suppression.handle)},
        )


class LeadQualificationDecisionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = LeadQualificationDecision.objects.all()
    serializer_class = LeadQualificationDecisionSerializer
    permission_classes = [IsMarketer, HasCapability]
    required_capability = 'marketing.view'

    def get_queryset(self):
        qs = super().get_queryset()
        lead_id = self.request.query_params.get('lead')
        if lead_id:
            qs = qs.filter(lead_id=lead_id)
        return qs


class EmailTemplateViewSet(viewsets.ModelViewSet):
    queryset = EmailTemplate.objects.all().order_by('-date_created')
    serializer_class = EmailTemplateSerializer
    permission_classes = [IsMarketer, HasCapability]
    required_capability = 'marketing.view'
    action_capabilities = {
        'create': 'marketing.review',
        'update': 'marketing.review',
        'partial_update': 'marketing.review',
        'destroy': 'marketing.review',
    }


class EmailCampaignViewSet(viewsets.ModelViewSet):
    queryset = EmailCampaign.objects.all().order_by('-date_created')
    serializer_class = EmailCampaignSerializer
    permission_classes = [IsMarketer, HasCapability]
    required_capability = 'marketing.view'
    action_capabilities = {
        'create': 'marketing.send',
        'approve': 'marketing.approve',
        'send': 'marketing.send',
        'test_send': 'marketing.send',
        'pause': 'marketing.send',
        'resume': 'marketing.send',
        'cancel': 'marketing.send',
        'update': 'marketing.approve',
        'partial_update': 'marketing.approve',
        'destroy': 'marketing.approve',
    }

    def perform_create(self, serializer):
        campaign = serializer.save(created_by=self.request.user)
        if not campaign.subject and campaign.template:
            campaign.subject = campaign.template.subject
        if not campaign.html_body and campaign.template:
            campaign.html_body = campaign.template.html_body
        campaign.save(update_fields=['subject', 'html_body'])
        record_audit(request=self.request, action='campaign.create',
                     entity=campaign)

    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):
        return Response(audience_preview(self.get_object()))

    @action(detail=True, methods=['post'])
    def test_send(self, request, pk=None):
        """Send one rendered test email — bypasses the recipient pipeline but
        never bypasses suppression on the destination address."""
        campaign = self.get_object()
        template = campaign.template
        subject = campaign.subject or (template.subject if template else '')
        body = campaign.html_body or (template.html_body if template else '')
        if not (subject and body):
            return Response({'error': 'Campaign has no subject/body to test'},
                            status=status.HTTP_400_BAD_REQUEST)

        lead_id = request.data.get('lead_id')
        lead = DesignerLead.objects.filter(id=lead_id).first() if lead_id else None
        if lead_id and not lead:
            return Response({'error': 'Lead not found'}, status=status.HTTP_404_NOT_FOUND)

        dest = (request.data.get('email') or '').strip() or (lead.email if lead else '')
        if not dest:
            return Response({'error': 'Provide lead_id or a destination email'},
                            status=status.HTTP_400_BAD_REQUEST)
        if LeadSuppression.objects.filter(email__iexact=dest).exists():
            return Response({'error': 'Destination address is suppressed'},
                            status=status.HTTP_403_FORBIDDEN)

        from .email_services import render_lead_email
        from apps.utils.email_sender import resend_sendmail, wrap_email_html

        html = render_lead_email(lead, subject, body)
        if not resend_sendmail(
            subject=f"[TEST] {subject}",
            recipient_list=[dest],
            message=wrap_email_html(html, subject),
            from_name="Urbana Africa Marketing",
        ):
            return Response({'error': 'Send failed — check mail provider logs'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if lead:
            EmailLog.objects.create(
                campaign=campaign, lead=lead, subject=subject,
                status='Sent', reason=f'test_send->{dest}'[:255],
            )
        record_audit(request=request, action='campaign.test_send',
                     entity=campaign, after={'destination': dest})
        return Response({'message': f'Test email sent to {dest}'})

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != 'draft':
            return Response(
                {'error': f'Only draft campaigns can be approved (status: {campaign.status})'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        preview = audience_preview(campaign)
        if preview['eligible'] == 0:
            return Response(
                {'error': 'No eligible recipients — cannot approve', 'audience': preview},
                status=status.HTTP_400_BAD_REQUEST,
            )
        threshold = _maker_checker_threshold()
        if (campaign.created_by_id and campaign.created_by_id == request.user.id
                and preview['eligible'] > threshold):
            return Response(
                {'error': (
                    f'Audiences over {threshold} eligible leads must be approved by a '
                    'different marketer than the creator (maker-checker).'
                )},
                status=status.HTTP_403_FORBIDDEN,
            )
        campaign.status = 'approved'
        campaign.approved_by = request.user
        campaign.approved_at = timezone.now()
        campaign.save(update_fields=['status', 'approved_by', 'approved_at'])
        record_audit(request=request, action='campaign.approve',
                     entity=campaign, approval=request.user.email,
                     after={'eligible': preview['eligible']})
        return Response(EmailCampaignSerializer(campaign).data)

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != 'approved':
            return Response(
                {'error': f'Campaign must be approved before sending (status: {campaign.status})'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if campaign.scheduled_at and campaign.scheduled_at > timezone.now():
            return Response({
                'message': f'Campaign stays approved and will start at {campaign.scheduled_at:%Y-%m-%d %H:%M} UTC',
            })
        campaign.status = 'sending'
        campaign.is_active = True
        campaign.save(update_fields=['status', 'is_active'])
        record_audit(request=request, action='campaign.send', entity=campaign)
        return Response({'message': 'Campaign is now sending', 'campaign': EmailCampaignSerializer(campaign).data})

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != 'sending':
            return Response({'error': 'Only sending campaigns can be paused'},
                            status=status.HTTP_400_BAD_REQUEST)
        campaign.status = 'paused'
        campaign.save(update_fields=['status'])
        record_audit(request=request, action='campaign.pause', entity=campaign)
        return Response(EmailCampaignSerializer(campaign).data)

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != 'paused':
            return Response({'error': 'Only paused campaigns can resume'},
                            status=status.HTTP_400_BAD_REQUEST)
        campaign.status = 'sending'
        campaign.save(update_fields=['status'])
        record_audit(request=request, action='campaign.resume', entity=campaign)
        return Response(EmailCampaignSerializer(campaign).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status in ('completed', 'cancelled'):
            return Response({'error': f'Campaign already {campaign.status}'},
                            status=status.HTTP_400_BAD_REQUEST)
        campaign.status = 'cancelled'
        campaign.is_active = False
        campaign.save(update_fields=['status', 'is_active'])
        record_audit(request=request, action='campaign.cancel', entity=campaign)
        return Response(EmailCampaignSerializer(campaign).data)


class EmailLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EmailLog.objects.all().order_by('-sent_at')
    serializer_class = EmailLogSerializer
    permission_classes = [IsMarketer, HasCapability]
    required_capability = 'marketing.view'


@api_view(['POST'])
@permission_classes([IsMarketer])
def scrape_leads_placeholder(request):
    """
    Endpoint for triggering third-party API scraping.
    Creates a ScrapeJob and dispatches the provider engine.
    """
    from .services import run_scraping_job

    query = request.data.get('query', '')
    if not query:
        return Response({'error': 'Search query is required'}, status=status.HTTP_400_BAD_REQUEST)

    provider_name = request.data.get('provider', '')
    try:
        max_results = int(request.data.get('max_results', 5))
    except (TypeError, ValueError):
        return Response({'error': 'max_results must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
    max_results = max(1, min(max_results, 50))

    job = run_scraping_job(
        query=query.strip()[:500],
        max_results=max_results,
        created_by=request.user,
        provider_name=provider_name,
    )

    return Response({
        'message': f'Scraping job started for query: {query}',
        'job_id': job.id,
        'status': job.status,
    }, status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
@permission_classes([IsMarketer])
def funnel_stats(request):
    """High-level pipeline + qualification metrics for the CRM dashboard."""
    status_breakdown = list(
        DesignerLead.objects.values('status').annotate(count=Count('status'))
    )
    qualification_breakdown = list(
        DesignerLead.objects.values('qualification_type')
        .annotate(count=Count('qualification_type'))
    )
    pending_review = DesignerLead.objects.filter(
        Q(needs_review=True) | Q(status__in=('Discovered', 'Needs Review'))
    ).count()
    qualified_plus = DesignerLead.objects.filter(
        status__in=list(CONTACTABLE_STATUSES)
    ).count()

    # Qualified-lead yield from the 20 most recent completed scrape jobs.
    recent_jobs = ScrapeJob.objects.filter(status='completed').order_by('-created_at')[:20]
    urls_found = sum((j.result_summary or {}).get('urls_found', 0) for j in recent_jobs)
    leads_created = sum((j.result_summary or {}).get('leads_created', 0) for j in recent_jobs)

    return Response({
        'total_leads': DesignerLead.objects.count(),
        'status_breakdown': status_breakdown,
        'qualification_breakdown': qualification_breakdown,
        'pending_review': pending_review,
        'qualified_leads': qualified_plus,
        'recent_scrape': {
            'jobs': len(recent_jobs),
            'urls_found': urls_found,
            'leads_created': leads_created,
            'yield_pct': round(100 * leads_created / urls_found, 1) if urls_found else None,
        },
    })


class ScrapeProviderConfigViewSet(viewsets.ModelViewSet):
    queryset = ScrapeProviderConfig.objects.all().order_by('priority', 'name')
    serializer_class = ScrapeProviderConfigSerializer
    permission_classes = [IsMarketer, HasCapability]
    # Provider credentials/budgets — restricted capability on top of view.
    required_capability = 'marketing.configure'


class ScrapeJobViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ScrapeJob.objects.all().order_by('-created_at')
    serializer_class = ScrapeJobSerializer
    permission_classes = [IsMarketer, HasCapability]
    required_capability = 'marketing.view'


class ScrapeCallViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ScrapeCall.objects.all().order_by('-created_at')
    serializer_class = ScrapeCallSerializer
    permission_classes = [IsMarketer, HasCapability]
    required_capability = 'marketing.view'
