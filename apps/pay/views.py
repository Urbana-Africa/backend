import threading
from threading import Thread
from django.http import HttpResponse
# accounts/views.py
from django.shortcuts import get_object_or_404
from django.db import transaction
from .models import AccountDetail
from .serializers import AccountDetailSerializer
import requests  # or use flutterwave-python SDK if you prefer
import sys
from typing import Dict, Optional
from datetime import datetime
from time import sleep
from django.shortcuts import render
from django.core.exceptions import ObjectDoesNotExist
from django.contrib import messages
from django.http import HttpResponse
from django.views import View
from django.utils import timezone
from pytz import utc
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum
from .models import WalletTransaction
from apps.pay.models import Wallet
from apps.utils.email_sender import resend_sendmail
from .models import Payment, Transfers
from rest_framework.views import APIView, status
from .serializers import *
from rest_framework.response import Response
from django.contrib import messages
from decouple import config
from paystackapi.verification import Verification
from paystackapi.transfer import Transfer
from paystackapi.trecipient import TransferRecipient
import importlib
from paystackease import PayStackWebhook, PayStackSignatureVerifyError
from rest_framework_simplejwt.authentication import JWTAuthentication
from .config import get_flutterwave_keys, get_paystack_keys
from django.template.loader import render_to_string
from .models import Wallet, Escrow, Withdrawal, Invoice
from .serializers import WalletSerializer, WalletTransactionSerializer, WithdrawalSerializer




ENV = config("ENV")


def contextObj(request):
    data = {}
    
    data['public_key'] = 'PAYSTACK_PUBLIC_KEY'

    return data



def cached_import(module_path, class_name):
    # Check whether module is loaded and fully initialized.
    if not (
        (module := sys.modules.get(module_path))
        and (spec := getattr(module, "__spec__", None))
        and getattr(spec, "_initializing", False) is False
    ):
        module = importlib.import_module(module_path)
    return getattr(module, class_name)



def import_string(dotted_path):
    """
    Import a dotted module path and return the attribute/class designated by the
    last name in the path. Raise ImportError if the import failed.
    """
    try:
        module_path, class_name = dotted_path.rsplit(".", 1)
    except ValueError as err:
        raise ImportError("%s doesn't look like a module path" % dotted_path) from err

    try:
        return cached_import(module_path, class_name)
    except AttributeError as err:
        raise ImportError(
            'Module "%s" does not define a "%s" attribute/class'
            % (module_path, class_name)
        ) from err



def activate_plan(_invoice:Invoice):
    user = _invoice.user
    profile = Profile.objects.get(user=user)
    if profile.is_admin:
        admin = Administrator.objects.get(user=user)
        plan = Plan.objects.filter(user=admin.user).order_by('date_time_added').last()
        if plan:
            if plan.name == 'Free':
                pass
            elif plan.name == 'Premium':
                unpaid_invoices = Invoice.objects.filter(user=admin.user,payment__is_paid=False, expiry_date__lt=timezone.now().date() ).count()
                if unpaid_invoices > 0:
                    plan.is_expired=True
                    plan.save()
                else:
                    plan.is_expired = False
                    plan.save()

def activate_invoice(payment):
    try:
        _invoice = Invoice.objects.get(payment=payment)
        _invoice.save()
        Thread(target=activate_plan,args=(_invoice,)).start()
    except Exception:
        pass


class TransferToAccount():
    def __init__(self,transfer,account_name,description,account_number,bank_code) -> None:
        self.transfer_obj = transfer
        self.account_name =account_name
        self.description = description
        self.account_number=account_number
        self.bank_code = bank_code

    def transfer(self):
        print("About to transfer")
        ps_transfer = TransferRecipient.create(type="nuban",
                                                            name=self.account_name,
                                                            description=self.description,
                                                            account_number=self.account_number,
                                                            bank_code=self.bank_code,
                                                            )
        recipient_code = ps_transfer['data']['recipient_code']

        transfer_instance = Transfer.initiate(recipient=recipient_code, amount=self.transfer_obj.amount, 
                                              reason=self.transfer_obj.description, reference=self.transfer_obj.transfer_id,
                                            source='balance',)
        self.transfer_obj.recipient_code = recipient_code
        self.transfer_obj.transfer_ref = transfer_instance['data']['transfer_code']
        self.transfer_obj.status = transfer_instance['data']['status']
        self.transfer_obj.save()

        transfer_failed = True
        count = 0
        while transfer_failed:
        
            if count == 3:
                break
            sleep(4.0)
            transfer_check = Transfer.fetch(
                id_or_code=self.transfer_obj.transfer_ref,
            )  
            if transfer_check['data']['status'] == 'success':
                transfer_failed = False
                self.transfer_obj.status = transfer_check['data']['status']
                self.transfer_obj.status = transfer_instance['data']['status']
                self.transfer_obj.is_approved = True
                self.transfer_obj.save()
                break

            elif transfer_check['data']['status'] == 'failed':
                self.transfer_obj.is_deleted=True
                self.transfer_obj.status = transfer_check['data']['status']
                break
            count+=1

