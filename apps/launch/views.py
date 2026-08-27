from django.db import transaction
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.template.loader import render_to_string
import threading
import types

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.throttling import AnonRateThrottle
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired

from .models import LaunchConfig, WaitlistSubscriber, WaitlistEvent, EmailSuppression
from .serializers import (
    LaunchConfigSerializer,
    WaitlistCreateSerializer,
    WaitlistResponseSerializer,
    WaitlistMeSerializer,
    WaitlistConfirmSerializer,
    WaitlistUnsubscribeSerializer,
    WaitlistStatsSerializer,
)


class WaitlistAnonRateThrottle(AnonRateThrottle):
    rate = '20/minute'


def _client_ip(request):
    if not request:
        return None
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _log_event(subscriber, event_type, request, metadata=None, campaign=None):
    WaitlistEvent.objects.create(
        subscriber=subscriber,
        event_type=event_type,
        campaign=campaign,
        metadata=metadata or {},
        ip=_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '') if request else ''
    )


def _send_confirm_email(subscriber, config, request=None):
    store_url = getattr(settings, 'STORE_URL', 'https://www.urbanaafrica.com')
    confirm_url = f"{store_url}/waitlist/confirm?token={subscriber.confirm_token}"
    unsubscribe_url = f"{store_url}/waitlist/unsubscribe?token={subscriber.unsubscribe_token}"
    launch_note = "We're expecting to launch soon and we'll email you the moment we go live."

    # If double opt-in is disabled, confirm immediately and send a welcome email.
    if not config.double_optin_enabled:
        if subscriber.status == 'pending':
            subscriber.confirm()
            _log_event(subscriber, 'confirmed', request, {'auto': True})
        subject = 'You are on the Urbana waitlist'
    else:
        subject = 'Confirm your Urbana early-access request'

    html_body = render_to_string('emails/waitlist_confirm.html', {
        'confirm_url': confirm_url,
        'unsubscribe_url': unsubscribe_url,
        'headline': config.headline,
        'launch_note': launch_note,
        'subject': subject,
        'double_optin_enabled': config.double_optin_enabled,
    })
    if config.double_optin_enabled:
        plain_body = f"""Hi,

Thanks for joining the Urbana waitlist. {launch_note}

Please confirm your email to stay on the list:

{confirm_url}

You can unsubscribe anytime: {unsubscribe_url}

— The Urbana Team
"""
    else:
        plain_body = f"""Hi,

Thanks for joining the Urbana waitlist. {launch_note}

You're all set. No further action is needed.

You can unsubscribe anytime: {unsubscribe_url}

— The Urbana Team
"""
    try:
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'hello@accounts.urbanaafrica.com')
        if not from_email or not from_email.endswith('@accounts.urbanaafrica.com'):
            from_email = 'hello@accounts.urbanaafrica.com'
        from_header = f"Urbana Africa <{from_email}>"

        send_mail(
            subject=subject,
            message=plain_body,
            from_email=from_header,
            recipient_list=[subscriber.email],
            html_message=html_body,
            fail_silently=False,
        )
        _log_event(subscriber, 'confirm_email_sent', request, {'status': 'sent'})
        print(f"[WAITLIST EMAIL] SENT to {subscriber.email} — subject: {subject}")
    except Exception as e:
        _log_event(subscriber, 'confirm_email_sent', request, {'status': 'failed', 'error': str(e)})
        print(f"[WAITLIST EMAIL] FAILED to {subscriber.email}: {e}")


class LaunchConfigView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]

    def get(self, request):
        config = LaunchConfig.get()
        serializer = LaunchConfigSerializer(config)
        data = serializer.data
        if config.show_signup_counter:
            confirmed = WaitlistSubscriber.objects.filter(status='confirmed').count()
            data['public_counter'] = confirmed + config.counter_offset
        else:
            data['public_counter'] = None
        return Response(data)


