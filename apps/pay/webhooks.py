import json
import logging
from datetime import timedelta, datetime
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import stripe
from django.conf import settings
from .models import Payment, PaymentAttempt, Invoice, PaymentWebhookLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# LOGGING HELPER
# ---------------------------------------------------------------------
def log_webhook_event(processor, event_type, payload, reference=None, status_code=200, processed=False):
    """Save all webhook payloads for auditing."""
    try:
        PaymentWebhookLog.objects.create(
            processor=processor,
            event_type=event_type or "unknown",
            raw_payload=payload,
            reference=reference or "",
            status_code=status_code,
            processed=processed,
        )
        logger.info(f"[{processor}] Webhook event logged for ref={reference}, type={event_type}")
    except Exception as e:
        logger.error(f"[{processor}] Failed to log webhook event: {e}")


# ---------------------------------------------------------------------
# CORE PAYMENT SUCCESS HANDLER
# ---------------------------------------------------------------------
def handle_successful_payment(reference, processor_name=None, data=None):
    """
    Handles all successful payment confirmations across processors.
    Updates payment, creates/updates PaymentAttempt, and links invoices.
    """
    try:
        payment = Payment.objects.get(reference=reference)
    except Payment.DoesNotExist:
        logger.warning(f"[{processor_name}] Payment with reference {reference} not found.")
        return

    payment.status = "success"
    payment.processor = processor_name or payment.processor
    payment.is_paid = True
    payment.date_time_paid = datetime.now()
    payment.save(update_fields=["status", "processor", "is_paid", "date_time_paid"])
    logger.info(f"[{processor_name}] Payment {reference} marked successful.")

    # --- PaymentAttempt ---
    attempt, created = PaymentAttempt.objects.get_or_create(
        reference=reference,
        defaults={
            "user": payment.user,
            "processor": processor_name or payment.processor,
            "amount": payment.amount,
            "currency": payment.currency,
            "status": "success",
            "is_successful": True,
        },
    )
    if not created:
        attempt.status = "success"
        attempt.is_successful = True
        attempt.processor = processor_name or attempt.processor
        attempt.updated_at = datetime.now()
        attempt.save(update_fields=["status", "is_successful", "processor", "updated_at"])
    logger.info(f"[{processor_name}] PaymentAttempt updated/created for {reference}.")

    # --- Link to Invoice(s) ---
    linked_invoices = Invoice.objects.filter(payment=payment)
    for invoice in linked_invoices:
        invoice.payment_attempts.add(attempt)
        invoice.is_active = True
        invoice.is_expired = False
        invoice.start_date = invoice.start_date or payment.date_time_paid.date()
        invoice.expiry_date = invoice.expiry_date or (invoice.start_date + timedelta(days=30))
        invoice.save(update_fields=["is_active", "is_expired", "start_date", "expiry_date"])
        logger.info(f"[{processor_name}] Invoice {invoice.id} activated for user {invoice.user_id}.")


# ---------------------------------------------------------------------
# BASE WEBHOOK VIEW
# ---------------------------------------------------------------------
class BaseWebhookView(APIView):
    """Reusable base class for all payment webhooks."""

    processor_name = None          # e.g., "Paystack"
    success_event_types = []       # list of success event names

    def parse_json_payload(self, request):
        try:
            return json.loads(request.body.decode("utf-8"))
        except ValueError:
            log_webhook_event(self.processor_name, "invalid_json", request.body, status_code=400)
            return None

    def handle_event(self, event_type, data, reference=None):
        log_webhook_event(self.processor_name, event_type, data, reference)
        logger.info(f"[{self.processor_name}] Webhook event: {event_type}, ref={reference}")

        if event_type in self.success_event_types and reference:
            handle_successful_payment(reference, self.processor_name, data)
            log_webhook_event(self.processor_name, event_type, data, reference, processed=True)

        return Response({"status": "ok"}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------
# PAYSTACK WEBHOOK
# ---------------------------------------------------------------------
@method_decorator(csrf_exempt, name="dispatch")
class PaystackWebhookView(BaseWebhookView):
    processor_name = "Paystack"
    success_event_types = ["charge.success"]

    def post(self, request):
        payload = self.parse_json_payload(request)
        if not payload:
            return Response({"error": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)

        event_type = payload.get("event")
        data = payload.get("data", {})
        reference = data.get("reference")

        # ✅ Confirm success
        if event_type == "charge.success" and data.get("status") == "success":
            return self.handle_event(event_type, payload, reference)

        log_webhook_event(self.processor_name, event_type or "unknown", payload, reference, processed=False)
        return Response({"status": "ignored"}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------
# FLUTTERWAVE WEBHOOK
# ---------------------------------------------------------------------
@method_decorator(csrf_exempt, name="dispatch")
class FlutterwaveWebhookView(BaseWebhookView):
    processor_name = "Flutterwave"
    success_event_types = ["charge.completed"]

    def post(self, request):
        payload = self.parse_json_payload(request)
        if not payload:
            return Response({"error": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)

        event_type = payload.get("event")
        data = payload.get("data", {})
        reference = data.get("tx_ref")

        # ✅ Flutterwave’s actual success event is “charge.completed” + status == “successful”
        if event_type == "charge.completed" and data.get("status") == "successful":
            return self.handle_event(event_type, payload, reference)

        log_webhook_event(self.processor_name, event_type or "unknown", payload, reference, processed=False)
        return Response({"status": "ignored"}, status=status.HTTP_200_OK)



@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(BaseWebhookView):
    processor_name = "Stripe"
    success_event_types = ["payment_intent.succeeded"]

    def post(self, request):
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

        try:
            event = stripe.Webhook.construct_event(
                payload=payload,
                sig_header=sig_header,
                secret=settings.STRIPE_WEBHOOK_SECRET,
            )
        except ValueError:
            log_webhook_event(self.processor_name, "invalid_payload", payload, status_code=400)
            return Response({"error": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)

        except stripe.error.SignatureVerificationError:
            log_webhook_event(self.processor_name, "invalid_signature", payload, status_code=400)
            return Response({"error": "Invalid signature"}, status=status.HTTP_400_BAD_REQUEST)

        event_type = event["type"]
        data = event["data"]["object"]

        # Extract your internal reference
        reference = data.get("metadata", {}).get("attempt_reference")

        log_webhook_event(self.processor_name, event_type, event, reference)

        # ────────────── SUCCESS EVENT ──────────────
        if event_type == "payment_intent.succeeded" and reference:
            try:
                attempt = PaymentAttempt.objects.filter(
                    reference=reference,
                    processor="stripe",
                ).first()

                if not attempt:
                    log_webhook_event(
                        self.processor_name,
                        event_type,
                        event,
                        reference,
                        processed=False,
                    )
                    return Response({"status": "ignored"}, status=status.HTTP_200_OK)

                # Mark attempt
                attempt.status = "success"
                attempt.is_successful = True
                attempt.processor_payment_id = data.get("id")
                attempt.save(
                    update_fields=["status", "is_successful", "processor_payment_id"]
                )

                # Core handler (payment + invoice activation)
                handle_successful_payment(reference, self.processor_name, data)

                log_webhook_event(
                    self.processor_name,
                    event_type,
                    event,
                    reference,
                    processed=True,
                )

            except Exception as e:
                log_webhook_event(
                    self.processor_name,
                    "processing_error",
                    str(e),
                    reference,
                    status_code=500,
                )
                return Response(
                    {"error": "Webhook processing failed"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        return Response({"status": "ok"}, status=status.HTTP_200_OK)