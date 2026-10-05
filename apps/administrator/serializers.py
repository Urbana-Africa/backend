from rest_framework import serializers
from django.utils import timezone

from apps.authentication.serializers import UserSerializer
from apps.core.models import (
    Product, Category, Brand, Currency, Sizes,
    MediaAsset, Review, ShippingMethod,
    Country, SmartCollection
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
    ReturnRequest, OrderTracking, Dispute
)

from apps.pay.models import Withdrawal




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
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    avatar = serializers.SerializerMethodField()
    country = serializers.SerializerMethodField()
    order_count = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta(AdminBaseSerializer.Meta):
        model = Customer
        fields = [
            "id", "first_name", "last_name", "email", "phone", "avatar",
            "country", "order_count", "status", "created_at",
        ]

    def get_avatar(self, obj):
        if obj.avatar:
            return obj.avatar.url
        if obj.user.profile_picture:
            return obj.user.profile_picture.url
        name_or_email = obj.user.first_name or obj.user.email
        return generate_email_avatar(name_or_email)

    def get_country(self, obj):
        default = obj.addresses.filter(is_default=True).first()
        if default:
            return default.country
        first = obj.addresses.first()
        return first.country if first else None

    def get_order_count(self, obj):
        return obj.orders.count()

    def get_status(self, obj):
        return "active" if obj.user.is_active else "inactive"


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
    designer = serializers.SerializerMethodField()
    moderation_gate = serializers.SerializerMethodField()

    class Meta(AdminBaseSerializer.Meta):
        model = Product
        read_only_fields = ["slug", "sku", "created_at"]

    def get_moderation_gate(self, obj):
        """CAT-01 — the live moderation checklist shown on the product."""
        from .gates import evaluate_product_moderation
        return evaluate_product_moderation(obj)

    def get_designer(self, obj):
        try:
            profile = obj.user.designer_profile
            return {
                "id": profile.id,
                "brand_name": profile.brand_name,
                "slug": profile.slug,
                "profile_picture": profile.profile_picture.url if profile.profile_picture else None,
                "status": profile.status,
                "email": obj.user.email,
                "full_name": f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip(),
            }
        except Exception:
            return None


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
    packaging_images = MediaAssetSerializer(many=True, read_only=True)
    packaging_video = MediaAssetSerializer(read_only=True)

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

