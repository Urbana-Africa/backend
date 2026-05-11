from apps.core.models import Color
# core/serializers.py
from rest_framework import serializers

from apps.authentication.serializers import UserSerializer
from apps.designers.models import Designer
from .models import Brand, ContactMessage, Country, Currency, MediaAsset, Category, Product, Review, ShippingMethod, Sizes, UserSettings, SupportTicket


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
        read_only_fields = ["id", "user", "created_at"]

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


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = "__all__"


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
            "sku",
            "is_published",
            "featured",
            "media",
        )
    
    def create(self, validated_data):
        request = self.context.get("request")
        # ManyToMany fields must be set after creation
        sizes = validated_data.pop("sizes", None)
        product = Product.objects.create(**validated_data)

        if sizes:
            product.sizes.set(sizes)

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

    def update(self, instance, validated_data):
        request = self.context.get("request")
        sizes = validated_data.pop("sizes", None)

        # Update basic scalar fields via DRF's default logic
        instance = super().update(instance, validated_data)

        if sizes is not None:
            instance.sizes.set(sizes)

        # Handle new uploaded images on update
        images = request.FILES.getlist("media[]")
        if images:
            if len(images) > 6:
                raise serializers.ValidationError("You can upload up to 6 images only.")
            for img in images:
                asset = MediaAsset.objects.create(
                    file=img,
                    media_type=MediaAsset.MediaType.IMAGE,
                )
                instance.media.add(asset)

        return instance
    
    def to_representation(self, instance):
        data = super().to_representation(instance)
        try:
            designer = Designer.objects.get(user=instance.user)
            data['designer'] = DesignerSerializer(designer).data
        except Designer.DoesNotExist:
            data['designer'] = None
        data['category'] = CategorySerializer(instance.category).data if instance.category else None
        data['subcategory'] = CategorySerializer(instance.subcategory).data if instance.subcategory else None
        data['brand'] = BrandSerializer(instance.brand).data if instance.brand else None
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


class SupportTicketSerializer(serializers.ModelSerializer):
    # Human-readable display values
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    # Submitter info (resolved at read-time from user or guest fields)
    submitter_name = serializers.SerializerMethodField()
    submitter_email = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            "id",
            "reference",
            "subject",
            "description",
            "category",
            "category_display",
            "priority",
            "priority_display",
            "status",
            "status_display",
            "admin_reply",
            "resolved_at",
            "created_at",
            "updated_at",
            "submitter_name",
            "submitter_email",
            # write-only guest fields (for unauthenticated submissions)
            "guest_name",
            "guest_email",
        ]
        read_only_fields = [
            "id", "reference", "status", "admin_reply",
            "resolved_at", "created_at", "updated_at",
        ]

    def get_submitter_name(self, obj):
        if obj.user:
            return obj.user.get_full_name() or obj.user.username or obj.user.email
        return obj.guest_name

    def get_submitter_email(self, obj):
        if obj.user:
            return obj.user.email
        return obj.guest_email

    def validate(self, attrs):
        request = self.context.get("request")
        # If user is not authenticated, guest_name and guest_email are required
        if not (request and request.user and request.user.is_authenticated):
            if not attrs.get("guest_name"):
                raise serializers.ValidationError({"guest_name": "Name is required."})
            if not attrs.get("guest_email"):
                raise serializers.ValidationError({"guest_email": "Email is required."})
        return attrs