def createcommission(user,amount,payment=None):
        partnercommission,created = PartnerCommisions.objects.get_or_create(payment=payment,user=user)
        partnercommission.amount = amount
        partnercommission.save()
        return partnercommission


def creditwallet(user,amount):
    wallet = Wallets.objects.get(user=user)
    wallet.balance+=amount
    wallet.save()
    return {'status':'success'}

def debitwallet(user, amount):
    wallet = Wallets.objects.get(user=user)
    if wallet.balance >= amount:
        wallet.balance -= amount
        wallet.save()
        return {'status':'success'}
    else:
        return {'status':'failed', 'message': "Insufficient balance",'error':"Insufficient balance",'insufficient_balance':True}


def walletbalance(request, amount):
    wallet = Wallets.objects.get(user=request.user)
    return wallet.balance


class CancelPayment( APIView):
    permission_classes = ()
    serializer_class = PaymentSerializer

    def get(self, request, pay_id):
        context = contextObj(request)
        try:
            payment = Payment.objects.get(
                payment_id=pay_id,status='pending',is_deleted=False, user=request.user)
            context['payment'] = payment
        except ObjectDoesNotExist:
            messages.error(
                request, message="Ooops! A pending payment record was not found.")
        template = 'pay/cancel_payment.html'
        try:
            context['title'] = 'Cancel payment | Algoridm Pay'
        except Exception:
            pass
        return render(request, template, context)

    def post(self, request, pay_id):
        # serializer_class = PaymentSerializer(data=request.data)
        try:
            payment = Payment.objects.get(
                payment_id=pay_id,is_deleted=False, user=request.user)
            data = {'status': 'success', 'canceled': True, }
            if payment.service=='Academy':
                applications = Applications.objects.filter(payment=payment)
                for application in applications:
                    application.delete()
            payment.delete()
        except ObjectDoesNotExist:
            data = {'status': 'failed', 'canceled': False}

        return Response(data, status=status.HTTP_202_ACCEPTED)


class CheckAccountNumber( APIView):

    def post(self, request):

        user = request.user
        bank_code = request.POST['bank_code']
        account_number = request.POST['account_number']
        verification_response = Verification.verify_account(
            account_number=account_number, bank_code=bank_code)
        if verification_response['status']:
            data = verification_response
        else:
            data = {'status': False}

        return Response(data=data)


class PaymentView( APIView):
    permission_classes = ([IsAuthenticated])
    serializer_class = PaymentSerializer

    def get(self, request):
        print(request.GET.get('payment_id'))
        try:
            payment = PaymentSerializer(Payment.objects.get(
                reference=request.GET.get('payment_id'),is_deleted=False, user=request.user),many=False).data
        except ObjectDoesNotExist:
            return Response({'status':'error','error':'Payment record not found','not_found':True},status=status.HTTP_404_NOT_FOUND)
        return Response({'payment':payment,'status':'success'},status=status.HTTP_200_OK)


class InvoicesView( APIView):
    permission_classes = ([IsAuthenticated])
    serializer_class = Invoice

    def get(self, request):
        try:
            invoice = InvoiceSerializer(Invoice.objects.get(
                id=request.GET.get('invoice_id'),is_deleted=False, user=request.user),many=False).data
        except ObjectDoesNotExist:
            return Response({'status':'error','error':'invoice record not found','not_found':True},status=status.HTTP_404_NOT_FOUND)
        return Response({'invoice':invoice,'status':'success'},status=status.HTTP_200_OK)



class Subscribe( APIView):
    permission_classes = ()
    serializer_class = PaymentSerializer

    def get(self, request):
        try:
            payment = Payment.objects.get(
                reference=request.GET.get('reference'),is_deleted=False, user=request.user, status='created')
            serialized_payment = PaymentSerializer(payment,many=False).data
            Invoice.objects.create(payment=payment,user=request.user)
        except ObjectDoesNotExist:
            return Response({'status':'error','error':'Payment record not found','not_found':True},status=status.HTTP_404_NOT_FOUND)
        return Response({'payment':serialized_payment,'status':'success'},status=status.HTTP_200_OK)


    def post(self, request):
        try:
            payment = Payment.objects.get(
                reference=request.data['reference'],is_deleted=False, user=request.user, is_paid=False)
            serialized_payment = PaymentSerializer(payment,many=False).data
            Invoice.objects.create(payment=payment,user=request.user)
        except ObjectDoesNotExist:
            return Response({'status':'error','error':'Payment record not found','not_found':True},status=status.HTTP_404_NOT_FOUND)
        return Response({'payment':serialized_payment,'status':'success'},status=status.HTTP_200_OK)


