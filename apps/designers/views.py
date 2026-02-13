from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.core.exceptions import ObjectDoesNotExist
from apps.core.models import Color, MediaAsset, Product
from apps.core.serializers import ColorSerializer, ProductSerializer
from apps.customers.models import OrderItem, ReturnRequest
from apps.customers.serializers import ReturnRequestSerializer
from django.db.models import Sum
from .models import Designer, DesignerProduct, DesignerOrder, DesignerAnalytics, DesignerStory, StoryView
from .serializers import (
    DesignerSerializer, CollectionSerializer, DesignerProductSerializer, DesignerStorySerializer, MediaAssetSerializer, ProductSizeUpdateSerializer,
    ShippingOptionSerializer, DesignerOrderSerializer, DesignerAnalyticsSerializer, StoryViewSerializer
)
from .models import InventoryAlert, ShipmentTracking, DesignerProduct, DesignerOrder
from .serializers import InventoryAlertSerializer, PromotionSerializer, ShipmentTrackingSerializer
from django.utils import timezone
import csv
from io import StringIO
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.db.models.functions import TruncWeek


class DesignerStoryListView(APIView):
    """List all stories for the authenticated designer or create new story."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        designer = request.user
        stories = designer.stories.all().order_by('-created_at')
        serializer = DesignerStorySerializer(stories, many=True)
        return Response({"status":"success", "message": "Stories retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request):
        data = request.data.copy()
        data['designer'] = request.user.id
        serializer = DesignerStorySerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response({"status":"success", "message": "Story created.", "data": serializer.data}, status=status.HTTP_201_CREATED)
        return Response({"status":"error", "message": "Invalid data.", "data": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

class DesignerStoryDetailView(APIView):
    """Retrieve, update, or delete a story by the designer."""
    permission_classes = [IsAuthenticated]

    def get(self, request, story_id):
        try:
            story = request.user.stories.get(id=story_id)
            serializer = DesignerStorySerializer(story)
            return Response({"status":"success", "message": "Story retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)
        except DesignerStory.DoesNotExist:
            return Response({"status":"error", "message": "Story not found.", "data": None}, status=status.HTTP_404_NOT_FOUND)

    def put(self, request, story_id):
        try:
            story = request.user.stories.get(id=story_id)
            serializer = DesignerStorySerializer(story, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response({"status":"success", "message": "Story updated.", "data": serializer.data}, status=status.HTTP_200_OK)
            return Response({"status":"error", "message": "Invalid data.", "data": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        except DesignerStory.DoesNotExist:
            return Response({"status":"error", "message": "Story not found.", "data": None}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request, story_id):
        try:
            story = request.user.stories.get(id=story_id)
            story.delete()
            return Response({"status":"success", "message": "Story deleted.", "data": None}, status=status.HTTP_200_OK)
        except DesignerStory.DoesNotExist:
            return Response({"status":"error", "message": "Story not found.", "data": None}, status=status.HTTP_404_NOT_FOUND)


# -------------------------------
# Public Stories - For Customers
# -------------------------------
class ActiveStoriesListView(APIView):
    """List all active stories (not expired) for all designers."""
    permission_classes = [AllowAny]

    def get(self, request):
        now = timezone.now()
        stories = DesignerStory.objects.filter(is_active=True, start_time__lte=now, end_time__gte=now).order_by('-created_at')
        serializer = DesignerStorySerializer(stories, many=True)
        return Response({"status":"success", "message": "Active stories retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)

class StoryViewCreateView(APIView):
    """Mark a story as viewed by a customer."""
    permission_classes = [IsAuthenticated]  # customer must be authenticated

    def post(self, request, story_id):
        from customers.models import Customer  # assuming customer model exists
        try:
            story = DesignerStory.objects.get(id=story_id, is_active=True)
            customer = request.user.customer_profile
            view, created = StoryView.objects.get_or_create(story=story, viewer=customer)
            serializer = StoryViewSerializer(view)
            return Response({"status":"success", "message": "Story viewed.", "data": serializer.data}, status=status.HTTP_200_OK)
        except DesignerStory.DoesNotExist:
            return Response({"status":"error", "message": "Story not found.", "data": None}, status=status.HTTP_404_NOT_FOUND)
        
        
# -------------------------------
# Inventory Alerts
# -------------------------------
class InventoryAlertListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        designer = request.user
        alerts = InventoryAlert.objects.filter(designer_product__user=designer)
        serializer = InventoryAlertSerializer(alerts, many=True)
        return Response({"status":"success", "message": "Inventory alerts retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)

# -------------------------------
# Bulk Product Upload
# -------------------------------
class ProductUploadView(APIView):
    """Upload multiple products via CSV."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        product_serializer = ProductSerializer(data = request.data,partial = True)

        if product_serializer.is_valid:
            product_serializer.save(designer = request.user)
            return Response({"status":"success", "message": "product uploaded.", "data": product_serializer.data}, status=status.HTTP_201_CREATED)
        else:
            return Response({"status":"error", "message": "Invalid data.", "data": product_serializer.data}, status=status.HTTP_400_BAD_REQUEST)


