"""Resend delivery webhooks (MKT-04/MKT-06).

Bounces and complaints suppress the address globally — every send path
(direct, broadcast, campaign) checks the suppression list, so a bad address
can never be re-mailed. Signature verification follows Resend's svix scheme;
when RESEND_WEBHOOK_SECRET is not configured the endpoint refuses events
rather than trusting unsigned payloads.
"""
import base64
import hashlib
import hmac
import json
import logging
import time

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .eligibility import record_suppression
from .models import DesignerLead, EmailLog, LeadSuppression

logger = logging.getLogger(__name__)

TOLERANCE_S = 300  # reject events older than 5 minutes

EVENT_TO_REASON = {
    'email.bounced': 'bounce',
    'email.complained': 'complaint',
}


def _verify_svix(request, secret: str) -> bool:
    """Verify Resend's svix signature headers against the raw body."""
    msg_id = request.headers.get('svix-id')
    timestamp = request.headers.get('svix-timestamp')
    signature_header = request.headers.get('svix-signature', '')
    if not (msg_id and timestamp and signature_header):
        return False
    try:
        if abs(time.time() - int(timestamp)) > TOLERANCE_S:
            return False
    except (TypeError, ValueError):
        return False

    key = secret[len('whsec_'):] if secret.startswith('whsec_') else secret
    try:
        key_bytes = base64.b64decode(key)
    except Exception:
        return False

    signed = f"{msg_id}.{timestamp}.{request.body.decode('utf-8')}"
    expected = base64.b64encode(
        hmac.new(key_bytes, signed.encode('utf-8'), hashlib.sha256).digest()
    ).decode('utf-8')
    candidates = [
        part.split(',', 1)[1]
        for part in signature_header.split(' ')
        if part.startswith('v1,')
    ]
    return any(hmac.compare_digest(expected, sig) for sig in candidates)


@method_decorator(csrf_exempt, name='dispatch')
class ResendWebhookView(APIView):
    """POST /marketing/webhooks/resend/ — provider delivery events."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        secret = getattr(settings, 'RESEND_WEBHOOK_SECRET', '')
        if not secret:
            logger.error("Resend webhook received but RESEND_WEBHOOK_SECRET is not configured")
            return JsonResponse({'error': 'webhook not configured'}, status=503)
        if not _verify_svix(request, secret):
            logger.warning("Resend webhook signature verification failed")
            return JsonResponse({'error': 'invalid signature'}, status=401)

        try:
            payload = json.loads(request.body.decode('utf-8'))
        except ValueError:
            return JsonResponse({'error': 'invalid json'}, status=400)

        event_type = payload.get('type', '')
        data = payload.get('data') or {}
        recipients = data.get('to') or []
        if isinstance(recipients, str):
            recipients = [recipients]
        single = data.get('email')
        if single and single not in recipients:
            recipients.append(single)

        reason = EVENT_TO_REASON.get(event_type)
        if not reason:
            return JsonResponse({'status': 'ignored', 'type': event_type})

        suppressed = []
        for email in recipients:
            email = (email or '').strip()
            if not email:
                continue
            record_suppression(email=email, reason=reason)
            # Suppress matching leads and annotate their latest send log.
            leads = DesignerLead.objects.filter(email__iexact=email)
            leads.update(status='Suppressed', needs_review=False)
            latest_log = (
                EmailLog.objects.filter(lead__in=leads, status='Sent')
                .order_by('-sent_at').first()
            )
            if latest_log:
                latest_log.reason = f'{event_type}: provider reported'[:255]
                latest_log.save(update_fields=['reason'])
            suppressed.append(email)

        logger.info("Resend %s -> suppressed %s", event_type, suppressed)
        return JsonResponse({'status': 'ok', 'suppressed': suppressed})