class WaitlistCreateView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]
    authentication_classes = ()

    def post(self, request):
        serializer = WaitlistCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        email = data['email'].lower().strip()
        config = LaunchConfig.get()

        # Honour suppression list
        if EmailSuppression.objects.filter(email=email).exists():
            return Response(
                {'error': 'This email address cannot be added to the waitlist.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate consent version
        if data['consent_version'] != config.consent_version:
            return Response(
                {'error': 'Consent text has changed. Please refresh the page.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        existing = WaitlistSubscriber.objects.filter(email=email).first()
        if existing:
            if existing.status in ('unsubscribed', 'bounced', 'complained'):
                return Response(
                    {'error': 'This email cannot be added to the waitlist.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            # Update attribution + consent if re-engaging
            existing.consent_marketing = data['consent_marketing']
            existing.consent_at = timezone.now()
            existing.consent_version = data['consent_version']
            existing.consent_ip = _client_ip(request)
            existing.consent_user_agent = request.META.get('HTTP_USER_AGENT', '')
            if data.get('anon_id'):
                existing.anon_id = data['anon_id']
            existing.save(update_fields=[
                'consent_marketing', 'consent_at', 'consent_version',
                'consent_ip', 'consent_user_agent', 'anon_id'
            ])
            _log_event(existing, 'submitted', request, {'resubmission': True})

            request_meta = types.SimpleNamespace(META={
                'HTTP_X_FORWARDED_FOR': request.META.get('HTTP_X_FORWARDED_FOR'),
                'REMOTE_ADDR': request.META.get('REMOTE_ADDR'),
                'HTTP_USER_AGENT': request.META.get('HTTP_USER_AGENT', ''),
            })

            # Resend the confirmation/welcome email on resubmission
            threading.Thread(
                target=_send_confirm_email,
                args=(existing, config, request_meta),
                daemon=True,
            ).start()

            response = WaitlistResponseSerializer(existing)
            return Response(response.data, status=status.HTTP_200_OK)

        with transaction.atomic():
            subscriber = WaitlistSubscriber.objects.create(
                email=email,
                consent_marketing=data['consent_marketing'],
                consent_version=data['consent_version'],
                consent_at=timezone.now(),
                consent_ip=_client_ip(request),
                consent_user_agent=request.META.get('HTTP_USER_AGENT', ''),
                source=data.get('source', ''),
                medium=data.get('medium', ''),
                campaign=data.get('campaign', ''),
                term=data.get('term', ''),
                content=data.get('content', ''),
                landing_path=data.get('landing_path', ''),
                referrer_url=data.get('referrer_url', ''),
                anon_id=data.get('anon_id', ''),
            )

            # Apply referrer if given and valid
            if data.get('referral_code'):
                try:
                    referrer = WaitlistSubscriber.objects.get(referral_code=data['referral_code'])
                    if referrer.id != subscriber.id:
                        subscriber.apply_referral(referrer)
                        _log_event(referrer, 'referral_shared', request, {'referred_email': email})
                except WaitlistSubscriber.DoesNotExist:
                    pass

        _log_event(subscriber, 'submitted', request, {
            'position': subscriber.position,
            'referral_code': subscriber.referral_code,
        })

        # Confirm immediately when double opt-in is disabled so the response
        # reflects the confirmed state; the actual email is sent in a thread.
        if not config.double_optin_enabled and subscriber.status == 'pending':
            subscriber.confirm()
            _log_event(subscriber, 'confirmed', request, {'auto': True})

        # Capture request metadata for the background thread so it doesn't
        # depend on the original request object after the response is sent.
        request_meta = types.SimpleNamespace(META={
            'HTTP_X_FORWARDED_FOR': request.META.get('HTTP_X_FORWARDED_FOR'),
            'REMOTE_ADDR': request.META.get('REMOTE_ADDR'),
            'HTTP_USER_AGENT': request.META.get('HTTP_USER_AGENT', ''),
        })

        # Send the confirmation/welcome email off the request/response cycle
        # so the "Joining" state on the frontend doesn't wait on SMTP.
        threading.Thread(
            target=_send_confirm_email,
            args=(subscriber, config, request_meta),
            daemon=True,
        ).start()

        response = WaitlistResponseSerializer(subscriber)
        return Response(response.data, status=status.HTTP_201_CREATED)


class WaitlistConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]

    def get(self, request):
        token = request.GET.get('token', '')
        try:
            subscriber = WaitlistSubscriber.objects.get(confirm_token=token)
        except WaitlistSubscriber.DoesNotExist:
            return Response({'error': 'Invalid or expired confirmation link.'}, status=status.HTTP_400_BAD_REQUEST)

        if subscriber.confirm_token_expires_at and subscriber.confirm_token_expires_at < timezone.now():
            return Response({'error': 'Confirmation link has expired.'}, status=status.HTTP_400_BAD_REQUEST)

        if not subscriber.confirm():
            return Response({'message': 'Email already confirmed.'}, status=status.HTTP_200_OK)

        subscriber.confirm_referral()
        _log_event(subscriber, 'confirmed', request)

        return Response({
            'message': 'Email confirmed. You are on the list.',
            'position': subscriber.position,
            'referral_code': subscriber.referral_code,
        })


class WaitlistMeView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]

    def get(self, request):
        code = request.GET.get('code', '')
        try:
            subscriber = WaitlistSubscriber.objects.get(referral_code=code)
        except WaitlistSubscriber.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = WaitlistMeSerializer(subscriber)
        return Response(serializer.data)


class WaitlistUnsubscribeView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]

    def post(self, request):
        serializer = WaitlistUnsubscribeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token = serializer.validated_data.get('token', '')
        email = serializer.validated_data.get('email', '').lower().strip()

        subscriber = None
        if token:
            subscriber = WaitlistSubscriber.objects.filter(unsubscribe_token=token).first()
        if not subscriber and email:
            subscriber = WaitlistSubscriber.objects.filter(email=email).first()

        if not subscriber:
            return Response({'message': 'If this address is on the list, it has been removed.'}, status=status.HTTP_200_OK)

        EmailSuppression.objects.get_or_create(
            email=subscriber.email,
            defaults={'reason': 'unsubscribed'}
        )
        subscriber.status = 'unsubscribed'
        subscriber.save(update_fields=['status'])
        _log_event(subscriber, 'unsubscribed', request)

        return Response({'message': 'You have been unsubscribed.'}, status=status.HTTP_200_OK)


class WaitlistStatsView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]

    def get(self, request):
        config = LaunchConfig.get()
        confirmed = WaitlistSubscriber.objects.filter(status='confirmed').count()
        total = WaitlistSubscriber.objects.count()
        data = {
            'confirmed_count': confirmed,
            'counter_offset': config.counter_offset,
            'total': total,
        }
        serializer = WaitlistStatsSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class DesignerCtaBeaconView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]

    def post(self, request):
        anon_id = request.data.get('anon_id', '')
        referrer = request.data.get('referrer_url', '')
        subscriber = None
        if anon_id:
            subscriber = WaitlistSubscriber.objects.filter(anon_id=anon_id).first()
        _log_event(subscriber, 'designer_cta_clicked', request, {
            'referrer_url': referrer,
            'anon_id': anon_id,
        })
        return Response({'status': 'ok'})


class ResendWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]

    def post(self, request):
        # Resend sends JSON with type, email, etc.
        payload = request.data
        email = (payload.get('email') or '').lower().strip()
        event_type = payload.get('type', '')

        if not email:
            return Response({'status': 'ignored'})

        if event_type in ('bounce', 'complained'):
            reason = 'bounced' if event_type == 'bounce' else 'complained'
            EmailSuppression.objects.get_or_create(
                email=email,
                defaults={'reason': reason, 'notes': str(payload)}
            )
            WaitlistSubscriber.objects.filter(email=email).update(status=reason)
            _log_event(
                WaitlistSubscriber.objects.filter(email=email).first(),
                reason, request, payload
            )

        if event_type == 'opened':
            _log_event(
                WaitlistSubscriber.objects.filter(email=email).first(),
                'email_opened', request, payload
            )

        if event_type == 'clicked':
            _log_event(
                WaitlistSubscriber.objects.filter(email=email).first(),
                'email_clicked', request, payload
            )

        return Response({'status': 'ok'})


