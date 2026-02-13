from rest_framework import serializers

from apps.authentication.serializers import UserSerializer
from .models import *



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
            representation['payment'] = PaymentSerializer(instance.payment,many=False).data
        return representation
