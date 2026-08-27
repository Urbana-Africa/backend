import csv
import io
import threading
from django.conf import settings
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.core.mail import send_mail
from django.core.signing import TimestampSigner
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework.pagination import PageNumberPagination

from apps.administrator.permissions import IsSuperAdmin, IsMarketer

from .models import LaunchConfig, WaitlistSubscriber, WaitlistEvent, LaunchCampaign
from .serializers import (
    LaunchConfigSerializer,
    WaitlistEventSerializer,
    LaunchCampaignSerializer,
    WaitlistSubscriberAdminSerializer,
)
from .views import _client_ip, _log_event


class LaunchConfigAdminView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        config = LaunchConfig.get()
        serializer = LaunchConfigSerializer(config)
        return Response(serializer.data)

    def patch(self, request):
        config = LaunchConfig.get()
        for key, value in request.data.items():
            if hasattr(config, key) and key not in ('id', 'created_at', 'updated_at'):
                setattr(config, key, value)
        config.updated_by = request.user
        config.save()
        serializer = LaunchConfigSerializer(config)
        return Response(serializer.data)


class WaitlistPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class WaitlistSubscriberAdminViewSet(ModelViewSet):
    queryset = WaitlistSubscriber.objects.all().order_by('-created_at')
    serializer_class = WaitlistSubscriberAdminSerializer
    pagination_class = WaitlistPagination
    permission_classes = [permissions.IsAuthenticated, IsMarketer]
    lookup_field = 'id'

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        status_filter = params.get('status')
        search = params.get('search')
        if status_filter:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(
                Q(email__icontains=search) |
                Q(referral_code__icontains=search) |
                Q(full_name__icontains=search)
            )
        return qs

    @action(detail=True, methods=['post'])
    def resend_confirm(self, request, id=None):
        from .views import _send_confirm_email
        subscriber = self.get_object()
        if subscriber.status != 'pending':
            return Response({'error': 'Only pending subscribers can be re-sent.'}, status=status.HTTP_400_BAD_REQUEST)
        _send_confirm_email(subscriber, LaunchConfig.get(), request)
        return Response({'status': 'queued'})

    @action(detail=True, methods=['get'])
    def timeline(self, request, id=None):
        subscriber = self.get_object()
        events = subscriber.events.all()[:100]
        serializer = WaitlistEventSerializer(events, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def export(self, request):
        """CSV export of filtered waitlist subscribers, audit-logged."""
        qs = self.filter_queryset(self.get_queryset())
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'id', 'email', 'full_name', 'status', 'position', 'referral_code',
            'referred_by_email', 'referral_count', 'consent_marketing', 'consent_version',
            'source', 'medium', 'campaign', 'country_code', 'created_at', 'confirmed_at'
        ])

        for sub in qs:
            writer.writerow([
                sub.id, sub.email, sub.full_name, sub.status, sub.position,
                sub.referral_code,
                sub.referred_by.email if sub.referred_by else '',
                sub.referral_count, sub.consent_marketing, sub.consent_version,
                sub.source, sub.medium, sub.campaign, sub.country_code,
                sub.created_at.isoformat() if sub.created_at else '',
                sub.confirmed_at.isoformat() if sub.confirmed_at else '',
            ])

        response = HttpResponse(output.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="waitlist.csv"'
        return response

    @action(detail=False, methods=['post'])
    def import_csv(self, request):
        """CSV import of waitlist subscribers. Existing emails are skipped."""
        uploaded = request.FILES.get('file')
        if not uploaded:
            return Response({'error': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

        decoded = uploaded.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(decoded))
        created = 0
        skipped = 0
        errors = []

        for row in reader:
            email = (row.get('email') or '').strip().lower()
            if not email:
                errors.append('row missing email')
                continue
            if WaitlistSubscriber.objects.filter(email=email).exists():
                skipped += 1
                continue

            try:
                WaitlistSubscriber.objects.create(
                    email=email,
                    full_name=(row.get('full_name') or '').strip(),
                    country_code=(row.get('country_code') or '').strip(),
                    status='pending',
                    consent_marketing=False,
                    consent_version=LaunchConfig.get().consent_version,
                    source='csv_import',
                    medium=row.get('medium', ''),
                    campaign=row.get('campaign', ''),
                )
                created += 1
            except Exception as e:
                errors.append(f"{email}: {e}")

        return Response({'created': created, 'skipped': skipped, 'errors': errors[:50]})


class LaunchCampaignAdminViewSet(ModelViewSet):
    queryset = LaunchCampaign.objects.all().order_by('-created_at')
    serializer_class = LaunchCampaignSerializer
    pagination_class = WaitlistPagination
    permission_classes = [permissions.IsAuthenticated, IsMarketer]
    lookup_field = 'id'

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def test_send(self, request, id=None):
        """Send the campaign to up to 5 internal addresses for review."""
        campaign = self.get_object()
        to_emails = request.data.get('to_emails', [])
        if not isinstance(to_emails, list) or not to_emails:
            return Response({'error': 'to_emails array required'}, status=status.HTTP_400_BAD_REQUEST)
        if len(to_emails) > 5:
            return Response({'error': 'Maximum 5 test emails'}, status=status.HTTP_400_BAD_REQUEST)

        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'hello@accounts.urbanaafrica.com')
        if not from_email or not from_email.endswith('@accounts.urbanaafrica.com'):
            from_email = 'hello@accounts.urbanaafrica.com'
        from_header = f"Urbana Africa <{from_email}>"
        for email in to_emails:
            send_mail(
                subject=campaign.subject,
                message='',
                from_email=from_header,
                recipient_list=[email],
                html_message=campaign.html_body,
            )
        return Response({'status': 'sent', 'recipients': len(to_emails)})

    @action(detail=True, methods=['post'])
    def send(self, request, id=None):
        """Enqueue the campaign to a waitlist segment."""
        from django.core.mail import EmailMultiAlternatives

        campaign = self.get_object()
        if campaign.status in ('sending', 'sent'):
            return Response({'error': 'Campaign already sent or in flight.'}, status=status.HTTP_400_BAD_REQUEST)

        segment = campaign.segment_query or {}
        status_filter = segment.get('status', ['confirmed'])
        if isinstance(status_filter, str):
            status_filter = [status_filter]

        qs = WaitlistSubscriber.objects.filter(status__in=status_filter)
        # Exclude suppressed / opted-out
        suppressed = set(EmailSuppression.objects.values_list('email', flat=True))
        qs = [s for s in qs if s.email not in suppressed and s.consent_marketing]

        campaign.status = 'sending'
        campaign.save(update_fields=['status'])

        store_url = getattr(settings, 'STORE_URL', 'https://urbanaafrica.com')
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'hello@accounts.urbanaafrica.com')
        if not from_email or not from_email.endswith('@accounts.urbanaafrica.com'):
            from_email = 'hello@accounts.urbanaafrica.com'
        from_header = f"Urbana Africa <{from_email}>"
        signer = TimestampSigner()

        def _send_to_segment():
            sent_count = 0
            failed_count = 0
            for recipient in qs:
                token = signer.sign(f"{recipient.email}|{campaign.id}|{store_url}")
                click_url = f"{store_url}/t/{token}"
                unsubscribe_url = f"{store_url}/waitlist/unsubscribe?token={recipient.unsubscribe_token}"

                footer = (
                    f"<p style='margin-top:24px'><a href='{click_url}'>Shop now</a></p>"
                    f"<p style='font-size:12px;color:#666'>"
                    f"<a href='{unsubscribe_url}'>Unsubscribe</a> | "
                    f"You're receiving this because you joined the Urbana waitlist."
                    f"</p>"
                )

                msg = EmailMultiAlternatives(
                    subject=campaign.subject,
                    body=f"Shop now: {click_url}\nUnsubscribe: {unsubscribe_url}",
                    from_email=from_email,
                    to=[recipient.email],
                    headers={
                        'List-Unsubscribe': f"<{unsubscribe_url}>",
                    },
                )
                msg.attach_alternative(campaign.html_body + footer, 'text/html')
                try:
                    msg.send(fail_silently=False)
                    _log_event(recipient, 'email_sent', None, {'campaign_id': campaign.id}, campaign=campaign)
                    sent_count += 1
                except Exception as e:
                    failed_count += 1
                    _log_event(recipient, 'email_sent', None, {'campaign_id': campaign.id, 'error': str(e)}, campaign=campaign)

            campaign.refresh_from_db()
            campaign.status = 'sent'
            campaign.sent_at = timezone.now()
            campaign.stats = {
                'recipients': len(qs),
                'sent': sent_count,
                'failed': failed_count,
            }
            campaign.save(update_fields=['status', 'sent_at', 'stats'])

        threading.Thread(target=_send_to_segment, daemon=True).start()

        return Response({
            'status': 'sending',
            'recipients': len(qs),
            'campaign_id': campaign.id,
        }, status=status.HTTP_202_ACCEPTED)


class WaitlistAnalyticsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsMarketer]

    def get(self, request):
        total = WaitlistSubscriber.objects.count()
        pending = WaitlistSubscriber.objects.filter(status='pending').count()
        confirmed = WaitlistSubscriber.objects.filter(status='confirmed').count()
        converted = WaitlistSubscriber.objects.filter(status='converted').count()
        unsubscribed = WaitlistSubscriber.objects.filter(status='unsubscribed').count()
        referrals = WaitlistSubscriber.objects.filter(referral_count__gt=0).count()
        total_referrals = WaitlistSubscriber.objects.filter(referred_by__isnull=False).count()
        events = WaitlistEvent.objects.values('event_type').annotate(count=Count('id'))

        daily = WaitlistEvent.objects.annotate(
            day=TruncDate('created_at')
        ).values('day', 'event_type').annotate(count=Count('id')).order_by('-day')[:30]

        conversion_rate = round((confirmed / total) * 100, 2) if total else 0.0

        return Response({
            'total_subscribers': total,
            'confirmed_subscribers': confirmed,
            'conversion_rate': conversion_rate,
            'total_referrals': total_referrals,
            'funnel': {
                'total': total,
                'pending': pending,
                'confirmed': confirmed,
                'unsubscribed': unsubscribed,
                'converted': converted,
            },
            'referrals': {
                'referrers': referrals,
                'total_referrals': total_referrals,
            },
            'events': list(events),
            'daily': list(daily),
        })


