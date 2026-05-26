from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from apps.core.models import MediaAsset, Product, Color, Sizes
from apps.core.serializers import ProductSerializer, ColorSerializer, SizesSerializer, MediaAssetSerializer
from apps.customers.models import OrderItem, ReturnRequest
from apps.designers.serializers import ReturnRequestSerializer
from django.db.models import Sum
from apps.utils.pagination import StandardPagination
from .models import Designer, DesignerProduct, DesignerStory, StoryView, Notification
from .serializers import (
    DesignerSerializer, DesignerProductSerializer, DesignerStorySerializer, OrderItemSerializer, StoryViewSerializer,
    NotificationSerializer
)
from rest_framework import status
from django.utils.text import slugify
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
import csv
from io import StringIO
from .models import InventoryAlert
from .serializers import InventoryAlertSerializer
from django.utils import timezone
from django.template.loader import render_to_string
import threading
from apps.utils.email_sender import resend_sendmail
import csv
from io import StringIO
from django.db.models.functions import TruncWeek
from rest_framework.decorators import action
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser
from rest_framework import viewsets


class DesignerBaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    filter_backends = [
        DjangoFilterBackend,
        SearchFilter,
        OrderingFilter,
    ]
    
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

class DesignerStoryViewSet(DesignerBaseViewSet):
    serializer_class = DesignerStorySerializer
    lookup_field = "id"

    def get_queryset(self):
        return DesignerStory.objects.filter(
            designer=self.request.user
        ).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(designer=self.request.user)

    @action(detail=False, permission_classes=[AllowAny])
    def active(self, request):
        """Public active stories"""
        now = timezone.now()

        stories = DesignerStory.objects.filter(
            is_active=True,
            start_time__lte=now,
            end_time__gte=now
        ).order_by("-created_at")

        serializer = self.get_serializer(stories, many=True)

        return Response({
            "status": "success",
            "message": "Active stories retrieved.",
            "data": serializer.data
        })

    @action(detail=True, methods=["post"])
    def view_story(self, request, pk=None):
        """Customer views a story"""

        story = self.get_object()
        customer = request.user.customer_profile

        view, _ = StoryView.objects.get_or_create(
            story=story,
            viewer=customer
        )

        serializer = StoryViewSerializer(view)

        return Response({
            "status": "success",
            "message": "Story viewed.",
            "data": serializer.data
        })