class Dashboard( APIView):

    def get(self, request):
        context = contextObj(request)

        user = request.user
        wallet, created = Wallets.objects.get_or_create(user=user)
        context['wallet'] = wallet
        transactions = {}
        payments = list(Payment.objects.filter(user=user,is_deleted=False).values())
        transfers = list(Transfers.objects.filter(user=user,is_deleted=False,status='success').values())
        commissions = list(PartnerCommisions.objects.filter(user=user,is_deleted=False,payment__is_paid=True).values())
        for transfer in transfers:
            transfer['type'] = 'transfer'
        for payment in payments:
            payment['type'] = 'payment'
        for commission in commissions:
            commission['type'] = 'commission'
        transactions =payments+transfers+commissions
        transactions.sort(key=lambda x:x['date_time_added'],reverse=True)
        context['transactions'] = transactions[:5]
        template = 'pay/dashboard.html'
        try:
            context['title'] = 'Dashboard | Algoridm Pay'
        except Exception:
            pass
        return render(request, template, context)


class MyPayments( APIView):

    def get(self, request):
        context = contextObj(request)

        user = request.user
        wallet, created = Wallets.objects.get_or_create(user=user)
        context['wallet'] = wallet

        payments = Payment.objects.filter(user=user,is_deleted=False)
        context['payments'] = payments

        template = 'pay/payments.html'
        try:
            context['title'] = 'My payments | Algoridm Pay'
        except Exception:
            pass
        return render(request, template, context)



class MyTransactions( APIView):

    def get(self, request):
        context = contextObj(request)

        user = request.user
        wallet, created = Wallets.objects.get_or_create(user=user)
        context['wallet'] = wallet
        transactions = {}
        payments = list(Payment.objects.filter(user=user,is_deleted=False).values())
        transfers = list(Transfers.objects.filter(user=user,is_deleted=False,status='success').values())
        commissions = list(PartnerCommisions.objects.filter(user=user,is_deleted=False).values())
        for transfer in transfers:
            transfer['type'] = 'transfer'
        for payment in payments:
            payment['type'] = 'payment'
        for commission in commissions:
            commission['type'] = 'commission'
        transactions =payments+transfers+commissions
        transactions.sort(key=lambda x:x['date_time_added'],reverse=True)
        context['transactions'] = transactions
        template = 'pay/transactions.html'
        try:
            context['title'] = 'My transactions | Algoridm Pay'
        except Exception:
            pass
        return render(request, template, context)



# class ConfirmPayment(APIView):
#     permission_classes = ()
#     serializer_class = PaymentSerializer

#     def verify_paystack(self, reference):
#         """Verify payment through Paystack"""
#         public_key, secret_key = get_paystack_keys()
#         headers = {
#             "Authorization": f"Bearer {secret_key}",
#             "Content-Type": "application/json",
#         }
#         url = f"https://api.paystack.co/transaction/verify/{reference}"
#         response = requests.get(url, headers=headers)
#         data = response.json()

#         if data.get("data") and data["data"].get("status") == "success":
#             return "success"
#         return data.get("data", {}).get("status", "failed")

#     def verify_stripe(self, reference):
#         """Verify payment through Stripe"""
#         stripe.api_key = get_stripe_key()
#         try:
#             session = stripe.checkout.Session.retrieve(reference)
#             if session.payment_status == "paid":
#                 return "success"
#             return session.payment_status
#         except Exception as e:
#             return str(e)

#     def verify_paypal(self, reference):
#         """Verify payment through PayPal"""
#         paypal_conf = get_paypal_keys()
#         try:
#             # Get OAuth token
#             auth_response = requests.post(
#                 f"{paypal_conf['base']}/v1/oauth2/token",
#                 auth=(paypal_conf["client_id"], paypal_conf["secret"]),
#                 data={"grant_type": "client_credentials"},
#             )
#             access_token = auth_response.json().get("access_token")

#             headers = {
#                 "Authorization": f"Bearer {access_token}",
#                 "Content-Type": "application/json",
#             }
#             order_url = f"{paypal_conf['base']}/v2/checkout/orders/{reference}"
#             order_response = requests.get(order_url, headers=headers)
#             order_data = order_response.json()

#             if order_data.get("status") == "COMPLETED":
#                 return "success"
#             return order_data.get("status", "failed")
#         except Exception as e:
#             return str(e)

