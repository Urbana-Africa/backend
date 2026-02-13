from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
import stripe
from .models import Invoice, PaymentAttempt
from .config import get_paystack_keys, get_flutterwave_keys, get_stripe_keys


# ───────────────────────────────
# 🧩 BASE INITIALIZER (INVOICE-BASED)
# ───────────────────────────────
class BaseInitializeInvoicePayment(APIView):
    """
    Base class for initializing invoice payments across gateways.
    Uses Invoice instead of Payment for validation.
    """
    permission_classes = [IsAuthenticated]
    processor_name = None  # e.g., 'paystack', 'flutterwave'

    def validate_invoice(self, reference, amount):
        if not reference or not amount:
            return None, Response({"error": "Reference and amount are required"}, status=400)

        invoice = Invoice.objects.filter(id=reference, is_deleted=False).first()
        if not invoice:
            return None, Response({"error": "Invalid or expired invoice reference"}, status=400)

        if invoice.payment and getattr(invoice.payment, "status", None) == "success":
            return None, Response({"error": "This invoice has already been paid"}, status=400)

        if int(invoice.amount) != int(amount):
            return None, Response({"error": "Amount mismatch with invoice"}, status=400)

        return invoice, None

    def log_attempt(self, invoice):
        """
        Create and link a PaymentAttempt to this invoice.
        Returns the PaymentAttempt object.
        """
        attempt = PaymentAttempt.objects.create(
            processor=self.processor_name,
            status="initialized"
        )
        invoice.payment_attempts.add(attempt)
        invoice.save()
        return attempt


# ───────────────────────────────
# 💳 PAYSTACK INITIALIZER (Modal)
# ───────────────────────────────
class InitializePaystackPayment(BaseInitializeInvoicePayment):
    processor_name = "paystack"

    def post(self, request):
        email = request.user.email
        invoice_id = request.data.get("reference")  # invoice.id
        amount = request.data.get("amount")

        invoice, error = self.validate_invoice(invoice_id, amount)
        if error:
            return error

        # Get keys (public key used in modal)
        keys = get_paystack_keys()
        public_key = keys["public_key"]
        # Log attempt and use its reference instead of invoice ID
        attempt = self.log_attempt(invoice)

        # Return data needed by Paystack modal
        return Response({
            "status": "success",
            "processor": "paystack",
            "public_key": public_key,
            "reference": attempt.reference,  # ✅ Use PaymentAttempt reference
            "invoice_id": invoice.id,
            "amount": int(amount),
            "email": email,
            "currency": "NGN",
        })


# ───────────────────────────────
# 🦋 FLUTTERWAVE INITIALIZER (Modal)
# ───────────────────────────────
class InitializeFlutterwavePayment(BaseInitializeInvoicePayment):
    processor_name = "flutterwave"

    def post(self, request):
        email = request.user.email
        invoice_id = request.data.get("reference")  # invoice.id
        amount = request.data.get("amount")
        currency = request.data.get("currency", "NGN")

        invoice, error = self.validate_invoice(invoice_id, amount)
        if error:
            return error

        # Get Flutterwave keys
        keys = get_flutterwave_keys()
        public_key = keys["public_key"]

        # Log attempt and use its reference
        attempt = self.log_attempt(invoice)

        # Return values for Flutterwave modal
        return Response({
            "status": "success",
            "processor": "flutterwave",
            "public_key": public_key,
            "reference": attempt.reference,  # ✅ Use PaymentAttempt reference
            "invoice_id": invoice.id,
            "amount": str(amount),
            "currency": currency,
            "email": email,
            "customer_name": request.user.get_full_name(),
            "description": invoice.purpose or "Invoice Payment",
        })



class InitializeStripePayment(BaseInitializeInvoicePayment):
    processor_name = "stripe"

    def post(self, request):
        invoice_id = request.data.get("reference")
        currency = request.data.get("currency", "USD").lower()
        amount = request.data.get("amount")

        # 🔐 Invoice validation

        invoice, error = self.validate_invoice(invoice_id, amount)
        if error:
            return error

        amount = invoice.amount  # ✅ invoice is source of truth

        keys = get_stripe_keys()
        stripe.api_key = keys["secret_key"]

        # 🧾 Log attempt first (your existing pattern)
        attempt = self.log_attempt(invoice)

        try:
            intent = stripe.PaymentIntent.create(
                amount=int(amount * 100),  # cents
                currency=currency,
                automatic_payment_methods={"enabled": True},
                metadata={
                    "invoice_id": str(invoice.id),
                    "invoice_reference": invoice.reference,
                    "attempt_reference": attempt.reference,
                    "user_id": str(request.user.id),
                },
                idempotency_key=f"invoice-{invoice.reference}",
            )

        except stripe.error.StripeError as e:
            attempt.status = "failed"
            attempt.save(update_fields=["status"])
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 🔗 Persist Stripe intent ID
        attempt.external_reference = intent.id
        attempt.save(update_fields=["external_reference"])

        return Response(
            {
                "status": "success",
                "processor": "stripe",
                "public_key": keys["public_key"],
                "client_secret": intent.client_secret,
                "reference": attempt.reference,  # internal ref
                "invoice_id": invoice.id,
                "amount": int(amount),
                "currency": currency.upper(),
                "email": request.user.email,
            },
            status=status.HTTP_200_OK,
        )