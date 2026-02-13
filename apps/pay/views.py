import threading
from threading import Thread
from django.http import HttpResponse

import sys
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

from apps.pay.models import Wallets
from apps.utils.email_sender import resend_sendmail
from .models import Payment, Transfers, Banks
from rest_framework.views import APIView, status
from .serializers import *
from rest_framework.response import Response
from django.contrib import messages
from decouple import config
from paystackapi.verification import Verification
from paystackapi.transfer import Transfer
from paystackapi.trecipient import TransferRecipient
from django.contrib.auth.hashers import check_password
from .threads import TransferThread
import importlib
from paystackease import PayStackWebhook, PayStackSignatureVerifyError
from rest_framework_simplejwt.authentication import JWTAuthentication
from .config import get_paystack_keys
from django.template.loader import render_to_string

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


class Withdraw( APIView):

    def get(self, request):
        context = contextObj(request)
        # try:
        #     banks = requests.get('https://api.paystack.co/bank?country=nigeria',
        #                          headers={'Authorization': f'Bearer {PS_SECRET_KEY}'}).json()['data']
        #     for bank in banks:
        #         bank_instance,created = Banks.objects.get_or_create(code=bank['code'])
        #         bank_instance.name=bank['name']
        #         bank_instance.save()
        #     print(banks)
        # except ObjectDoesNotExist:
        #     messages.error(request,message="An error occured")
        banks = Banks.objects.all()
        user = request.user
        context['banks'] = banks
        wallet, created = Wallets.objects.get_or_create(user=user)
        context['wallet'] = wallet
        template = 'pay/withdraw.html'
        try:
            context['title'] = 'Transfer | Algoridm Pay'
        except Exception:
            pass
        return render(request, template, context)

    def post(self, request, ):

        user = request.user
        password = request.POST['password']
        if check_password(password,user.password):
            bank_code = request.POST['bank_code']
            amount = int(request.POST['amount'])
            account_number = request.POST['account_number']
            account_name = request.POST['account_name']
            description = request.POST['description']
            description = "Algoridm pay - Withdrawal " + description
            transfer = Transfers.objects.create(
                user=user, amount=amount, bank_code=bank_code, account_name=account_name, account_number=account_number)
            debit_wallet = debitwallet(user, transfer.amount)
            if debit_wallet['status']=='success':
                TransferThread(TransferToAccount(transfer, account_name,description,account_number,bank_code)).start()
                transfer_amount = "{:,.2f}".format(transfer.amount)

                data = {'status':'processing','amount':transfer_amount}
            elif debit_wallet['insufficient_balance']:

                transfer.delete()
                data = debit_wallet

        else:
            data={'invalid_password':True}

        return Response(data=data)


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