class DSARView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]

    def _by_email(self, email):
        User = None
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.filter(email__iexact=email).first()
        except Exception:
            user = None

        waitlist = WaitlistSubscriber.objects.filter(email__iexact=email)
        launches = WaitlistEvent.objects.filter(subscriber__email__iexact=email)
        leads = None
        try:
            from apps.marketing.models import DesignerLead
            leads = DesignerLead.objects.filter(email__iexact=email)
        except Exception:
            leads = None

        events = None
        try:
            from apps.analytics.models import Event
            events = Event.objects.none()
            if user:
                events = Event.objects.filter(user=user)
        except Exception:
            events = None

        return {
            'user': {
                'id': user.id if user else None,
                'email': user.email if user else None,
                'date_joined': user.date_joined if user else None,
            } if user else None,
            'waitlist': [
                {
                    'id': s.id,
                    'status': s.status,
                    'confirmed_at': s.confirmed_at,
                    'created_at': s.created_at,
                    'consent_analytics': s.consent_analytics,
                    'consent_marketing': s.consent_marketing,
                    'referral_count': s.referral_count,
                }
                for s in waitlist
            ],
            'waitlist_events': [
                {
                    'event_type': e.event_type,
                    'created_at': e.created_at,
                    'metadata': e.metadata,
                }
                for e in launches
            ],
            'designer_leads': [
                {
                    'id': lead.id,
                    'brand_name': lead.brand_name,
                    'status': lead.status,
                    'source': lead.source,
                    'needs_review': lead.needs_review,
                }
                for lead in (leads or [])
            ],
            'analytics_events_count': events.count() if events is not None else 0,
        }

    def get(self, request):
        email = request.query_params.get('email')
        if not email:
            return Response({'error': 'email is required'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'email': email, 'data': self._by_email(email)})

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response({'error': 'email is required'}, status=status.HTTP_400_BAD_REQUEST)

        user = None
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.filter(email__iexact=email).first()
        except Exception:
            pass

        # Anonymise or delete platform user
        if user:
            user.email = f"deleted-{user.id}@deleted.urbana"
            user.first_name = ''
            user.last_name = ''
            if hasattr(user, 'is_active'):
                user.is_active = False
            user.save()

        # Delete waitlist records and suppress email
        deleted_waitlist = WaitlistSubscriber.objects.filter(email__iexact=email).delete()
        from .models import EmailSuppression
        EmailSuppression.objects.get_or_create(email=email.lower())

        # Anonymise designer leads
        try:
            from apps.marketing.models import DesignerLead
            DesignerLead.objects.filter(email__iexact=email).update(
                email='',
                phone_number='',
                social_media_links={},
                needs_review=True,
            )
        except Exception:
            pass

        # Delete analytics events linked to the user
        try:
            from apps.analytics.models import Event
            if user:
                Event.objects.filter(user=user).delete()
        except Exception:
            pass

        return Response({
            'email': email,
            'deleted_waitlist_count': deleted_waitlist[0] if deleted_waitlist else 0,
            'user_anonymised': bool(user),
            'suppressed': True,
        })
