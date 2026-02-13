from rest_framework import serializers

from apps.authentication.serializers import UserSerializer
from apps.core.models import Sizes
from apps.customers.models import OrderItem
from apps.customers.serializers import CustomerSerializer
from .models import (
    Designer, Collection, DesignerProduct, DesignerStory, ProductImage,
    ShippingOption, DesignerOrder, DesignerAnalytics, StoryView
)
from apps.core.serializers import ProductSerializer
from .models import DesignerStory, MediaAsset


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

        
class MediaAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaAsset
        fields = "__all__"

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

class ShippingOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingOption
        fields = ['id', 'name', 'cost', 'estimated_days', 'is_active']


class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)
    # price = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields ='__all__'



class DesignerOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = DesignerOrder
        fields = ['id','order_item', 'shipping_option', 'status', 'created_at']

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['order_item'] = OrderItemSerializer(instance.order_item).data
        return representation

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