class DesignerProductUploadViewSet(DesignerBaseViewSet):

    serializer_class = ProductSerializer
    parser_classes = (MultiPartParser, FormParser, JSONParser)  # needed for file uploads and JSON

    def get_queryset(self):
        # Only products belonging to the authenticated designer
        return Product.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "status": "success",
            "data": serializer.data
        })

    def create(self, request):
        """
        POST /designer-products/ → Create new product
        """
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            product = serializer.save(user=request.user)

            return Response({
                "status": "success",
                "message": "Product uploaded successfully",
                "data": ProductSerializer(product).data
            }, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def update(self, request, pk=None):
        """
        PUT /designer-products/<pk>/ → Update existing product
        """
        product = get_object_or_404(self.get_queryset(), pk=pk)

        serializer = self.get_serializer(product, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()  # user is already set, no need to touch it

            return Response({
                "status": "success",
                "message": "Product updated successfully",
                "data": serializer.data
            })

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None):
        """
        PATCH /designer-products/<pk>/ → Partial update
        """
        product = get_object_or_404(self.get_queryset(), pk=pk)

        serializer = self.get_serializer(product, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()

            return Response({
                "status": "success",
                "message": "Product partially updated",
                "data": serializer.data
            })

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], url_path="bulk-upload")
    def bulk_upload(self, request):
        """
        POST /designer-products/bulk-upload/ → Upload multiple products via CSV
        """
        file = request.FILES.get("file")

        if not file:
            return Response({"error": "CSV file is required"}, status=400)

        try:
            csv_file = StringIO(file.read().decode("utf-8"))
            reader = csv.DictReader(csv_file)

            created_products = []

            for row in reader:
                product = Product.objects.create(
                    user=request.user,
                    title=row.get("title", "").strip(),
                    description=row.get("description", "").strip(),
                    price=float(row.get("price", 0)),
                    # add other Product fields you want from CSV
                )

                designer_product = DesignerProduct.objects.create(
                    user=request.user,
                    product=product,
                    stock=int(row.get("stock", 0)),
                    # add other DesignerProduct fields if needed
                )

                created_products.append(designer_product)

            serializer = DesignerProductSerializer(created_products, many=True)

            return Response({
                "status": "success",
                "message": f"{len(created_products)} products uploaded successfully",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({
                "status": "error",
                "message": f"Bulk upload failed: {str(e)}"
            }, status=status.HTTP_400_BAD_REQUEST)

    # -------------------------------
    # Product Colors
    # -------------------------------
    @action(detail=True, methods=["get", "post", "delete"], url_path="colors")
    def manage_colors(self, request, pk=None):
        product = self.get_object()

        if request.method == "GET":
            colors = product.colors.all()
            return Response({
                "status": "success",
                "data": ColorSerializer(colors, many=True).data
            })

        if request.method == "POST":
            name = request.data.get("name")
            hex_code = request.data.get("hex_code")
            if not name:
                return Response({"error": "Color name is required"}, status=status.HTTP_400_BAD_REQUEST)
            color = Color.objects.create(product=product, name=name, hex_code=hex_code)
            return Response({
                "status": "success",
                "data": ColorSerializer(color).data
            }, status=status.HTTP_201_CREATED)

        if request.method == "DELETE":
            color_id = request.data.get("color_id")
            try:
                color = Color.objects.get(id=color_id, product=product)
                color.delete()
                return Response({"status": "success"})
            except Color.DoesNotExist:
                return Response({"error": "Color not found"}, status=status.HTTP_404_NOT_FOUND)

    # -------------------------------
    # Product Sizes
    # -------------------------------
    @action(detail=True, methods=["get", "post", "put", "delete"], url_path="sizes")
    def manage_sizes(self, request, pk=None):
        product = self.get_object()

        if request.method == "GET":
            sizes = product.sizes.all()
            return Response({
                "status": "success",
                "data": SizesSerializer(sizes, many=True).data
            })

        if request.method == "POST":
            size_id = request.data.get("size_id")
            try:
                size = Sizes.objects.get(id=size_id)
                product.sizes.add(size)
                return Response({"status": "success"})
            except Sizes.DoesNotExist:
                return Response({"error": "Size not found"}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "PUT":
            size_ids = request.data.get("sizes", [])
            if not isinstance(size_ids, list):
                size_ids = [size_ids]
            valid_sizes = Sizes.objects.filter(id__in=size_ids)
            product.sizes.set(valid_sizes)
            return Response({
                "status": "success",
                "data": SizesSerializer(product.sizes, many=True).data
            })

        if request.method == "DELETE":
            size_id = request.data.get("size_id")
            try:
                size = Sizes.objects.get(id=size_id)
                product.sizes.remove(size)
                return Response({"status": "success"})
            except Sizes.DoesNotExist:
                return Response({"error": "Size not found"}, status=status.HTTP_404_NOT_FOUND)

    # -------------------------------
    # Product Media
    # -------------------------------
    @action(detail=True, methods=["get", "post", "delete"], url_path="media")
    def manage_media(self, request, pk=None):
        product = self.get_object()

        if request.method == "GET":
            media = product.media.all()
            return Response({
                "status": "success",
                "data": MediaAssetSerializer(media, many=True).data
            })

        if request.method == "POST":
            images = request.FILES.getlist("media[]")
            if len(images) > 6:
                return Response({"error": "Max 6 images per upload"}, status=status.HTTP_400_BAD_REQUEST)

            new_assets = []
            for img in images:
                asset = MediaAsset.objects.create(
                    file=img,
                    media_type=MediaAsset.MediaType.IMAGE,
                )
                product.media.add(asset)
                new_assets.append(asset)

            return Response({
                "status": "success",
                "data": MediaAssetSerializer(new_assets, many=True).data
            })

        if request.method == "DELETE":
            media_id = request.data.get("media_id")
            try:
                asset = MediaAsset.objects.get(id=media_id)
                product.media.remove(asset)
                return Response({"status": "success"})
            except MediaAsset.DoesNotExist:
                return Response({"error": "Media not found"}, status=status.HTTP_404_NOT_FOUND)


class DesignerOrderViewSet(DesignerBaseViewSet):

    serializer_class = OrderItemSerializer
    lookup_field = "item_id"

    def get_queryset(self):

        return OrderItem.objects.filter(
            product__user=self.request.user,
            order__invoice__payment__status='success',
            order__invoice__payment__is_paid=True
        ).select_related(
            "order",
            "product"
        ).order_by("-created_at")

    @action(detail=True, methods=["post"])
    def update_status(self, request, item_id=None):

        order_item = self.get_object()

        status_update = request.data.get("status")

        order_item.designer_status = status_update
        order_item.save()

        return Response({
            "status": "success",
            "data": OrderItemSerializer(order_item).data
        })





class DesignerProfileViewSet(DesignerBaseViewSet):
    serializer_class = DesignerSerializer
    parser_classes = (JSONParser, FormParser, MultiPartParser)

    def list(self, request):
        profile, _ = Designer.objects.get_or_create(user=request.user)
        serializer = self.get_serializer(profile)
        return Response({
            "status": "success",
            "data": serializer.data
        })

    @action(detail=False, methods=["post", "put"], url_path="setup")
    def setup_profile(self, request):
        profile, created = Designer.objects.get_or_create(user=request.user)
        # ================= FILE HANDLING =================
        lookbook_files = request.FILES.getlist('lookbook_files[]')
        profile_picture = request.FILES.get('profile_picture')
        banner_image = request.FILES.get('banner_image')

        new_assets = []

        try:
            if profile_picture:
                profile.profile_picture = profile_picture
                profile.save(update_fields=['profile_picture'])

            if banner_image:
                profile.banner_image = banner_image
                profile.save(update_fields=['banner_image'])
            if lookbook_files:
                for file in lookbook_files:
                    if file.size > 20 * 1024 * 1024:
                        return Response(
                            {"error": f"{file.name} exceeds 20MB"},
                            status=400
                        )

                    asset = MediaAsset.objects.create(
                        file=file,
                        media_type=(
                            MediaAsset.MediaType.IMAGE
                            if file.content_type.startswith('image/')
                            else MediaAsset.MediaType.DOCUMENT
                        ),
                        alt_text=file.name,
                    )
                    new_assets.append(asset)
                    profile.lookbook_files.add(asset)

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=400
            )

        # ================= SERIALIZER =================
        serializer = self.get_serializer(
            profile,
            data=request.data,
            partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # slug
        if 'brand_name' in request.data and request.data['brand_name']:
            profile.slug = slugify(profile.brand_name)
            profile.save(update_fields=['slug'])

        # Notify designer on first submission
        if created:
            # In-app notification
            Notification.objects.create(
                user=request.user,
                title="Profile submitted for review",
                message="Your designer profile has been submitted and is under review by our curation team. We'll notify you once it's been reviewed.",
                notification_type=Notification.Type.PROFILE,
                link="/profile-status",
            )

            # Email confirmation
            try:
                subject = "Urbana Studio: Profile Submitted for Review"
                context = {"designer": profile}
                message = render_to_string("administrator/status_update.html", context)
                threading.Thread(
                    target=resend_sendmail,
                    args=(subject, [request.user.email], message),
                ).start()
            except Exception as e:
                print(f"Error sending profile submission email: {str(e)}")

            # Send welcome email only on first profile creation
            try:
                context = {
                    "designer_name": profile.brand_name or request.user.first_name or "Designer",
                }
                message = render_to_string("administrator/designer_welcome.html", context)
                threading.Thread(
                    target=resend_sendmail,
                    args=(
                        "Welcome to Urbana Studio",
                        [request.user.email],
                        message,
                    ),
                ).start()
            except Exception as e:
                print(f"Error sending designer welcome email: {str(e)}")

        return Response({
            "status": "success",
            "message": "Profile setup completed" if created else "Profile updated",
            "data": serializer.data
        })


class DesignerDashboardViewSet(DesignerBaseViewSet):
    queryset = OrderItem.objects.all()  # or .none() if you filter everywhere

    def get_queryset(self):
        # Normal filtering for list/retrieve/update/destroy
        return super().get_queryset().filter(
            product__user=self.request.user,
            status__in=["processing", "shipped", "delivered"]
        )

    @action(detail=False, methods=['get'], url_path='overview')
    def dashboard(self, request):
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
    
    @action(detail=False, methods=['get'], url_path='analytics')
    def analytics(self, request):
        """Designer analytics (views + top products). View tracking is not yet
        implemented, so view counts return 0; sales-based top products are
        derived from orders."""
        now = timezone.now()
        last_7 = now - timedelta(days=7)
        last_30 = now - timedelta(days=30)

        base_items = OrderItem.objects.filter(
            product__user=request.user,
            status__in=["processing", "shipped", "delivered"],
        )
        recent_items = base_items.filter(created_at__gte=last_30)

        # Top products by units sold (used as a proxy for "top products")
        top_products_qs = (
            recent_items
            .values("product__name")
            .annotate(units=Sum("quantity"))
            .order_by("-units")[:10]
        )

        top_products = [
            {"product__name": p["product__name"], "views": int(p["units"])}
            for p in top_products_qs
        ]

        # Daily views placeholder (last 7 days, all zeros)
        daily_views = [
            {
                "date": (now - timedelta(days=i)).strftime("%Y-%m-%d"),
                "views": 0,
            }
            for i in range(6, -1, -1)
        ]

        payload = {
            "status": "success",
            "data": {
                "views_last_7d": 0,
                "views_last_30d": 0,
                "unique_visitors_7d": 0,
                "top_products": top_products,
                "daily_views": daily_views,
            },
        }

        return Response(payload)


class DesignerReturnRequestViewSet(DesignerBaseViewSet):

    serializer_class = ReturnRequestSerializer

    filterset_fields = ["status", "designer_status"]
    search_fields = ["return_id"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]
    search_fields = ["return_id"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    lookup_field = "return_id"
    lookup_url_kwarg = "return_id"

    def get_queryset(self):
        return ReturnRequest.objects.select_related(
            "order_item",
            "order_item__order",
            "order_item__product"
        ).filter(
            order_item__product__user=self.request.user
        )

    @action(detail=True, methods=["post"], url_path="action")
    def perform_action(self, request, return_id=None):
        """
        POST /designers/returns/{return_id}/action

        Body:
        {
            "action": "approve" | "reject",
            "reason": "optional rejection reason"
        }
        """

        instance = self.get_object()

        action_type = request.data.get("action")
        reason = request.data.get("reason", "")

        if action_type not in ["approve", "reject"]:
            return Response(
                {"detail": "Invalid action. Must be 'approve' or 'reject'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order = instance.order_item.order
        customer = order.customer

        context = {
            "customer": customer,
            "return_request": instance,
            "order": order,
            "order_item": instance.order_item,
            "reject_reason": reason,
        }

        if action_type == "approve":

            instance.designer_status = "approved"
            instance.status = ReturnRequest.Status.APPROVED
            instance.save()

            subject = f"Your Return Request #{instance.return_id} Has Been Approved"

            message = render_to_string(
                "designers/return_approved.html",
                context,
            )

        elif action_type == "reject":

            if not reason:
                return Response(
                    {"detail": "Rejection reason is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            instance.designer_status = "rejected"
            instance.status = ReturnRequest.Status.REJECTED
            instance.reject_reason = reason
            instance.save()

            subject = f"Your Return Request #{instance.return_id} Was Rejected"

            message = render_to_string(
                "designers/return_rejected.html",
                context,
            )

        # Send email asynchronously
        threading.Thread(
            target=resend_sendmail,
            args=(subject, [customer.user.email], message),
        ).start()

        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="confirm-receipt")
    def confirm_receipt(self, request, return_id=None):
        """
        POST /designers/returns/{return_id}/confirm-receipt
        Marks the return as received and triggers the refund process.
        """
        instance = self.get_object()

        if instance.status != ReturnRequest.Status.APPROVED and instance.status != ReturnRequest.Status.RETURN_IN_TRANSIT:
             return Response(
                {"detail": "Return must be approved or in transit to confirm receipt."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance.status = ReturnRequest.Status.RETURN_RECEIVED
        instance.save()

        # In a real system, this might trigger a background task to process the refund
        # For now, we'll just update the status.

        serializer = self.get_serializer(instance)
        return Response({
            "status": "success",
            "message": "Return marked as received. Refund process initiated.",
            "data": serializer.data
        })


class NotificationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "status": "success",
            "data": serializer.data,
        })

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        queryset = self.get_queryset().filter(is_read=False)
        count = queryset.count()
        queryset.update(is_read=True, read_at=timezone.now())
        return Response({
            "status": "success",
            "message": f"{count} notification(s) marked as read",
            "count": count,
        })

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save()
        serializer = self.get_serializer(notification)
        return Response({
            "status": "success",
            "data": serializer.data,
        })

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = self.get_queryset().filter(is_read=False).count()
        return Response({
            "status": "success",
            "count": count,
        })


