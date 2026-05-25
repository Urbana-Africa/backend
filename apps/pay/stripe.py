"""
DEPRECATED: This module previously contained a standalone Stripe checkout session helper.
The active Stripe integration lives in:
  - apps.pay.initialize.InitializeStripePayment (creates PaymentIntent)
  - apps.pay.confirm.StripeConfirmView (confirms payment server-side)
  - apps.pay.webhooks.StripeWebhookView (handles payment_intent.succeeded events)
  - apps.pay.services.withdrawals._fire_stripe_transfer (designer payouts)
Do not use StripeMakePayment — it was a stub with hardcoded values.
"""