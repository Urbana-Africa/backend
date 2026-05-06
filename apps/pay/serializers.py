from rest_framework import serializers

from apps.authentication.serializers import UserSerializer
from .models import *


from rest_framework import serializers
from .models import Wallet, WalletTransaction, Escrow, Withdrawal


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = "__all__"
        read_only_fields = ["available_balance", "pending_balance"]


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = "__all__"
        read_only_fields = ["status", "completed_at"]


class EscrowSerializer(serializers.ModelSerializer):
    class Meta:
        model = Escrow
        fields = "__all__"
        read_only_fields = ["status", "released_at"]


class WithdrawalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Withdrawal
        fields = "__all__"
        read_only_fields = ["status", "flutterwave_transfer_id"]

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = '__all__'

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['user'] = UserSerializer(instance.user,many=False).data
        return representation

class TransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transfers
        fields = '__all__'


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = '__all__'

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.payment:
            representation['payment'] = PaymentSerializer(instance.payment, many=False).data
        
        # Include user info for payment processors
        representation['user'] = {
            'email': instance.user.email,
            'full_name': instance.user.get_full_name() or instance.user.username
        }
        return representation

class AccountDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountDetail
        fields = [
            "id", "account_name", "account_number", "bank_code",
            "bank_name", "country", "account_type",
            "recipient_code", "created_at", "updated_at"
        ]
        read_only_fields = ["recipient_code", "created_at", "updated_at"]