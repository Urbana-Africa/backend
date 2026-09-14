import logging
import threading
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.template.loader import render_to_string

from .models import Payment, PaymentAttempt, Invoice
from .verify import (
    verify_flutterwave_transaction,
    verify_stripe_payment,
)
from apps.customers.models import Order
from apps.utils.email_sender import resend_sendmail

logger = logging.getLogger(__name__)


# ───────────────────────────────
# 🧩 BASE CONFIRM VIEW (INVOICE-COMPATIBLE)
# ───────────────────────────────
class BasePaymentConfirmView(APIView):
    """
    Base class for confirming payments across processors.
    Uses Invoice relationships instead of raw Payment reference.
    """

    processor = None
    verify_func = None  # should be a staticmethod in subclasses
    id_field = None     # e.g., 'reference' or 'transaction_id'
    success_check = None  # staticmethod for success check

    def post(self, request):
        transaction_id = request.data.get(self.id_field)
        if not transaction_id:
            return Response(
                {"error": f"{self.id_field} is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find payment attempt
        attempt = PaymentAttempt.objects.filter(
            processor=self.processor,
            processor_payment_id=request.data['reference']
        ).first()

        if not attempt:
            attempt = PaymentAttempt.objects.filter(
                processor=self.processor,
                reference=request.data['reference']
            ).first()

        if not attempt:
            return Response(
                {"error": "Payment attempt not found for this transaction."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ────────────── VERIFY TRANSACTION ──────────────
        verify_result = self.verify_func(transaction_id)
        if verify_result["status"] != "success":
            attempt.status = "failed"
            attempt.is_successful = False
            attempt.save(update_fields=["status", "is_successful"])
            return Response(
                {"error": verify_result["message"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = verify_result["data"]
        if not self.success_check(data):
            attempt.status = "failed"
            attempt.is_successful = False
            attempt.save(update_fields=["status", "is_successful"])
            return Response(
                {"error": "Transaction not successful"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ────────────── VERIFIED SUCCESS ──────────────
        processor_payment_id = (
            data.get("id")
            or data.get("reference")
            or data.get("transaction_id")
            or transaction_id
        )

        attempt.status = "success"
        attempt.is_successful = True
        attempt.processor_payment_id = processor_payment_id
        attempt.save(update_fields=["status", "is_successful", "processor_payment_id"])

        # ────────────── LINK INVOICE ──────────────
        invoices = Invoice.objects.filter(payment_attempts=attempt)
        if not invoices.exists():
            return Response(
                {"error": "No invoice found linked to this payment attempt."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Create or update Payment record
        invoice = invoices.first()
        payment, _ = Payment.objects.get_or_create(
            reference=invoice.id,
            defaults={
                "user": invoice.user,
                "amount": invoice.amount,
                "processor": self.processor,
                "status": "success",
                "is_paid": True,
                "date_time_paid": timezone.now(),
            },
        )
        payment.mark_as_paid(processor_id=processor_payment_id)

        # Update all linked invoices
        for invoice in invoices:
            invoice.payment = payment
            invoice.save()

            # Update Order status (emails are sent via the canonical idempotent
            # notification helpers below, not a duplicate template-based send).
            try:
                order = Order.objects.filter(invoice=invoice).first()
                if order and order.status == "pending":
                    order.status = "processing"
                    order.save(update_fields=["status"])
            except Exception as e:
                logger.error("Error updating order status after payment: %s", e)

            # Notify designers + customer of the new paid order (idempotent —
            # safe even if the webhook or checkout.py already triggered them).
            try:
                if order:
                    from apps.utils.notifications import (
                        send_designer_new_order,
                        send_customer_order_confirmed,
                    )
                    for item in order.items.select_related("product").all():
                        try:
                            send_designer_new_order(item)
                        except Exception as e:
                            logger.error("Error sending designer order email: %s", e)
                        try:
                            send_customer_order_confirmed(item)
                        except Exception as e:
                            logger.error("Error sending customer order confirmation email: %s", e)
            except Exception as e:
                logger.error("Error sending order emails: %s", e)

        return Response(
            {
                "message": f"{self.processor.capitalize()} payment verified successfully",
                "status": "success",
                "invoice_ids": [inv.id for inv in invoices],
            },
            status=status.HTTP_200_OK,
        )


# ───────────────────────────────
# 🦋 FLUTTERWAVE CONFIRM VIEW
# ───────────────────────────────
class FlutterwaveConfirmView(BasePaymentConfirmView):
    processor = "flutterwave"
    verify_func = staticmethod(verify_flutterwave_transaction)
    id_field = "transaction_id"
    success_check = staticmethod(lambda data: data.get("status") == "successful")



class StripeConfirmView(BasePaymentConfirmView):
    processor = "stripe"
    verify_func = staticmethod(verify_stripe_payment)
    id_field = "reference"  # PaymentAttempt.reference
    success_check = staticmethod(lambda data: data.get("status") == "succeeded")