#     def check_payment(self, request, payment: Payment):
#         """Handles payment verification and updates record"""
#         processor = request.data.get("processor")
#         reference = request.data.get("reference")
#         payment_status = "failed"

#         if processor == "paystack":
#             payment_status = self.verify_paystack(reference)
#         elif processor == "stripe":
#             payment_status = self.verify_stripe(reference)
#         elif processor == "paypal":
#             payment_status = self.verify_paypal(reference)

#         if payment_status == "success":
#             payment.approved = True
#             payment.status = "success"
#             payment.reference = reference
#             payment.is_paid = True
#             payment.date_time_paid = datetime.now(utc)
#             payment.date_time_approved = datetime.now(utc)
#             payment.save()
#             Thread(target=activate_invoice, args=(payment,)).start()
#         else:
#             payment.status = payment_status
#             payment.save()

#     def post(self, request):
#         reference = request.data.get("reference")
#         try:
#             payment = Payment.objects.get(reference=reference, is_deleted=False)
#         except ObjectDoesNotExist:
#             return Response(
#                 {"status": "error", "error": "Payment not found"},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         Thread(target=self.check_payment, args=(request, payment)).start()
#         return Response({"status": "success"})


def send_payment_success_email(user, payment:Payment):
    user= payment.user
    email_context = {}
    email_context["user"] = user
    email_context['amount'] = '{:20,.2f}'.format(payment.amount)
    message = render_to_string(
        "pay/payment_received.html", email_context
    )
    subject = f"Payment receipt - {'ShoolMummy'}"
    # sendmail([user.email], message, message, subject,
    #             )
    threading.Thread(
        target=resend_sendmail,
        args=(
            [user.email],
            message,
            message,
            subject,
        ),
    ).start()


class PaystackWebhookView(View):
    def post(self, request, *args, **kwargs):
        paystack_public, paystack_secret = get_paystack_keys
        payload = request.body
        signature_header = request.META["HTTP_X_PAYSTACK_SIGNATURE"]

        try:
            event = PayStackWebhook.get_event_data(paystack_secret, payload, signature_header)
        except ValueError as error:
            return HttpResponse(status=400)
        except PayStackSignatureVerifyError as error:
            return HttpResponse(status=400)
        if event["event"] == "charge.success":
            session = event["data"]
            if session["status"] == "success":
                payment = Payment.objects.get(payment_id = session["reference"])
                payment.approved = True
                payment.status = 'success'
                payment.is_paid=True
                payment.date_time_paid = datetime.now(utc)
                payment.date_time_approved = datetime.now(utc)
                payment.save()
        return HttpResponse(status=200)


class MakePaymentView(APIView):
    permission_classes=[IsAuthenticated]
    authentication_classes=[JWTAuthentication]
    def post(self,request):
        try:
            if request.data.get('name') not in ['basic','premium','free']:
                return Response({
                    'message':'invalid package selected'
                },status=status.HTTP_406_NOT_ACCEPTABLE)
            else:
                serializer=PaymentSerializer(data=request.data)
                if serializer.is_valid():
                    serialized_data=serializer.save(
                        user=request.user
                    )
                    invoice=Invoice.objects.create(
                        payment=serialized_data
                    )

                    invoice_serializer=InvoiceSerializer(invoice).data
                    invoice_serializer['payment']=serialized_data
                    return Response({
                        'data':invoice_serializer
                    },status=status.HTTP_201_CREATED)

        
        except Exception as e:
            return Response({
                'message':'error occured at {e}'
            },status=status.HTTP_400_BAD_REQUEST)



class MyWalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        serializer = WalletSerializer(wallet)
        return Response(serializer.data)


