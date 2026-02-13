from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from .models import Payment, PaymentAttempt, Invoice
from .verify import (
    verify_paystack_transaction,
    verify_flutterwave_transaction,
    verify_stripe_payment,
)


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

        return Response(
            {
                "message": f"{self.processor.capitalize()} payment verified successfully",
                "status": "success",
                "invoice_ids": [inv.id for inv in invoices],
            },
            status=status.HTTP_200_OK,
        )


# ───────────────────────────────
# 🟧 PAYSTACK CONFIRM VIEW
# ───────────────────────────────
class PaystackConfirmView(BasePaymentConfirmView):
    processor = "paystack"
    verify_func = staticmethod(verify_paystack_transaction)
    id_field = "reference"
    success_check = staticmethod(lambda data: data.get("status") == "success")


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