from rest_framework import serializers

from apps.authentication.serializers import UserSerializer
from apps.core.models import MediaAsset, Sizes
from apps.customers.models import Order, OrderItem, ReturnRequest
from apps.customers.serializers import AddressSerializer, CustomerSerializer
from .models import (
    Designer, Collection, DesignerProduct, DesignerStory, ProductImage, Shipment,
    ShippingOption, DesignerOrder, DesignerAnalytics, StoryView
)
from apps.core.serializers import MediaAssetSerializer, ProductSerializer
from django.utils import timezone


# =====================================================
# GENERIC BASE SERIALIZER
# =====================================================

class BaseSerializer(serializers.ModelSerializer):
    class Meta:
        fields = "__all__"

class DesignerStorySerializer(serializers.ModelSerializer):
    views_count = serializers.IntegerField(source='views.count', read_only=True)

    class Meta:
        model = DesignerStory
        fields = ['id', 'designer', 'title', 'media', 'caption', 'start_time', 'end_time', 'is_active', 'created_at', 'views_count']

class StoryViewSerializer(serializers.ModelSerializer):
    viewer = CustomerSerializer(read_only=True)

    class Meta:
        model = StoryView
        fields = ['id', 'story', 'viewer', 'viewed_at']

        


class StorySerializer(serializers.ModelSerializer):
    media = MediaAssetSerializer(many=True, read_only=True)

    class Meta:
        model = DesignerStory
        fields = "__all__"


from .models import InventoryAlert, Promotion, ShipmentTracking

class InventoryAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryAlert
        fields = ['id', 'designer_product', 'threshold', 'notified']

class PromotionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Promotion
        fields = ['id', 'title', 'description', 'discount_percentage', 'active', 'start_date', 'end_date']

class ShipmentTrackingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentTracking
        fields = ['id', 'order', 'tracking_number', 'carrier', 'status', 'last_updated']


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['id', 'image', 'alt_text', 'is_featured']

class DesignerProductSerializer(serializers.ModelSerializer):
    product = ProductSerializer()
    images = ProductImageSerializer(source='product.images', many=True)

    class Meta:
        model = DesignerProduct
        fields = ['id', 'product', 'featured', 'is_active', 'stock', 'images']

class CollectionSerializer(serializers.ModelSerializer):
    products = serializers.SerializerMethodField()

    class Meta:
        model = Collection
        fields = ['id', 'title', 'description', 'cover_image', 'slug', 'is_published', 'products']

    def get_products(self, obj):
        products = obj.designer.products.filter(product__collection=obj.product_set.first())
        return DesignerProductSerializer(products, many=True).data

class DesignerSerializer(serializers.ModelSerializer):
    collections = CollectionSerializer(many=True, read_only=True)
    products = DesignerProductSerializer(many=True, read_only=True)
    user = UserSerializer()

    class Meta:
        model = Designer
        fields = "__all__"

class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = "__all__"

class ShippingOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingOption
        fields = ['id', 'name', 'cost', 'estimated_days', 'is_active']



class OrderSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)
    # price = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields ='__all__'

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['shipping_address'] = AddressSerializer(instance.shipping_address).data
        return data


class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)
    order = OrderSerializer(read_only=True)
    shipment = ShipmentSerializer(read_only=True)

    class Meta:
        model = OrderItem
        fields ='__all__'


    def to_representation(self, instance):
        data = super().to_representation(instance)
        return data


class DesignerOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = DesignerOrder
        fields = ['id','order_item', 'shipping_option', 'status', 'created_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['order_item'] = OrderItemSerializer(instance.order_item).data
        return data

class DesignerAnalyticsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DesignerAnalytics
        fields = ['total_sales', 'total_orders', 'total_products', 'last_updated']


class ProductSizeUpdateSerializer(serializers.Serializer):
    sizes = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True
    )

    def update(self, instance, validated_data):
        size_ids = validated_data.get("sizes", [])
        sizes = Sizes.objects.filter(id__in=size_ids)
        instance.sizes.set(sizes)  # replaces existing sizes safely
        return instance
    
    
class DesignerDashboardSerializer(serializers.Serializer):
    total_sales = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_orders = serializers.IntegerField()
    average_order_value = serializers.DecimalField(max_digits=12, decimal_places=2)
    conversion_rate = serializers.FloatField()
    sales_change_pct = serializers.FloatField()
    order_change_pct = serializers.FloatField()

    sales_over_time = serializers.ListField()
    top_products = serializers.ListField()


class MediaAssetSerializer(serializers.ModelSerializer):
    file = serializers.SerializerMethodField()
    file_name = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = [
            'id',
            'file',
            'file_name',
            'media_type',
            'alt_text',
            'caption',
            'created_at',  # optional
        ]
        read_only_fields = fields  # all read-only from designer perspective

    def get_file(self, obj):
        if obj.file:
            return obj.file.url
        return None

    def get_file_name(self, obj):
        if obj.file:
            return obj.file.name.split('/')[-1]
        return None


class DesignerSerializer(serializers.ModelSerializer):
    lookbook_files = MediaAssetSerializer(many=True, read_only=True)
    # Optional: return absolute URLs for profile/banner if needed
    profile_picture = serializers.ImageField(read_only=True)
    banner_image = serializers.ImageField(read_only=True)
    full_name = serializers.SerializerMethodField()
    email = serializers.CharField(source="user.email")
    class Meta:
        model = Designer
        fields = [
            'id',
            'user',               # optional — usually just ID or username
            'brand_name',
            'story',
            'bio',
            'specialty',
            'country',
            'years_of_experience',
            'website',
            'instagram',
            'full_name',
            'email',
            'profile_picture',
            'banner_image',
            'lookbook_files',     # ← now included!
            'status',
            'is_verified',
            'slug',
            'created_at',
            'status_updated_at',
        ]
        read_only_fields = [
            'id',
            'user',
            'status',
            'is_verified',
            'slug',
            'created_at',
            'status_updated_at',
            'lookbook_files',     # prevent accidental write via this field
        ]

    def get_full_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"



class ReturnRequestSerializer(BaseSerializer):
    product_photos = MediaAssetSerializer(many=True, read_only=True)
    packaging_photo = MediaAssetSerializer(read_only=True)
    unboxing_video = MediaAssetSerializer(read_only=True)

    class Meta(BaseSerializer.Meta):
        model = ReturnRequest
        read_only_fields = ["return_id", "created_at", "resolved_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['customer'] = UserSerializer(instance.order_item.order.customer.user).data
        data['designer'] = DesignerSerializer(instance.order_item.designer.designer_profile).data
        data['order_item'] = OrderItemSerializer(instance.order_item).data

        return data
    
    def update(self, instance, validated_data):
        designer_status = validated_data.get("designer_status")

        if designer_status in ["approved", "rejected"]:
            instance.resolved_at = timezone.now()

        instance = super().update(instance, validated_data)

        
        instance.order_item.designer_status = designer_status
        instance.order_item.save()

        return instance

