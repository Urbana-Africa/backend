from rest_framework import serializers

from apps.pay.models import Invoice
from apps.pay.serializers import PaymentSerializer
from .models import Customer, Address, OrderTracking, Wishlist, CartItem, Order, OrderItem, ReturnRequest
from apps.core.serializers import ColorSerializer, ProductSerializer, SizesSerializer


class CustomerSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Customer
        fields = ['id', 'user', 'phone', 'avatar', 'created_at']


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = '__all__'
        read_only_fields = ['customer', 'created_at']


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = '__all__'

    def to_representation(self, instance):
        data =  super().to_representation(instance)
        data['payment'] = PaymentSerializer(instance.payment).data
        return data


class CheckoutSerializer(serializers.Serializer):
    shipping_address_id = serializers.IntegerField(required=False, allow_null=True)

    # email = serializers.EmailField()
    # first_name = serializers.CharField()
    # last_name = serializers.CharField()
    # address = serializers.CharField()
    line1 = serializers.CharField()
    line2 = serializers.CharField(required=False,allow_null = True)
    postal_code = serializers.CharField()
    state = serializers.CharField()
    city = serializers.CharField()
    country = serializers.CharField()

    shipping_method = serializers.CharField()
    shipping_amount = serializers.IntegerField()
    # payment_method = serializers.CharField()


    
class OrderTrackingSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderTracking
        fields = ['tracking_number', 'carrier', 'current_status', 'last_updated', 'estimated_delivery']




class WishlistSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)

    class Meta:
        model = Wishlist
        fields = ['id', 'product', 'added_at']


class CartItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = "__all__"

    def get_subtotal(self, obj):
        return obj.subtotal()
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['size'] = SizesSerializer(instance.size).data
        data['color'] = ColorSerializer(instance.color).data
        return data

class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)
    color = ColorSerializer(read_only=True)
    size = SizesSerializer(read_only=True)
    # subtotal = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = "__all__"
        # fields = ['id', 'product', 'quantity', 'price','color']

    # def get_subtotal(self, obj):
    #     return obj.subtotal()


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    shipping_address = AddressSerializer(read_only=True)

    class Meta:
        model = Order
        fields = "__all__"
        # fields = ['id', 'order_id', 'shipping_address', 'total_amount', 'status', 'created_at', 'items']

    def to_representation(self, instance):
        data =  super().to_representation(instance)
        data['items'] = OrderItemSerializer(instance.items,many=True).data
        data['invoice'] = InvoiceSerializer(instance.invoice).data
        return data

class ReturnRequestSerializer(serializers.ModelSerializer):
    order_item = OrderItemSerializer(read_only=True)

    class Meta:
        model = ReturnRequest
        fields = "__all__"
        # fields = ['id', 'order_item', 'reason', 'status', 'created_at', 'resolved_at']

    def to_representation(self, instance):
        data =  super().to_representation(instance)
        data['order_item'] = OrderItemSerializer(instance.order_item).data
        return data