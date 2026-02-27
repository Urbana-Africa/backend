from apps.core.models import Color
# core/serializers.py
from rest_framework import serializers

from apps.authentication.serializers import UserSerializer
from apps.designers.models import Designer
from .models import ContactMessage, Country, Currency, MediaAsset, Category, Product, Review, ShippingMethod, Sizes, UserSettings


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = "__all__"


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = "__all__"


class ShippingMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingMethod
        fields = ['id', 'name', 'price', 'estimated_days', 'is_active']



class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = "__all__"


class ReviewSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.full_name', read_only=True)

    class Meta:
        model = Review
        fields = "__all__"


class MediaAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaAsset
        fields = ["id", "file", "media_type", "alt_text", "caption"]


class DesignerSerializer(serializers.ModelSerializer):
    user = UserSerializer()

    class Meta:
        model = Designer
        fields = "__all__"


class SizesSerializer(serializers.ModelSerializer):

    class Meta:
        model = Sizes
        fields = "__all__"



class ColorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Color
        fields = ("id", "product", "name", "hex_code")


class ProductSerializer(serializers.ModelSerializer):
    media = MediaAssetSerializer(many=True, read_only=True)
    colors = ColorSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "description",
            "price",
            "discount",
            "currency",
            "category",
            "subcategory",
            "brand",
            "material",
            "colors",
            "sizes",
            "origin",
            "stock",
            "stock",
            "sku",
            "is_published",
            "featured",
            "media",
        )
    
    def create(self, validated_data):
        request = self.context.get("request")
        product = Product.objects.create(**validated_data)

        # Handle multiple uploaded images
        images = request.FILES.getlist("media[]")

        if len(images) > 6:
            raise serializers.ValidationError("You can upload up to 6 images only.")

        for img in images:
            asset = MediaAsset.objects.create(
                file=img,
                media_type=MediaAsset.MediaType.IMAGE,
            )
            product.media.add(asset)

        return product
    
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['designer'] = DesignerSerializer(Designer.objects.get(user=instance.user)).data
        data['sizes'] = SizesSerializer(instance.sizes, many=True).data
        data['colors'] = ColorSerializer(instance.colors, many=True).data
        return data
    


class UserSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSettings
        exclude = ("id", "user")


class ContactMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactMessage
        fields = ["id", "name", "email", "message"]