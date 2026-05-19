from django.db import models
from apps.core.models import Color
# core/serializers.py
from rest_framework import serializers

from apps.authentication.serializers import UserSerializer
from apps.designers.models import Designer
from .models import (
    Brand, ContactMessage, Country, Currency, MediaAsset, Category, Product,
    Review, ShippingMethod, Sizes, UserSettings, SupportTicket, TicketMessage, SmartCollection,
    ProductView, DesignerDailyAnalytics, LoyaltyPoints, LoyaltyBalance,
    SizeRecommendation, UserLookbook, SubscriptionPlan, UserSubscription,
)


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
    country_of_origin = CountrySerializer(read_only=True)
    fit_me_image = serializers.ImageField(required=False, allow_null=True)
    avg_rating = serializers.SerializerMethodField(read_only=True)
    colors_input = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
    )

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
            "is_sustainable",
            "sustainability_notes",
            "availability_type",
            "print_type",
            "occasion",
            "country_of_origin",
            "lead_time_days",
            "rental_price_per_day",
            "popularity_score",
            "fit_stats",
            "size_chart_image",
            "fit_me_image",
            "avg_rating",
            "colors_input",
        )

    def get_avg_rating(self, obj):
        avg = obj.reviews.filter(is_approved=True).aggregate(
            avg=models.Avg("rating")
        )["avg"]
        if avg is None:
            return None
        return round(avg, 1)

    def validate(self, attrs):
        request = self.context.get("request")
        raw = getattr(request, "data", {}) if request else {}

        # Require sizes and colors on create; on update they are optional if not sent
        is_create = self.instance is None
        if is_create:
            sizes = raw.get("sizes")
            colors_input = raw.get("colors_input") or raw.get("colors")
            if not sizes or len(sizes) == 0:
                raise serializers.ValidationError({"sizes": "At least one size is required."})
            if not colors_input or len(colors_input) == 0:
                raise serializers.ValidationError({"colors": "At least one color is required."})

        return super().validate(attrs)

    def _handle_colors(self, product, colors_data):
        if colors_data is None:
            return
        # clear existing and recreate
        product.colors.all().delete()
        for c in colors_data:
            if isinstance(c, dict):
                name = c.get("name", "").strip()
                hex_code = c.get("hex_code", "").strip() or None
            else:
                # fallback if it arrives as a string or ID somehow
                continue
            if name:
                Color.objects.create(product=product, name=name, hex_code=hex_code)

    def create(self, validated_data):
        request = self.context.get("request")
        sizes = validated_data.pop("sizes", None)
        fit_me_image = validated_data.pop("fit_me_image", None)
        colors_data = validated_data.pop("colors_input", None)
        product = Product.objects.create(**validated_data)

        if sizes:
            product.sizes.set(sizes)

        self._handle_colors(product, colors_data)

        # Handle fit_me_image upload
        if fit_me_image:
            product.fit_me_image = fit_me_image
            product.save(update_fields=["fit_me_image"])
        elif "fit_me_image" in request.FILES:
            product.fit_me_image = request.FILES["fit_me_image"]
            product.save(update_fields=["fit_me_image"])

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
        fit_me_image = validated_data.pop("fit_me_image", None)
        colors_data = validated_data.pop("colors_input", None)

        # Detect explicit null from JSON payload for clearing
        raw_data = request.data if request else {}
        fit_me_sent = "fit_me_image" in raw_data

        # Update basic scalar fields via DRF's default logic
        instance = super().update(instance, validated_data)

        if sizes is not None:
            instance.sizes.set(sizes)

        self._handle_colors(instance, colors_data)

        # Handle fit_me_image upload or clearing
        if fit_me_image:
            instance.fit_me_image = fit_me_image
            instance.save(update_fields=["fit_me_image"])
        elif "fit_me_image" in request.FILES:
            instance.fit_me_image = request.FILES["fit_me_image"]
            instance.save(update_fields=["fit_me_image"])
        elif fit_me_sent and not fit_me_image:
            # Explicit null sent — clear the field
            instance.fit_me_image = None
            instance.save(update_fields=["fit_me_image"])

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


class TicketMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()

    class Meta:
        model = TicketMessage
        fields = ["id", "ticket", "sender", "sender_name", "body", "created_at", "is_internal"]
        read_only_fields = ["id", "sender", "created_at"]

    def get_sender_name(self, obj):
        if obj.sender:
            name = obj.sender.get_full_name()
            if name:
                return name
            return obj.sender.email
        return "Unknown"


class SupportTicketSerializer(serializers.ModelSerializer):
    # Human-readable display values
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    # Submitter info (resolved at read-time from user or guest fields)
    submitter_name = serializers.SerializerMethodField()
    submitter_email = serializers.SerializerMethodField()
    messages = TicketMessageSerializer(many=True, read_only=True)
    user_type = serializers.SerializerMethodField()

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
            "user",
            "user_type",
            "messages",
            # write-only guest fields (for unauthenticated submissions)
            "guest_name",
            "guest_email",
        ]
        read_only_fields = [
            "id", "reference", "status", "admin_reply",
            "resolved_at", "created_at", "updated_at", "messages",
        ]

    def get_submitter_name(self, obj):
        if obj.user:
            return obj.user.get_full_name() or obj.user.username or obj.user.email
        return obj.guest_name

    def get_submitter_email(self, obj):
        if obj.user:
            return obj.user.email
        return obj.guest_email

    def get_user_type(self, obj):
        if obj.user:
            try:
                if obj.user.designer_profile:
                    return "designer"
            except Exception:
                pass
            if obj.user.user_type:
                return obj.user.user_type
        return "guest"

    def validate(self, attrs):
        request = self.context.get("request")
        # If user is not authenticated, guest_name and guest_email are required
        if not (request and request.user and request.user.is_authenticated):
            if not attrs.get("guest_name"):
                raise serializers.ValidationError({"guest_name": "Name is required."})
            if not attrs.get("guest_email"):
                raise serializers.ValidationError({"guest_email": "Email is required."})
        return attrs


class SmartCollectionSerializer(serializers.ModelSerializer):
    products = ProductSerializer(many=True, read_only=True)

    class Meta:
        model = SmartCollection
        fields = [
            "id", "title", "slug", "subtitle", "description",
            "collection_type", "products", "cover_image",
            "is_active", "display_order", "created_at",
        ]


class ProductViewSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductView
        fields = ["id", "product", "designer", "session_id", "event_type", "source", "metadata", "created_at"]


class DesignerDailyAnalyticsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DesignerDailyAnalytics
        fields = [
            "id", "designer", "date", "page_views", "unique_visitors",
            "add_to_cart_events", "purchase_events", "revenue", "updated_at",
        ]


class LoyaltyPointsSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyPoints
        fields = ["id", "user", "points", "transaction_type", "description", "order", "created_at"]


class LoyaltyBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyBalance
        fields = ["user", "total_points", "lifetime_earned", "lifetime_redeemed", "tier", "updated_at"]


class SizeRecommendationSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = SizeRecommendation
        fields = [
            "id", "user", "product", "product_name",
            "recommended_size", "confidence_score", "body_measurements", "created_at",
        ]


class UserLookbookSerializer(serializers.ModelSerializer):
    products = ProductSerializer(many=True, read_only=True)

    class Meta:
        model = UserLookbook
        fields = [
            "id", "user", "name", "description", "products",
            "cover_image", "is_public", "created_at", "updated_at",
        ]


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = [
            "id", "name", "slug", "tier", "price_monthly", "price_yearly",
            "ai_calls_daily", "has_ai_outfit_builder", "has_ai_personalized_search",
            "has_ai_fitme", "has_gift_concierge", "has_event_styling",
            "has_priority_support", "description", "features",
        ]


class UserSubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)

    class Meta:
        model = UserSubscription
        fields = [
            "id", "plan", "status", "billing_cycle", "started_at",
            "expires_at", "ai_calls_used_today", "ai_calls_reset_at", "auto_renew",
        ]
