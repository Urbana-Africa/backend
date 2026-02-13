from django.urls import path

from apps.pay.confirm import FlutterwaveConfirmView, PaystackConfirmView
from apps.pay.initialize import InitializeFlutterwavePayment, InitializePaystackPayment, InitializeStripePayment
from apps.pay.webhooks import FlutterwaveWebhookView, PaystackWebhookView, StripeWebhookView
from . import views as payviews

urlpatterns = [
    # path('make-payment', payviews.MakePayment.as_view(),name='pay'),
    path('withdraw', payviews.Withdraw.as_view(), name='withdraw'),
    path('', payviews.Dashboard.as_view(), name='pay_dashboard'),
    path('my-transactions', payviews.MyTransactions.as_view(),
         name='my_transactions'),
    path('my-payments', payviews.MyPayments.as_view(), name='my_payments'),
    path('check-account-number', payviews.CheckAccountNumber.as_view(),
         name='check_account_number'),
    path("invoices", payviews.InvoicesView.as_view()),
    path("payments", payviews.PaymentView.as_view()),
    path("webhook/flutterwave", FlutterwaveWebhookView.as_view()),
    path("webhook/paystack", PaystackWebhookView.as_view()),
path("webhooks/stripe/", StripeWebhookView.as_view(), name="stripe-webhook"),

    path("subscribe", payviews.Subscribe.as_view(), name='subscribe'),
    path('cancel-payment/<str:pay_id>',
         payviews.CancelPayment.as_view(), name='cancel_payment'),
    path("confirm/flutterwave", FlutterwaveConfirmView.as_view(),),
    path("confirm/paystack", PaystackConfirmView.as_view()),
    path("init/flutterwave", InitializeFlutterwavePayment.as_view()),
    path("init/paystack", InitializePaystackPayment.as_view()),
    path("init/stripe", InitializeStripePayment.as_view()),
    # path("capture-paypal-order/", payviews.CapturePayPalOrder.as_view(), name="paypal-capture"),
]
