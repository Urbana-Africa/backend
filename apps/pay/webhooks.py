import json
import logging
import threading
import hashlib
import hmac
from datetime import timedelta, datetime
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.template.loader import render_to_string
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import stripe
from django.conf import settings
from decouple import config
from .models import Payment, PaymentAttempt, Invoice, PaymentWebhookLog
from .config import get_flutterwave_keys
from apps.customers.models import Order
from apps.utils.email_sender import resend_sendmail

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
    'reference' may be a Payment.reference OR a PaymentAttempt.reference.

    IDEMPOTENT: if the payment is already marked successful, this is a
    duplicate webhook/event and we return early without re-processing.
    This prevents double-escrow, double-wallet-credit, and double-email
    on webhook retries or duplicate deliveries.
    """
    # ── 0. Idempotency check ────────────────────────────────────────
    # If the payment is already paid, this is a duplicate event.
    existing = Payment.objects.filter(reference=reference, is_paid=True).first()
    if existing:
        logger.info(
            f"[{processor_name}] Duplicate event for reference {reference} — "
            f"payment already marked paid. Skipping to prevent double-processing."
        )
        return

    # ── 1. Resolve Payment ──────────────────────────────────────────
    payment = None
    attempt = None

    # Try as Payment reference first
    try:
        payment = Payment.objects.get(reference=reference)
    except Payment.DoesNotExist:
        pass

    # Fallback: look up by PaymentAttempt reference
    if not payment:
        attempt = PaymentAttempt.objects.filter(reference=reference).first()
        if attempt:
            # Find linked invoice and derive payment
            invoice = Invoice.objects.filter(payment_attempts=attempt).first()
            if invoice and invoice.payment:
                payment = invoice.payment
            else:
                # Create Payment from invoice data if possible
                if invoice:
                    payment, _ = Payment.objects.get_or_create(
                        reference=invoice.id,
                        defaults={
                            "user": invoice.user,
                            "amount": invoice.amount,
                            "processor": processor_name or attempt.processor,
                            "status": "success",
                            "is_paid": True,
                            "date_time_paid": datetime.now(),
                        },
                    )
                else:
                    # No invoice linked — try to find via metadata
                    metadata = (data or {}).get("metadata", {})
                    invoice_id = metadata.get("invoice_id")
                    if invoice_id:
                        invoice = Invoice.objects.filter(id=invoice_id).first()
                        if invoice:
                            payment, _ = Payment.objects.get_or_create(
                                reference=invoice.id,
                                defaults={
                                    "user": invoice.user,
                                    "amount": invoice.amount,
                                    "processor": processor_name or attempt.processor,
                                    "status": "success",
                                    "is_paid": True,
                                    "date_time_paid": datetime.now(),
                                },
                            )

    if not payment:
        logger.warning(f"[{processor_name}] Payment with reference {reference} not found.")
        return

    # ── 2. Update Payment ─────────────────────────────────────────────
    payment.status = "success"
    payment.processor = processor_name or payment.processor
    payment.is_paid = True
    payment.date_time_paid = datetime.now()
    payment.save(update_fields=["status", "processor", "is_paid", "date_time_paid"])
    logger.info(f"[{processor_name}] Payment {payment.reference} marked successful.")

    # ── 3. Update PaymentAttempt ────────────────────────────────────
    if not attempt:
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
    else:
        attempt.status = "success"
        attempt.is_successful = True
        attempt.processor = processor_name or attempt.processor
        attempt.updated_at = datetime.now()
        attempt.save(update_fields=["status", "is_successful", "processor", "updated_at"])
    logger.info(f"[{processor_name}] PaymentAttempt updated/created for {reference}.")

    # ── 4. Link to Invoice(s) ───────────────────────────────────────
    linked_invoices = Invoice.objects.filter(payment=payment)
    if not linked_invoices.exists() and attempt:
        linked_invoices = Invoice.objects.filter(payment_attempts=attempt)
        for invoice in linked_invoices:
            invoice.payment = payment
            invoice.save(update_fields=["payment"])

    for invoice in linked_invoices:
        invoice.payment_attempts.add(attempt)
        invoice.is_active = True
        invoice.is_expired = False
        invoice.start_date = invoice.start_date or payment.date_time_paid.date()
        invoice.expiry_date = invoice.expiry_date or (invoice.start_date + timedelta(days=30))
        invoice.save(update_fields=["is_active", "is_expired", "start_date", "expiry_date"])
        logger.info(f"[{processor_name}] Invoice {invoice.id} activated for user {invoice.user_id}.")

        # ── 4b. Wallet top-up invoices credit the customer's wallet ──
        try:
            from apps.pay.services.wallet_topup import credit_wallet_topup
            credit_wallet_topup(invoice, payment)
        except Exception as e:
            logger.error(f"[{processor_name}] Wallet top-up credit failed for invoice {invoice.id}: {e}")

        # ── 5. Update Order status & send order-confirmation emails ──
        try:
            order = Order.objects.filter(invoice=invoice).first()
            if order and order.status == "pending":
                order.status = "processing"
                order.save(update_fields=["status"])

                # Notify designers + customer (idempotent — safe even if the
                # client-side confirm view or checkout.py already fired them).
                from apps.utils.notifications import (
                    send_designer_new_order,
                    send_customer_order_confirmed,
                )
                for item in order.items.select_related("product").all():
                    try:
                        send_designer_new_order(item)
                    except Exception as e:
                        logger.error(f"[{processor_name}] Designer order email failed: {e}")
                    try:
                        send_customer_order_confirmed(item)
                    except Exception as e:
                        logger.error(f"[{processor_name}] Customer order email failed: {e}")

                logger.info(f"[{processor_name}] Order confirmation emails dispatched for {order.order_id}.")
        except Exception as e:
            logger.error(f"[{processor_name}] Error sending order confirmation emails: {e}")


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
# FLUTTERWAVE WEBHOOK
# ---------------------------------------------------------------------
@method_decorator(csrf_exempt, name="dispatch")
class FlutterwaveWebhookView(BaseWebhookView):
    processor_name = "Flutterwave"
    success_event_types = ["charge.completed"]

    def post(self, request):
        # Verify Flutterwave signature (SHA512 of secret_key + "|" + raw_body)
        flw_sig = request.META.get("HTTP_VERIF_HASH", "")
        secret_key = get_flutterwave_keys().get("secret_key", "")
        if not flw_sig or not secret_key:
            log_webhook_event(self.processor_name, "missing_signature", "", status_code=401)
            return Response({"error": "Missing signature"}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            expected_sig = hashlib.sha512(
                (secret_key + request.body.decode("utf-8")).encode("utf-8")
            ).hexdigest()
        except Exception:
            log_webhook_event(self.processor_name, "signature_error", "", status_code=500)
            return Response({"error": "Signature error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not hmac.compare_digest(expected_sig, flw_sig):
            log_webhook_event(self.processor_name, "invalid_signature", request.body, status_code=401)
            return Response({"error": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

        payload = self.parse_json_payload(request)
        if not payload:
            return Response({"error": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)

        event_type = payload.get("event")
        data = payload.get("data", {})
        reference = data.get("tx_ref")

        # ✅ Flutterwave's actual success event is "charge.completed" + status == "successful"
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


from rest_framework.permissions import AllowAny
from django.utils import timezone

@method_decorator(csrf_exempt, name="dispatch")
class ShippoWebhookView(APIView):
    """
    POST /pay/webhook/shippo
    Processes tracking updates from Shippo.
    If tracking status is 'DELIVERED', updates OrderItem and executes customer notification.

    Verifies the X-Shippo-Signature header using HMAC-SHA256 with the
    SHIPPO_WEBHOOK_SECRET environment variable.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        # Verify Shippo signature (HMAC-SHA256 of raw body with webhook secret)
        shippo_sig = request.headers.get("X-Shippo-Signature", "")
        if not shippo_sig:
            log_webhook_event("Shippo", "missing_signature", "", status_code=401)
            return Response({"error": "Missing signature"}, status=status.HTTP_401_UNAUTHORIZED)

        shippo_secret = getattr(settings, "SHIPPO_WEBHOOK_SECRET", "") or config("SHIPPO_WEBHOOK_SECRET", default="", cast=str)
        if not shippo_secret:
            log_webhook_event("Shippo", "missing_secret_config", "", status_code=500)
            return Response({"error": "Webhook secret not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            expected_sig = hmac.new(
                shippo_secret.encode("utf-8"),
                request.body,
                hashlib.sha256,
            ).hexdigest()
        except Exception as e:
            log_webhook_event("Shippo", "signature_error", "", status_code=500)
            return Response({"error": "Signature verification failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not hmac.compare_digest(expected_sig, shippo_sig):
            log_webhook_event("Shippo", "invalid_signature", request.body, status_code=401)
            return Response({"error": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

        payload = request.body.decode("utf-8")
        try:
            event_data = json.loads(payload)
        except ValueError:
            return Response({"error": "Invalid JSON"}, status=400)
            
        event = event_data.get("event")
        data = event_data.get("data")
        
        # Audit log the webhook event
        log_webhook_event("Shippo", event or "unknown", event_data, reference=data.get("tracking_number") if data else None)
        
        if event == "track_updated" and data:
            tracking_number = data.get("tracking_number")
            carrier = data.get("carrier")
            status_obj = data.get("tracking_status") or {}
            current_status = status_obj.get("status")
            
            if tracking_number:
                # Find the OrderItem associated with this tracking number
                from apps.customers.models import OrderItem
                order_item = OrderItem.objects.filter(tracking_number=tracking_number).first()
                if order_item:
                    # Update tracking state in database
                    try:
                        from apps.designers.models import Shipment
                        Shipment.objects.update_or_create(
                            order_item=order_item,
                            defaults={
                                "carrier": carrier or order_item.properties.get("carrier") or "",
                                "tracking_number": tracking_number,
                                "tracking_status": current_status,
                                "tracking_data": data
                            }
                        )
                    except Exception as e:
                        print(f"Error updating shipment from webhook: {e}")
                        
                    # If status is delivered, mark order item delivered and notify
                    if current_status == "DELIVERED" and order_item.status != "delivered":
                        order_item.status = "delivered"
                        order_item.designer_status = "delivered"
                        order_item.delivered_at = timezone.now()
                        order_item.save()
                        
                        # Send customer delivery confirmation email
                        from apps.utils.notifications import send_customer_order_delivered
                        try:
                            send_customer_order_delivered(order_item)
                        except Exception as e:
                            logger.error("[EMAIL] Customer delivered email failed: %s", e)
                            
                        # Log webhook as processed successfully
                        log_webhook_event("Shippo", event, event_data, reference=tracking_number, processed=True)
                        
        return Response({"status": "ok"}, status=200)