class AdminDisputeSerializer(AdminBaseSerializer):
    customer_evidence = MediaAssetSerializer(many=True, read_only=True)
    designer_evidence = MediaAssetSerializer(many=True, read_only=True)

    class Meta(AdminBaseSerializer.Meta):
        model = Dispute
        read_only_fields = ["dispute_id", "created_at", "resolved_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['customer'] = UserSerializer(instance.return_request.order_item.order.customer.user).data
        data['designer'] = AdminDesignerSerializer(instance.return_request.order_item.designer.designer_profile).data
        return data


class AdminReturnRequestSerializer(AdminBaseSerializer):
    product_photos = MediaAssetSerializer(many=True, read_only=True)
    packaging_photo = MediaAssetSerializer(read_only=True)
    unboxing_video = MediaAssetSerializer(read_only=True)

    class Meta(AdminBaseSerializer.Meta):
        model = ReturnRequest
        read_only_fields = ["return_id", "created_at", "resolved_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['customer'] = UserSerializer(instance.order_item.order.customer.user).data
        data['designer'] = AdminDesignerSerializer(instance.order_item.designer.designer_profile).data
        data['order_item'] = AdminOrderItemSerializer(instance.order_item).data
        try:
            data['dispute'] = AdminDisputeSerializer(instance.dispute).data
        except:
            data['dispute'] = None

        return data
    
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
    email = serializers.CharField(source="user.email", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    lookbook_files = MediaAssetSerializer(many=True, read_only=True)
    products_count = serializers.SerializerMethodField()
    approval_gate = serializers.SerializerMethodField()

    class Meta:
        model = Designer
        fields = "__all__"

    def get_approval_gate(self, obj):
        """DES-02 — onboarding completeness checklist shown on the record."""
        from .gates import evaluate_designer_readiness
        return evaluate_designer_readiness(obj)

    def get_full_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"

    def get_products_count(self, obj):
        # Count products the designer actually uploaded (via Product.user),
        # not just DesignerProduct join records. Products uploaded before the
        # join-record fix have no DesignerProduct link and would be invisible
        # to obj.products.count().
        from apps.core.models import Product as ProductModel
        return ProductModel.objects.filter(user=obj.user).count()

class AdminDesignerProductSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = DesignerProduct


class AdminCollectionSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Collection


class AdminSmartCollectionSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = SmartCollection
        fields = [
            "id", "name", "slug", "collection_type", "description",
            "is_active", "sort_order", "criteria", "products", "created_at", "updated_at"
        ]


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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        try:
            data['product_name'] = instance.designer_product.product.name
            data['stock'] = instance.designer_product.stock
            data['designer_brand'] = instance.designer_product.designer.brand_name
            data['designer_email'] = instance.designer_product.designer.user.email
        except:
            data['product_name'] = None
            data['stock'] = None
            data['designer_brand'] = None
            data['designer_email'] = None
        return data


class AdminPromotionSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Promotion

    def to_representation(self, instance):
        data = super().to_representation(instance)
        try:
            data['designer'] = {
                "id": instance.designer.id,
                "brand_name": instance.designer.brand_name,
                "email": instance.designer.user.email,
            }
        except:
            data['designer'] = None
        return data

class AdminWithdrawalSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Withdrawal

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['designer'] = {
            "id": instance.user.id,
            "email": instance.user.email,
            "full_name": f"{instance.user.first_name} {instance.user.last_name}",
        }
        try:
            data['brand_name'] = instance.user.designer_profile.brand_name
        except:
            data['brand_name'] = "Unknown"
        return data


# =====================================================
# NEWSLETTER MANAGEMENT
# =====================================================

from apps.newsletter.models import Newsletter, NewsletterSubscriber

class AdminNewsletterSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = Newsletter
        read_only_fields = ["created_at", "updated_at", "sent_at"]


class AdminNewsletterSubscriberSerializer(AdminBaseSerializer):
    class Meta(AdminBaseSerializer.Meta):
        model = NewsletterSubscriber
        read_only_fields = ["subscribed_at", "unsubscribed_at"]

# =====================================================
# GOVERNANCE (Phase 0 — audit & data health)
# =====================================================

from .models import (
    AuditEvent, DataQualityCheck, ReconciliationRun, ReconciliationException,
    WorkItem, ApprovalRequest, Case, PolicyVersion, PrivacyRequest,
    Incident, CapabilityGrant,
)


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = '__all__'


class DataQualityCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataQualityCheck
        fields = '__all__'


class ReconciliationExceptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReconciliationException
        fields = '__all__'
        read_only_fields = (
            'id', 'run', 'entity_type', 'entity_id', 'issue', 'detail',
            'resolved_by', 'resolved_at', 'created_at',
        )


class ReconciliationRunSerializer(serializers.ModelSerializer):
    exceptions = ReconciliationExceptionSerializer(many=True, read_only=True)

    class Meta:
        model = ReconciliationRun
        fields = '__all__'


class ApprovalRequestSerializer(serializers.ModelSerializer):
    requested_by_email = serializers.CharField(
        source='requested_by.email', read_only=True, default=''
    )
    decided_by_email = serializers.CharField(
        source='decided_by.email', read_only=True, default=''
    )

    class Meta:
        model = ApprovalRequest
        fields = '__all__'
        read_only_fields = (
            'id', 'action', 'entity_type', 'entity_id', 'payload',
            'requested_by', 'decided_by', 'decided_at', 'created_at',
        )


class WorkItemSerializer(serializers.ModelSerializer):
    assigned_to_email = serializers.CharField(
        source='assigned_to.email', read_only=True, default=''
    )
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = WorkItem
        fields = '__all__'
        read_only_fields = (
            'id', 'queue', 'entity_type', 'entity_id', 'title', 'detail',
            'source_status', 'resolved_by', 'resolved_at',
            'created_at', 'updated_at',
        )


class CaseSerializer(serializers.ModelSerializer):
    owner_email = serializers.CharField(
        source='owner.email', read_only=True, default='')
    allowed_transitions = serializers.SerializerMethodField()

    class Meta:
        model = Case
        fields = '__all__'
        read_only_fields = ('id', 'case_ref', 'created_at', 'updated_at',
                            'resolved_at')

    def get_allowed_transitions(self, obj):
        from .cases import TICKET_TRANSITIONS
        return sorted(TICKET_TRANSITIONS.get(obj.status, set()))


class PolicyVersionSerializer(serializers.ModelSerializer):
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True, default='')
    approved_by_email = serializers.CharField(
        source='approved_by.email', read_only=True, default='')

    class Meta:
        model = PolicyVersion
        fields = '__all__'
        read_only_fields = ('id', 'version', 'created_by', 'approved_by',
                            'created_at')


class PrivacyRequestSerializer(serializers.ModelSerializer):
    handler_email = serializers.CharField(
        source='handler.email', read_only=True, default='')
    subject_user_email = serializers.CharField(
        source='subject_user.email', read_only=True, default='')
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = PrivacyRequest
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at', 'verified_at',
                            'completed_at')

    def get_is_overdue(self, obj):
        return bool(
            obj.due_at and obj.due_at < timezone.now()
            and obj.status not in ('completed', 'rejected'))


class IncidentSerializer(serializers.ModelSerializer):
    owner_email = serializers.CharField(
        source='owner.email', read_only=True, default='')

    class Meta:
        model = Incident
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at', 'resolved_at')


class CapabilityGrantSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(
        source='user.email', read_only=True, default='')
    granted_by_email = serializers.CharField(
        source='granted_by.email', read_only=True, default='')
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = CapabilityGrant
        fields = '__all__'
        read_only_fields = ('id', 'granted_by', 'created_at', 'revoked_at')
