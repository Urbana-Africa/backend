from rest_framework import serializers
from django.utils import timezone

from apps.core.models import (
    Product, Category, Brand, Currency, Sizes,
    MediaAsset, Review, ShippingMethod,
    Country
)
from apps.core.serializers import ColorSerializer, MediaAssetSerializer, SizesSerializer
from apps.designers.models import (
       Designer, DesignerProduct, Collection,
    DesignerAnalytics, ShippingOption,
    DesignerOrder, ShipmentTracking,
    InventoryAlert, Promotion,
)

from apps.customers.models import (
    Customer, Address, Wishlist,
    CartItem, Order, OrderItem,
    ReturnRequest, OrderTracking
)




# =====================================================
# GENERIC BASE SERIALIZER
# =====================================================

class AdminBaseSerializer(serializers.ModelSerializer):
    class Meta:
        fields = "__all__"


# =====================================================
# CUSTOMER MANAGEMENT
# =====================================================

class AdminCustomerSerializer(AdminBaseSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta(AdminBaseSerializer.Meta):
        model = Customer


class AdminAddressSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Address


class AdminWishlistSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Wishlist


class AdminCartItemSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = CartItem


# =====================================================
# PRODUCT & CATALOG MANAGEMENT
# =====================================================

class AdminProductSerializer(AdminBaseSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    media = MediaAssetSerializer(many=True, read_only=True)
    colors = ColorSerializer(many=True, read_only=True)
    sizes = SizesSerializer(many=True, read_only=True)

    class Meta(AdminBaseSerializer.Meta):
        model = Product
        read_only_fields = ["slug", "sku", "created_at"]


class AdminCategorySerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Category


class AdminBrandSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Brand


class AdminCurrencySerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Currency


class AdminSizesSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Sizes


class AdminMediaAssetSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = MediaAsset


class AdminReviewSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Review


class AdminShippingMethodSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = ShippingMethod


class AdminCountrySerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Country


# =====================================================
# ORDER MANAGEMENT
# =====================================================

class AdminOrderItemSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = OrderItem


class AdminOrderSerializer(AdminBaseSerializer):
    customer_email = serializers.EmailField(
        source="customer.user.email",
        read_only=True
    )
    items = AdminOrderItemSerializer(many=True)

    class Meta(AdminBaseSerializer.Meta):
        model = Order

    # def to_representation(self, instance):

    #     data =  super().to_representation(instance)
    #     order
    #     return data



class AdminOrderTrackingSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = OrderTracking


# =====================================================
# RETURN MANAGEMENT
# =====================================================

class AdminReturnRequestSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = ReturnRequest
        read_only_fields = ["return_id", "created_at", "resolved_at"]

    def update(self, instance, validated_data):
        admin_status = validated_data.get("admin_status")

        if admin_status in ["returned", "rejected"]:
            instance.resolved_at = timezone.now()

        instance = super().update(instance, validated_data)

        if admin_status == "returned":
            instance.order_item.status = "returned"
            instance.order_item.save()

        return instance


# =====================================================
# DESIGNER MANAGEMENT
# =====================================================

class AdminDesignerSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    email = serializers.CharField(source="user.email")

    class Meta:
        model = Designer
        fields = "__all__"

    def get_full_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"

class AdminDesignerProductSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = DesignerProduct


class AdminCollectionSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Collection


class AdminDesignerAnalyticsSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = DesignerAnalytics


class AdminShippingOptionSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = ShippingOption


class AdminDesignerOrderSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = DesignerOrder


class AdminShipmentTrackingSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = ShipmentTracking


class AdminInventoryAlertSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = InventoryAlert


class AdminPromotionSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Promotion