class ReferralRedirectView(APIView):
    """
    Redirects a referral link to the store launch page, setting attribution.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]

    def get(self, request, referral_code):
        try:
            referrer = WaitlistSubscriber.objects.get(referral_code=referral_code)
        except WaitlistSubscriber.DoesNotExist:
            return Response({'error': 'Invalid referral link.'}, status=status.HTTP_404_NOT_FOUND)

        _log_event(referrer, 'referral_shared', request, {
            'code': referral_code,
            'utm_source': 'waitlist_referral',
            'ip': _client_ip(request),
        })

        store_url = getattr(settings, 'STORE_URL', 'https://urbanaafrica.com')
        redirect_url = (
            f"{store_url}?ref={referral_code}"
            f"&utm_source=waitlist_referral&utm_medium=link&utm_campaign=prelaunch"
        )
        return Response({'redirect_url': redirect_url}, status=status.HTTP_302_FOUND, headers={'Location': redirect_url})


class EmailClickView(APIView):
    """
    Verifies a signed email click token and redirects to the destination.
    Token format: signed(email|campaign_id|redirect_url).
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WaitlistAnonRateThrottle]

    def get(self, request, token):
        signer = TimestampSigner()
        try:
            raw = signer.unsign(token, max_age=3600 * 24 * 7)  # 7 days
            email, campaign_id, redirect_url = raw.split('|', 2)
        except (BadSignature, SignatureExpired, ValueError):
            return Response({'error': 'Invalid or expired link.'}, status=status.HTTP_400_BAD_REQUEST)

        subscriber = WaitlistSubscriber.objects.filter(email=email).first()
        campaign = LaunchCampaign.objects.filter(id=campaign_id).first()

        _log_event(subscriber, 'email_clicked', request, {
            'campaign_id': campaign_id,
            'redirect_url': redirect_url,
        }, campaign=campaign)
        _log_event(subscriber, 'returned_to_site', request, {
            'source': 'email_click',
            'campaign_id': campaign_id,
        }, campaign=campaign)

        return Response({'redirect_url': redirect_url}, status=status.HTTP_302_FOUND, headers={'Location': redirect_url})