class ReleaseEscrowView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, escrow_id):
        from apps.pay.services.escrow import release_escrow
        try:
            escrow = get_object_or_404(Escrow, id=escrow_id)
            if request.user != escrow.customer and not request.user.is_superuser and not request.user.is_staff:
                return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)
            
            release_escrow(escrow_id, request.user)
            return Response({"detail": "Escrow released successfully"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class RequestWithdrawalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.pay.services.withdrawals import request_withdrawal
        amount = request.data.get("amount")
        if not amount:
            return Response({"error": "Amount is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        account_detail = AccountDetail.objects.filter(user=request.user).first()
        if not account_detail:
            return Response({"error": "No bank account details found"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            withdrawal = request_withdrawal(
                user=request.user,
                amount=amount,
                bank_code=account_detail.bank_code,
                account_number=account_detail.account_number,
                bank_name=account_detail.bank_name,
                account_name=account_detail.account_name
            )
            return Response(WithdrawalSerializer(withdrawal).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ApproveWithdrawalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, withdrawal_id):
        if not request.user.is_superuser and not request.user.is_staff:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)
        from apps.pay.services.withdrawals import approve_withdrawal
        try:
            withdrawal = approve_withdrawal(withdrawal_id, request.user)
            return Response(WithdrawalSerializer(withdrawal).data)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ProcessWithdrawalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, withdrawal_id):
        if not request.user.is_superuser and not request.user.is_staff:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)
        from apps.pay.services.withdrawals import process_withdrawal
        try:
            withdrawal = process_withdrawal(withdrawal_id, request.user)
            return Response(WithdrawalSerializer(withdrawal).data)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CompleteWithdrawalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, withdrawal_id):
        if not request.user.is_superuser and not request.user.is_staff:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)
        from apps.pay.services.withdrawals import complete_withdrawal
        try:
            withdrawal = complete_withdrawal(withdrawal_id)
            return Response(WithdrawalSerializer(withdrawal).data)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class FailWithdrawalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, withdrawal_id):
        if not request.user.is_superuser and not request.user.is_staff:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)
        from apps.pay.services.withdrawals import fail_withdrawal
        try:
            withdrawal = fail_withdrawal(withdrawal_id, reason=request.data.get("reason", ""))
            return Response(WithdrawalSerializer(withdrawal).data)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WalletSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)

        lifetime_earnings = WalletTransaction.objects.filter(
            wallet=wallet,
            transaction_type="escrow_release",
            status="completed"
        ).aggregate(total=Sum("amount"))["total"] or 0

        recent_transactions = WalletTransaction.objects.filter(
            wallet=wallet
        ).order_by("-created_at")[:10]

        return Response({
            "available_balance": wallet.available_balance,
            "pending_balance": wallet.pending_balance,
            "lifetime_earnings": lifetime_earnings,
            "currency": wallet.currency,
            "recent_activity": WalletTransactionSerializer(recent_transactions, many=True).data
        })


class InitiatePayoutView(APIView):
    """
    POST /pay/fw/initiate-payout
    Instant designer withdrawal — no admin gate.
    Fires Flutterwave transfer immediately in background.
    Returns the Withdrawal record so the frontend can poll status.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.pay.services.withdrawals import request_withdrawal
        amount = request.data.get("amount")
        if not amount:
            return Response({"error": "Amount is required"}, status=status.HTTP_400_BAD_REQUEST)

        account_detail = AccountDetail.objects.filter(user=request.user).first()
        if not account_detail:
            return Response(
                {"error": "No bank account found. Please add your bank details in Settings."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            withdrawal = request_withdrawal(
                user=request.user,
                amount=amount,
                bank_code=account_detail.bank_code,
                account_number=account_detail.account_number,
                bank_name=account_detail.bank_name,
                account_name=account_detail.account_name,
            )
            return Response({
                "status": "success",
                "message": "Withdrawal initiated. Processing in the background.",
                "withdrawal": WithdrawalSerializer(withdrawal).data,
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WithdrawalStatusView(APIView):
    """
    GET /pay/withdrawals/<id>/status
    Polls Flutterwave for live transfer status and syncs DB.
    Used by frontend background polling.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, withdrawal_id):
        from apps.pay.services.withdrawals import check_withdrawal_status
        try:
            withdrawal = Withdrawal.objects.get(id=withdrawal_id, user=request.user)
        except Withdrawal.DoesNotExist:
            return Response({"error": "Withdrawal not found"}, status=status.HTTP_404_NOT_FOUND)

        result = check_withdrawal_status(withdrawal_id)
        # Refresh after potential status update
        withdrawal.refresh_from_db()
        return Response({
            "status": result["status"],
            "flutterwave_status": result.get("flutterwave_status"),
            "withdrawal": WithdrawalSerializer(withdrawal).data,
        })


class WithdrawalListView(APIView):
    """
    GET /pay/withdrawals
    Returns paginated withdrawal history for the requesting user.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        withdrawals = Withdrawal.objects.filter(user=request.user).order_by("-created_at")
        page = int(request.query_params.get("page", 1))
        limit = int(request.query_params.get("limit", 20))
        from django.core.paginator import Paginator
        paginator = Paginator(withdrawals, limit)
        page_obj = paginator.get_page(page)
        return Response({
            "results": WithdrawalSerializer(page_obj.object_list, many=True).data,
            "total": paginator.count,
            "pages": paginator.num_pages,
            "current_page": page,
        })


class CustomerWalletSummaryView(APIView):
    """
    GET /pay/customer-wallet/summary
    Customer wallet overview — balance + recent transactions.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(
            user=request.user,
            defaults={"currency": "NGN"}
        )
        recent = WalletTransaction.objects.filter(wallet=wallet).order_by("-created_at")[:15]
        total_refunds = WalletTransaction.objects.filter(
            wallet=wallet, transaction_type="refund", status="completed"
        ).aggregate(total=Sum("amount"))["total"] or 0

        return Response({
            "available_balance": wallet.available_balance,
            "pending_balance": wallet.pending_balance,
            "currency": wallet.currency,
            "total_refunds_received": total_refunds,
            "recent_activity": WalletTransactionSerializer(recent, many=True).data,
        })


class WalletPaymentView(APIView):
    """
    POST /pay/customer-wallet/pay
    Deducts from the customer's wallet to pay an invoice.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        invoice_id = request.data.get("invoice_id")
        if not invoice_id:
            return Response({"error": "invoice_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            invoice = Invoice.objects.get(id=invoice_id, user=request.user, is_used=False)
        except Invoice.DoesNotExist:
            return Response({"error": "Invoice not found or already paid"}, status=status.HTTP_404_NOT_FOUND)

        from decimal import Decimal
        amount = Decimal(str(invoice.amount))

        try:
            wallet = Wallet.objects.select_for_update().get(user=request.user)
        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found"}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=request.user)
            if wallet.available_balance < amount:
                return Response(
                    {"error": "Insufficient wallet balance"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            wallet.available_balance -= amount
            wallet.save(update_fields=["available_balance"])

            ref = f"WALLET-PAY-{invoice_id}-{int(timezone.now().timestamp())}"

            # Create a Payment record marking wallet as the method
            payment = Payment.objects.create(
                user=request.user,
                amount=amount,
                payment_method="wallet",
                reference=ref,
                processor="manual",
                status="success",
                is_paid=True,
                date_time_paid=timezone.now(),
            )

            invoice.payment = payment
            invoice.is_active = True
            invoice.is_used = True
            invoice.save()

            WalletTransaction.objects.create(
                wallet=wallet,
                user=request.user,
                transaction_type="withdrawal",
                status="completed",
                amount=amount,
                reference=ref,
                related_payment=payment,
                description=f"Wallet payment for invoice {invoice_id}",
                completed_at=timezone.now(),
            )

        return Response({
            "status": "success",
            "message": "Payment successful",
            "payment_id": payment.id,
            "invoice_id": invoice_id,
        })
    


class AccountDetailView(APIView):
    """
    ViewSet for managing user's bank account details with Flutterwave recipient creation.
    
    - list:   GET /accounts/          → list own account details
    - create: POST /accounts/         → create + generate recipient_code on Flutterwave
    - retrieve: GET /accounts/<pk>/   → get single detail
    - update: PUT /accounts/<pk>/     → full update
    - partial_update: PATCH /accounts/<pk>/ → partial update
    """
    
    serializer_class = AccountDetailSerializer
    permission_classes = [IsAuthenticated]


    def get(self,request):
        try:
            account=AccountDetail.objects.get(user=request.user)
            return Response({
                    'data':AccountDetailSerializer(account,many=False).data,
                    'status':'success',

                },status=status.HTTP_200_OK)

        except ObjectDoesNotExist:
            return Response({
               'data':False,
            },status=status.HTTP_200_OK)
        

        except Exception as e:
            return Response({
                'data':[],
                'status':'error',
                'message':f'error occured at {e}'
            },status=status.HTTP_400_BAD_REQUEST)


    def delete(self, request):
        """
        Delete user's bank account details and also delete the beneficiary from Flutterwave.
        """
        try:
            account_detail = AccountDetail.objects.get(user=request.user)

            # ── Delete from Flutterwave first (if recipient_code exists) ──
            recipient_code = account_detail.recipient_code
            if recipient_code:
                success = self._delete_flutterwave_beneficiary(recipient_code)
                if not success:
                    # You can decide whether to continue or fail the whole operation
                    # For now, we'll log and continue (soft delete on FW side is acceptable)
                    print(f"Warning: Failed to delete Flutterwave beneficiary {recipient_code}")

            # ── Delete from database ──
            account_detail.delete()

            return Response({
                "status": "success",
                "message": "Bank account details deleted successfully"
            }, status=status.HTTP_200_OK)

        except AccountDetail.DoesNotExist:
            return Response({
                "status": "error",
                "message": "No bank account details found for this user"
            }, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            print(f"Error deleting bank account: {str(e)}")
            return Response({
                "status": "error",
                "message": "An error occurred while deleting your bank details"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Helper method (add this inside the same View class)
    def _delete_flutterwave_beneficiary(self, recipient_code: str) -> bool:
        """
        Delete a beneficiary from Flutterwave.
        Returns True if successful, False otherwise.
        """
        try:
            access_token = get_flutterwave_keys()['secret_key']
            base_url = "https://api.flutterwave.com/v3"
            
            url = f"{base_url}/beneficiaries/{recipient_code}"
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            response = requests.delete(url, headers=headers, timeout=10)

            if response.status_code in (200, 204):
                print(f"Successfully deleted Flutterwave beneficiary: {recipient_code}")
                return True

            # Log Flutterwave error but don't fail the whole delete
            print(f"Flutterwave delete failed: {response.status_code} - {response.text}")
            return False

        except requests.RequestException as e:
            print(f"Network error deleting Flutterwave beneficiary: {str(e)}")
            return False
        except Exception as e:
            print(f"Unexpected error deleting Flutterwave beneficiary: {str(e)}")
            return False

    def post(self, request):
        """
        Create or update bank account details for a user.
        Uses Flutterwave to create/fetch recipient and stores the recipient_code.
        """

        user = request.user

        account_name = request.data.get("account_name")
        # Fixed: request.GET.get() returns None or str, but you had a comma making it a tuple!
        account_number = "0690000032" if ENV == "dev" else request.data.get("account_number")
        bank_code = "044" if ENV == "dev" else request.data.get("bank_code")
        bank_name = request.data.get("bank_name")

        if not all([account_name, account_number, bank_code, bank_name]):
            return Response(
                {"error": "All bank fields are required: account_name, account_number, bank_code, bank_name"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                # Get or create AccountDetail first so we can pass it to the function
                account_detail, created = AccountDetail.objects.get_or_create(
                    user=user,
                    defaults={
                        "account_name": account_name,
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "bank_name": bank_name,
                    }
                )

                # Update fields in case they changed
                account_detail.account_name = account_name
                account_detail.account_number = account_number
                account_detail.bank_code = bank_code
                account_detail.bank_name = bank_name
                account_detail.save(update_fields=[
                    "account_name", "account_number", "bank_code", "bank_name", "updated_at"
                ])

                # Now call the improved Flutterwave function and pass the account_detail instance
                response = create_fw_transfer_recipient(
                    access_token=get_flutterwave_keys()["secret_key"],
                    name=account_name,
                    account_number=account_number,
                    bank_code=bank_code,
                    bank_name=bank_name,
                    account_detail=account_detail,   # ← This enables direct fetch by recipient_code
                )

                print("Flutterwave Recipient Response:", response)

                if response["status"] != "success":
                    return Response(
                        {
                            "error": response.get("message", "Failed to process recipient"),
                            "details": response.get("data")
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Update recipient_code from Flutterwave response (safer than assuming ["data"]["id"])
                recipient_code = response.get("recipient_code")
                if recipient_code:
                    account_detail.recipient_code = recipient_code
                    account_detail.save(update_fields=["recipient_code", "updated_at"])

                # Refresh the instance to ensure latest data
                account_detail.refresh_from_db()

        except Exception as e:
            print("Error in bank account creation:", str(e))
            return Response(
                {"error": "An unexpected error occurred while processing your bank details."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "status": "success",
                "message": "Bank account details saved successfully",
                "data": AccountDetailSerializer(account_detail).data,
            },
            status=status.HTTP_200_OK,
        )

    


class FlutterwaveBanksView(APIView):
    """
    Retrieves the list of banks in a given country using Flutterwave API.
    """

    def get(self, request):
        country = request.query_params.get(
            "country", "NG")  # default to Nigeria

        url = f"https://api.flutterwave.com/v3/banks/{country.upper()}"
        keys = get_flutterwave_keys()
        headers = {
            "Authorization": f"Bearer {keys['secret_key']}",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            return Response(
                {"status": "error",
                    "message": f"Failed to fetch banks: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY
            )

        data = resp.json()
        if data.get("status") != "success":
            return Response(
                {"status": "error", "message": data.get(
                    "message", "Unknown error")},
                status=status.HTTP_502_BAD_GATEWAY
            )

        # Return only relevant bank info
        banks = [{"name": b["name"], "code": b["code"]}
                 for b in data.get("data", [])]

        return Response({"status": "success", "banks": banks}, status=status.HTTP_200_OK)







def create_fw_transfer_recipient(
    access_token: str,
    name: str,
    account_number: str,
    bank_code: str,
    bank_name: str,
    country: str = "NG",
    currency: str = "NGN",
    email: Optional[str] = None,
    type_override: Optional[str] = None,
    # New: pass the AccountDetail instance so we can read/update recipient_code
    account_detail: Optional["AccountDetail"] = None,
) -> Dict:
    """
    Create or retrieve a Flutterwave beneficiary (transfer recipient).
    
    Returns:
    {
        "status": "success" | "error",
        "message": str,
        "data": dict | None,
        "recipient_code": str | None   # This is the Flutterwave beneficiary ID
    }
    """
    base_url = "https://api.flutterwave.com/v3"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "accept": "application/json",
    }

    recipient_type = type_override or {
        "NG": "nuban",
        "GH": "ghipss",
        "KE": "pesalink",
        "UG": "mobile_money",
    }.get(country.upper(), "nuban")

    try:
        # ── STEP 1: Try direct fetch if we already have a recipient_code ──
        if account_detail and account_detail.recipient_code:
            fetch_url = f"{base_url}/beneficiaries/{account_detail.recipient_code}"
            resp = requests.get(fetch_url, headers=headers, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    beneficiary = data.get("data", {})
                    return {
                        "status": "success",
                        "message": "Recipient fetched successfully",
                        "data": beneficiary,
                        "recipient_code": beneficiary.get("id") or beneficiary.get("recipient_code"),
                    }

            # If we get here → fetch failed (probably 404). We'll create a new one below.

        # ── STEP 2: Fallback - List beneficiaries (only if no code or fetch failed) ──
        # You can keep this for safety, but it's still not ideal for high volume.
        list_url = f"{base_url}/beneficiaries"
        params = {"currency": currency}
        resp = requests.get(list_url, headers=headers, params=params, timeout=15)

        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                for ben in data.get("data", []):
                    if (
                        ben.get("account_number") == account_number
                        and ben.get("bank_code") == bank_code   # or ben.get("account_bank")
                    ):
                        # Update model if we have it
                        if account_detail:
                            account_detail.recipient_code = ben.get("id") or ben.get("recipient_code", "")
                            account_detail.save(update_fields=["recipient_code", "updated_at"])

                        return {
                            "status": "success",
                            "message": "Recipient already exists (found via list)",
                            "data": ben,
                            "recipient_code": ben.get("id") or ben.get("recipient_code"),
                        }

        # ── STEP 3: Create new beneficiary ──
        create_url = f"{base_url}/beneficiaries"
        payload = {
            "account_bank": bank_code,
            "account_number": account_number,
            "account_name": name.strip(),
            "bank_name": bank_name.strip(),
            "currency": currency,
        }
        if email:
            payload["email"] = email.strip()

        resp = requests.post(create_url, json=payload, headers=headers, timeout=15)

        if resp.status_code not in (200, 201):
            return {
                "status": "error",
                "message": f"Creation failed: {resp.status_code} - {resp.text}",
                "data": None,
                "recipient_code": None,
            }

        data = resp.json()
        if data.get("status") != "success":
            return {
                "status": "error",
                "message": data.get("message", "Failed to create recipient"),
                "data": data,
                "recipient_code": None,
            }

        beneficiary = data.get("data", {})
        recipient_code = beneficiary.get("id") or beneficiary.get("recipient_code")   # Flutterwave usually returns "id"

        if not recipient_code:
            return {
                "status": "error",
                "message": "Created but no recipient_code/id returned",
                "data": beneficiary,
                "recipient_code": None,
            }

        # Update the model with the new code
        if account_detail:
            account_detail.recipient_code = recipient_code
            account_detail.save(update_fields=["recipient_code", "updated_at"])

        return {
            "status": "success",
            "message": "Recipient created successfully",
            "data": beneficiary,
            "recipient_code": recipient_code,
        }

    except requests.Timeout:
        return {"status": "error", "message": "Request timed out", "data": None, "recipient_code": None}
    except requests.RequestException as e:
        return {"status": "error", "message": f"Network error: {str(e)}", "data": None, "recipient_code": None}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}", "data": None, "recipient_code": None}

       
FW_BASE_URL='https://api.flutterwave.com/v3'


class FlutterWaveVerifyAccountNumber(APIView):
    def get(self,request):
        try: 
            fw_keys = get_flutterwave_keys()    
            url = "https://api.flutterwave.com/v3/accounts/resolve"
            headers = {
                "Authorization": f"Bearer {fw_keys['secret_key']}",
                 "Content-Type": "application/json"
            }

            payload = {
                "account_number":"0690000032" if ENV == 'dev' else  request.GET.get('account_number'),
                "account_bank": '044' if ENV == 'dev' else  request.GET.get('bank_code')
            }

            response = requests.post(url, json=payload, headers=headers)
            print(response.json())
            
            return Response({
                'status':'success',
                'data':response.json()['data']
            })
        
        except Exception as e:
            return Response({
                'status':'error',
                'message':f'error occured at {e}'
            })
