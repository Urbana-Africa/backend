from rest_framework import viewsets, status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.pagination import PageNumberPagination

from apps.core.models import *
from apps.customers.models import *
from apps.designers.models import *
from .serializers import *


class AdminPagination(PageNumberPagination):
    page_size = 12
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({
            "count": self.page.paginator.count,
            "total_pages": self.page.paginator.num_pages,
            "current_page": self.page.number,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "results": data,
        })

# =====================================================
# BASE ADMIN VIEWSET
# =====================================================

class AdminBaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    pagination_class = AdminPagination

    filter_backends = [
        DjangoFilterBackend,
        SearchFilter,
        OrderingFilter,
    ]

# =====================================================
# CUSTOMER MANAGEMENT
# =====================================================

class AdminCustomerViewSet(AdminBaseViewSet):
    queryset = Customer.objects.select_related("user")
    serializer_class = AdminCustomerSerializer
    search_fields = ["user__email", "user__username"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]


class AdminAddressViewSet(AdminBaseViewSet):
    queryset = Address.objects.select_related("customer")
    serializer_class = AdminAddressSerializer


class AdminWishlistViewSet(AdminBaseViewSet):
    queryset = Wishlist.objects.all()
    serializer_class = AdminWishlistSerializer


class AdminCartItemViewSet(AdminBaseViewSet):
    queryset = CartItem.objects.all()
    serializer_class = AdminCartItemSerializer

# =====================================================
# PRODUCT & CATALOG
# =====================================================