class BulkProductUploadView(APIView):
    """Upload multiple products via CSV."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({"status":"error", "message": "CSV file required.", "data": None}, status=status.HTTP_400_BAD_REQUEST)

        csv_file = StringIO(file.read().decode())
        reader = csv.DictReader(csv_file)
        created_products = []

        for row in reader:
            # Expecting CSV columns: title, description, price, stock
            product = Product.objects.create(
                title=row.get('title'),
                description=row.get('description'),
                price=row.get('price')
            )
            designer_product = DesignerProduct.objects.create(
                user=request.user,
                product=product,
                stock=int(row.get('stock', 0))
            )
            created_products.append(designer_product)

        serializer = DesignerProductSerializer(created_products, many=True)
        return Response({"status":"success", "message": "Bulk products uploaded.", "data": serializer.data}, status=status.HTTP_201_CREATED)

# -------------------------------
# Order Status Update
# -------------------------------
class DesignerOrderUpdateStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        status_update = request.data.get('status')
        order_id = request.data.get('order_id')
        if status_update not in ['pending', 'processing', 'shipped', 'delivered', 'cancelled','returned']:
            return Response({"status":"error", "message": "Invalid status.", "data": None}, status=status.HTTP_400_BAD_REQUEST)
        try:
            order = DesignerOrder.objects.get(id=order_id, user=request.user)
            order.order_item.designer_status = status_update
            order.order_item.save()
            serializer = DesignerOrderSerializer(order)
            return Response({"status":"success", "message": "Order status updated.", "data": serializer.data}, status=status.HTTP_200_OK)
        except DesignerOrder.DoesNotExist:
            return Response({"status":"error", "message": "Order not found.", "data": None}, status=status.HTTP_404_NOT_FOUND)

# -------------------------------
# Shipment Tracking
# -------------------------------
class ShipmentTrackingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        designer = request.user
        shipments = ShipmentTracking.objects.filter(order__user=designer)
        serializer = ShipmentTrackingSerializer(shipments, many=True)
        return Response({"status":"success", "message": "Shipments retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, order_id):
        try:
            shipment, _ = ShipmentTracking.objects.get_or_create(order_id=order_id)
            shipment.tracking_number = request.data.get('tracking_number')
            shipment.carrier = request.data.get('carrier')
            shipment.status = request.data.get('status', shipment.status)
            shipment.last_updated = timezone.now()
            shipment.save()
            serializer = ShipmentTrackingSerializer(shipment)
            return Response({"status":"success", "message": "Shipment updated.", "data": serializer.data}, status=status.HTTP_200_OK)
        except DesignerOrder.DoesNotExist:
            return Response({"status":"error", "message": "Order not found.", "data": None}, status=status.HTTP_404_NOT_FOUND)

# -------------------------------
# Promotions / Discounts
# -------------------------------
class PromotionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        designer = request.user
        promotions = designer.promotions.filter(end_date__gte=timezone.now())
        serializer = PromotionSerializer(promotions, many=True)
        return Response({"status":"success", "message": "Promotions retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = PromotionSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response({"status":"success", "message": "Promotion created.", "data": serializer.data}, status=status.HTTP_201_CREATED)
        return Response({"status":"error", "message": "Invalid data.", "data": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
# -------------------------------
# Designer Profile
# -------------------------------
class DesignerProfileView(APIView):
    """Retrieve or update designer profile."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            designer = Designer.objects.get(user = request.user)
            serializer = DesignerSerializer(designer)
            return Response({"status":"success", "message": "Designer profile retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)
        except ObjectDoesNotExist:
            return Response({"status":"success", "message": "Designer profile not found.", "data": False}, status=status.HTTP_404_NOT_FOUND)


    def put(self, request):
        profile, _ = Designer.objects.get_or_create(
            user=request.user
        )
        serializer = DesignerSerializer(
            profile, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(
                {"status": "success", "data": serializer.data},
                status=status.HTTP_200_OK
            )
        return Response(
            {"status": "error", "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
# -------------------------------
# Products Management
# -------------------------------
class DesignerProductView(APIView):
    """List all designer products or add a new product."""
    permission_classes = [IsAuthenticated]
    # parser_classes = (MultiPartParser, FormParser)

    def get(self, request):
        try:
            products = request.user.products.all()
            serializer = ProductSerializer(products, many=True)
            return Response({"status":"success", "message": "Products retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)
        except Exception as e:
            print(e)
            return Response({"status":"error", "message": "Could not fetch products"}, status=status.HTTP_400_BAD_REQUEST)


    def post(self, request):
        try:

            serializer = ProductSerializer(
                data=request.data,
                partial=True,
                context={"request": request}
            )
            if serializer.is_valid():
                product = serializer.save(user=request.user)
                return Response(
                    {"status": "success", "message": "Product uploaded.", "data": ProductSerializer(product).data},
                    status=status.HTTP_201_CREATED
                )
            else:
                return Response(
                    {"status": "error", "message": str(serializer.errors)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return Response(
                {"status": "error", "message": "Invalid data."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def put(self,request):
        try:
            product = Product.objects.get(id=request.data['product_id'],user= request.user)
            product_serializer = ProductSerializer(instance = product, data = request.data, partial = True)
            if product_serializer.is_valid():
                product_serializer.save()
            return Response({"status":"success", "message": "Products retrieved.", "data": product_serializer.data}, status=status.HTTP_200_OK)

        except Exception as e:
            print(e)
            return Response(
                {"status": "error", "message": "Invalid data."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class UpdateProductStatusView(APIView):
    permission_classes = [IsAuthenticated]
    # parser_classes = (MultiPartParser, FormParser)
    def put(self, request):
        try:
            product = Product.objects.get(id=request.data['product_id'],user= request.user)
            product_status = request.data['status']
            if product_status == 'publish':
                product.is_published = True
            elif product_status == 'unpublish':
                product.is_published = False
            product.save()
            return Response(
                {"status": "success", "message": "Product uploaded.", "data": ProductSerializer(product).data},
                status=status.HTTP_201_CREATED
            )
           
        except Exception as e:
            print(e)
            return Response(
                {"status": "error", "message": "Invalid data."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class DesignerUploadProductMediaView(APIView):
    permission_classes = [IsAuthenticated]
    # parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        try:
            product = Product.objects.get(id=request.data['product_id'],user= request.user)
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
                {"status": "success", "message": "Product uploaded.", "data": MediaAssetSerializer(new_assets, many=True).data},
                status=status.HTTP_201_CREATED
            )
           
        except Exception as e:
            print(e)
            return Response(
                {"status": "error", "message": "Invalid data."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def delete(self,request):
        try:
            product = Product.objects.get(id=request.data['product_id'],user= request.user)
            media = MediaAsset.objects.get(id=request.data['media_id'], products = product)
            media.delete()

            return Response(
                {"status": "success", "message": "Media deleted"},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            print(e)
            return Response(
                {"status": "error", "message": "Invalid data."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )



class DesignerProductDetailView(APIView):
    """Retrieve, update, or delete a designer product."""
    permission_classes = [IsAuthenticated]

    def get(self, request, product_id):
        try:
            product = request.user.products.get(id=product_id)
            serializer = DesignerProductSerializer(product)
            return Response({"status":"success", "message": "Product retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)
        except DesignerProduct.DoesNotExist:
            return Response({"status":"error", "message": "Product not found.", "data": None}, status=status.HTTP_404_NOT_FOUND)

# -------------------------------
# Collections Management
# -------------------------------
class DesignerCollectionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        designer = request.user
        collections = designer.collections.all()
        serializer = CollectionSerializer(collections, many=True)
        return Response({"status":"success", "message": "Collections retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)

# -------------------------------
# Shipping Options
# -------------------------------
class DesignerShippingListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        designer = request.user
        shipping_options = designer.shipping_options.all()
        serializer = ShippingOptionSerializer(shipping_options, many=True)
        return Response({"status":"success", "message": "Shipping options retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)

# -------------------------------
# Orders Management
# -------------------------------
class DesignerOrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        limit = int(request.GET.get("limit", 10))

        queryset = (
            DesignerOrder.objects
            .filter(user=request.user)
            .order_by("-created_at")
        )

        paginator = Paginator(queryset, limit)
        page_obj = paginator.get_page(page)

        serializer = DesignerOrderSerializer(page_obj, many=True)

        return Response(
            {
                "status": "success",
                "data": serializer.data,
                "pagination": {
                    "current_page": page_obj.number,
                    "total_pages": paginator.num_pages,
                    "total_items": paginator.count,
                    "has_next": page_obj.has_next(),
                    "has_previous": page_obj.has_previous(),
                },
            },
            status=status.HTTP_200_OK,
        )


# -------------------------------
# Analytics
# -------------------------------
class DesignerAnalyticsView(APIView):
    """Retrieve analytics for designer."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        designer = request.user
        analytics, _ = DesignerAnalytics.objects.get_or_create(user=designer)
        serializer = DesignerAnalyticsSerializer(analytics)
        return Response({"status":"success", "message": "Analytics retrieved.", "data": serializer.data}, status=status.HTTP_200_OK)




class ProductColorView(APIView):
    permission_classes = [IsAuthenticated]


    def get(self, request):
            """
            List colors for a product
            """
            product_id = request.GET.get("product_id")

            if not product_id:
                return Response(
                    {
                        "status": "error",
                        "message": "product query parameter is required",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                product = Product.objects.get(id=product_id, user=request.user)
            except Product.DoesNotExist:
                return Response(
                    {
                        "status": "error",
                        "message": "Product not found or permission denied",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            colors = Color.objects.filter(product=product).order_by("name")

            serializer = ColorSerializer(colors, many=True)

            return Response(
                {
                    "status": "success",
                    "data": serializer.data,
                },
                status=status.HTTP_200_OK,
            )

    def post(self, request):
        """
        Create a color
        """
        serializer = ColorSerializer(data=request.data)
        print(request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "status": "error",
                    "errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        product = serializer.validated_data["product"]

        # Ownership check (VIEW-LEVEL, not serializer validation)
        if product.user != request.user:
            return Response(
                {
                    "status": "error",
                    "message": "Permission denied",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        color = serializer.save()

        return Response(
            {
                "status": "success",
                "message": "Color created successfully",
                "data": {
                    "id": color.id,
                    "name": color.name,
                    "hex_code": color.hex_code,
                },
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request):
        """
        Delete a color
        """
        color_id = request.data["color_id"]

        if not color_id:
            return Response(
                {
                    "status": "error",
                    "error": "color_id query parameter is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            color = Color.objects.select_related("product").get(id=color_id)
        except Color.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "error": "Color not found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if color.product.user != request.user:
            return Response(
                {
                    "status": "error",
                    "error": "Permission denied",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        color.delete()

        return Response(
            {
                "status": "success",
                "message": "Color deleted successfully",
            },
            status=status.HTTP_200_OK,
        )


class UpdateProductSizes(APIView):
    def put(self, request, product_id):
        product = get_object_or_404(Product, id=product_id)

        serializer = ProductSizeUpdateSerializer(
            instance=product,
            data=request.data
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "status": "success",
                "message": "Product sizes updated"
            },
            status=status.HTTP_200_OK
        )





# ---------------- Return Requests ----------------
class ReturnRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        returns = ReturnRequest.objects.filter(order_item__product__user=request.user)
        serializer = ReturnRequestSerializer(returns, many=True)
        return Response({"status":"success", "message": "Return requests retrieved successfully.", "data": serializer.data})

    def put(self, request):
        return_item_id = request.data.get('return_item_id')
        return_status = request.data.get('status')
        try:
            return_item = ReturnRequest.objects.get(id=return_item_id, order_item__product__user=request.user)
        except ReturnRequest.DoesNotExist:
            return Response({"status":"error", "message": "return item not found."}, status=404)
        return_item.designer_status=return_status
        return_item.save()
        serializer = ReturnRequestSerializer(return_item)
        return Response({"status":"success", "message": "Return request submitted successfully.", "data": serializer.data})


class DesignerDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        last_30 = now - timedelta(days=30)
        prev_30 = last_30 - timedelta(days=30)

        base_items = OrderItem.objects.filter(
            product__user=request.user,
            status__in=["processing", "shipped", "delivered"],
        )

        recent_items = base_items.filter(created_at__gte=last_30)
        previous_items = base_items.filter(created_at__gte=prev_30, created_at__lt=last_30)

        total_sales = recent_items.aggregate(total=Sum("sub_total"))["total"] or 0
        previous_sales = previous_items.aggregate(total=Sum("sub_total"))["total"] or 0
        total_orders = recent_items.values("order").distinct().count()
        previous_orders = previous_items.values("order").distinct().count()
        average_order_value = total_sales / total_orders if total_orders else 0
        sales_change_pct = ((total_sales - previous_sales) / previous_sales * 100) if previous_sales else 0
        order_change_pct = ((total_orders - previous_orders) / previous_orders * 100) if previous_orders else 0

        # -----------------------------
        # Sales over time (weekly)
        # -----------------------------
        sales_weekly_qs = (
            recent_items
            .annotate(week=TruncWeek("created_at"))
            .values("week")
            .annotate(total=Sum("sub_total"))
            .order_by("week")
        )

        sales_over_time = [
            {"name": f"Week {i+1}", "amount": float(item["total"])}  # <-- convert Decimal to float
            for i, item in enumerate(sales_weekly_qs)
        ]

        # -----------------------------
        # Top selling products
        # -----------------------------
        top_products_qs = (
            recent_items
            .values("product__name")
            .annotate(units=Sum("quantity"))
            .order_by("-units")[:5]
        )

        top_products = [
            {"name": p["product__name"], "value": float(p["units"])}  # convert to float if needed
            for p in top_products_qs
        ]

        payload = {
            "total_sales": total_sales,
            "total_orders": total_orders,
            "average_order_value": average_order_value,
            "conversion_rate": 0.0,
            "sales_change_pct": round(sales_change_pct, 2),
            "order_change_pct": round(order_change_pct, 2),
            "sales_over_time": sales_over_time,
            "top_products": top_products,
        }

        return Response(payload)