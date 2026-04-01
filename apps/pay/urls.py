from django.urls import path

from apps.pay.confirm import FlutterwaveConfirmView, PaystackConfirmView
from apps.pay.initialize import InitializeFlutterwavePayment, InitializePaystackPayment, InitializeStripePayment
from apps.pay.webhooks import FlutterwaveWebhookView, PaystackWebhookView, StripeWebhookView
from . import views as payviews
from rest_framework.routers import DefaultRouter
from .views import (
    AccountDetailView,
    FlutterWaveVerifyAccountNumber,
    FlutterwaveBanksView,
    InitiatePayoutView,
    WithdrawalStatusView,
    WithdrawalListView,
    CustomerWalletSummaryView,
    WalletPaymentView,
)


router = DefaultRouter(trailing_slash=False)


urlpatterns = [
    # ─── Wallet — Designer ────────────────────────────────────────────────────
    path("wallet/summary", payviews.WalletSummaryView.as_view()),
    path("withdrawals", WithdrawalListView.as_view()),
    path("withdrawals/request", payviews.RequestWithdrawalView.as_view()),
    path("withdrawals/<str:withdrawal_id>/status", WithdrawalStatusView.as_view()),
    path("withdrawals/<str:withdrawal_id>/approve", payviews.ApproveWithdrawalView.as_view()),
    path("withdrawals/<str:withdrawal_id>/process", payviews.ProcessWithdrawalView.as_view()),
    path("withdrawals/<str:withdrawal_id>/complete", payviews.CompleteWithdrawalView.as_view()),
    path("withdrawals/<str:withdrawal_id>/fail", payviews.FailWithdrawalView.as_view()),
    path("fw/initiate-payout", InitiatePayoutView.as_view()),

    # ─── Wallet — Customer ────────────────────────────────────────────────────
    path("customer-wallet/summary", CustomerWalletSummaryView.as_view()),
    path("customer-wallet/pay", WalletPaymentView.as_view()),

    # ─── Escrow ───────────────────────────────────────────────────────────────
    path("escrow/<str:escrow_id>/release", payviews.ReleaseEscrowView.as_view()),

    # ─── Core payment flows ───────────────────────────────────────────────────
    path("", payviews.Dashboard.as_view(), name="pay_dashboard"),
    path("my-transactions", payviews.MyTransactions.as_view(), name="my_transactions"),
    path("my-payments", payviews.MyPayments.as_view(), name="my_payments"),
    path("check-account-number", payviews.CheckAccountNumber.as_view(), name="check_account_number"),
    path("invoices", payviews.InvoicesView.as_view()),
    path("payments", payviews.PaymentView.as_view()),

    # ─── Webhooks ─────────────────────────────────────────────────────────────
    path("webhook/flutterwave", FlutterwaveWebhookView.as_view()),
    path("webhook/paystack", PaystackWebhookView.as_view()),
    path("webhooks/stripe/", StripeWebhookView.as_view(), name="stripe-webhook"),

    # ─── Subscribe / cancel ───────────────────────────────────────────────────
    path("subscribe", payviews.Subscribe.as_view(), name="subscribe"),
    path("cancel-payment/<str:pay_id>", payviews.CancelPayment.as_view(), name="cancel_payment"),

    # ─── Payment gateway ─────────────────────────────────────────────────────
    path("confirm/flutterwave", FlutterwaveConfirmView.as_view()),
    path("confirm/paystack", PaystackConfirmView.as_view()),
    path("init/flutterwave", InitializeFlutterwavePayment.as_view()),
    path("init/paystack", InitializePaystackPayment.as_view()),
    path("init/stripe", InitializeStripePayment.as_view()),

    # ─── Bank account / bank list ─────────────────────────────────────────────
    path("account", AccountDetailView.as_view(), name="account-detail"),
    path("banks", FlutterwaveBanksView.as_view()),
    path("fw/verify-account", FlutterWaveVerifyAccountNumber.as_view()),
]