class AdminProductViewSet(AdminBaseViewSet):
    queryset = Product.objects.select_related(
        "user", "category", "brand", "currency"
    ).prefetch_related("sizes", "media")

    serializer_class = AdminProductSerializer

    filterset_fields = [
        "is_published",
        "is_admin_published",
        "is_active",
        "featured",
    ]

    search_fields = ["name", "sku"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    @action(detail=True, methods=["patch"], url_path="unpublish")
    def unpublish(self, request, pk=None):
        product = self.get_object()

        reasons = request.data.get("unpublish_reasons", [])
        comment = request.data.get("comment", "")

        if not isinstance(reasons, list) or not reasons:
            return Response(
                {"message": "At least one unpublish reason is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        product.unpublish_reasons = reasons
        product.unpublish_comment = comment
        product.is_admin_published = False
        product.save()

        serializer = self.get_serializer(product)
        return Response({
            "status": "success",
            "data": serializer.data
        })


class AdminCategoryViewSet(AdminBaseViewSet):
    queryset = Category.objects.all()
    serializer_class = AdminCategorySerializer


class AdminBrandViewSet(AdminBaseViewSet):
    queryset = Brand.objects.all()
    serializer_class = AdminBrandSerializer


class AdminCurrencyViewSet(AdminBaseViewSet):
    queryset = Currency.objects.all()
    serializer_class = AdminCurrencySerializer


class AdminSizesViewSet(AdminBaseViewSet):
    queryset = Sizes.objects.all()
    serializer_class = AdminSizesSerializer


class AdminMediaAssetViewSet(AdminBaseViewSet):
    queryset = MediaAsset.objects.all()
    serializer_class = AdminMediaAssetSerializer


class AdminReviewViewSet(AdminBaseViewSet):
    queryset = Review.objects.select_related("product", "customer")
    serializer_class = AdminReviewSerializer


class AdminShippingMethodViewSet(AdminBaseViewSet):
    queryset = ShippingMethod.objects.all()
    serializer_class = AdminShippingMethodSerializer


class AdminCountryViewSet(AdminBaseViewSet):
    queryset = Country.objects.all()
    serializer_class = AdminCountrySerializer


# =====================================================
# ORDER MANAGEMENT
# =====================================================
class AdminOrderViewSet(AdminBaseViewSet):
    queryset = Order.objects.select_related("customer", "invoice")
    serializer_class = AdminOrderSerializer
    filterset_fields = ["status"]
    search_fields = ["order_id"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]


class AdminOrderItemViewSet(AdminBaseViewSet):
    queryset = OrderItem.objects.select_related("order", "product")
    serializer_class = AdminOrderItemSerializer


class AdminOrderTrackingViewSet(AdminBaseViewSet):
    queryset = OrderTracking.objects.select_related("order")
    serializer_class = AdminOrderTrackingSerializer

# =====================================================
# RETURN MANAGEMENT
# =====================================================

class AdminReturnRequestViewSet(AdminBaseViewSet):
    queryset = ReturnRequest.objects.select_related(
        "order_item",
        "order_item__order"
    )

    serializer_class = AdminReturnRequestSerializer

    filterset_fields = ["status", "admin_status"]
    search_fields = ["return_id"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

# =====================================================
# DESIGNER MANAGEMENT
# =====================================================



class AdminDesignerViewSet(AdminBaseViewSet):
    queryset = Designer.objects.select_related("user")
    serializer_class = AdminDesignerSerializer

    filterset_fields = {
        "status": ["exact"],
        "is_verified": ["exact"],
        "country": ["exact"],
        "created_at": ["gte", "lte"],
    }

    search_fields = [
        "brand_name",
        "specialty",
        "country",
        "user__first_name",
        "user__last_name",
        "user__email",
    ]

    ordering_fields = ["created_at", "brand_name"]
    ordering = ["-created_at"]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        stats = {
            "total": queryset.count(),
            "verified": queryset.filter(is_verified=True).count(),
            "unverified": queryset.filter(is_verified=False).count(),
            "approved": queryset.filter(status=Designer.Status.APPROVED).count(),
            "blocked": queryset.filter(status=Designer.Status.BLOCKED).count(),
            "rejected": queryset.filter(status=Designer.Status.REJECTED).count(),
            "pending": queryset.filter(status=Designer.Status.PENDING).count(),
        }

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["stats"] = stats
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "results": serializer.data,
            "stats": stats
        })

class AdminDesignerProductViewSet(AdminBaseViewSet):
    queryset = DesignerProduct.objects.select_related("designer", "product")
    serializer_class = AdminDesignerProductSerializer


class AdminCollectionViewSet(AdminBaseViewSet):
    queryset = Collection.objects.select_related("designer")
    serializer_class = AdminCollectionSerializer


class AdminDesignerAnalyticsViewSet(AdminBaseViewSet):
    queryset = DesignerAnalytics.objects.select_related("designer")
    serializer_class = AdminDesignerAnalyticsSerializer


class AdminShippingOptionViewSet(AdminBaseViewSet):
    queryset = ShippingOption.objects.select_related("designer")
    serializer_class = AdminShippingOptionSerializer


class AdminDesignerOrderViewSet(AdminBaseViewSet):
    queryset = DesignerOrder.objects.select_related("user", "order_item")
    serializer_class = AdminDesignerOrderSerializer


class AdminShipmentTrackingViewSet(AdminBaseViewSet):
    queryset = ShipmentTracking.objects.select_related("order")
    serializer_class = AdminShipmentTrackingSerializer


class AdminInventoryAlertViewSet(AdminBaseViewSet):
    queryset = InventoryAlert.objects.select_related("designer_product")
    serializer_class = AdminInventoryAlertSerializer


class AdminPromotionViewSet(AdminBaseViewSet):
    queryset = Promotion.objects.select_related("designer")
    serializer_class = AdminPromotionSerializer



class AdminUploadProductMediaView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            product = Product.objects.get(
                id=request.data["product_id"],
                user=request.user
            )

            images = request.FILES.getlist("media[]")
            new_assets = []

            for img in images:
                asset = MediaAsset.objects.create(
                    file=img,
                    media_type=MediaAsset.MediaType.IMAGE,
                )
                product.media.add(asset)
                new_assets.append(asset)

            return Response(
                {
                    "status": "success",
                    "message": "Product uploaded.",
                    "data": MediaAssetSerializer(new_assets, many=True).data
                },
                status=status.HTTP_201_CREATED
            )

        except Exception:
            return Response(
                {"status": "error", "message": "Invalid data."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )