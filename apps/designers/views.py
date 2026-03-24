from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from apps.core.models import MediaAsset, Product
from apps.core.serializers import ProductSerializer
from apps.customers.models import OrderItem, ReturnRequest
from apps.designers.serializers import ReturnRequestSerializer
from django.db.models import Sum
from apps.utils.pagination import StandardPagination
from .models import Designer, DesignerProduct, DesignerStory, StoryView
from .serializers import (
    DesignerSerializer, DesignerProductSerializer, DesignerStorySerializer, OrderItemSerializer, StoryViewSerializer
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
    # parser_classes = (MultiPartParser, FormParser)  # needed for file uploads

    def get_queryset(self):
        # Only products belonging to the authenticated designer
        return Product.objects.filter(user=self.request.user)

    def create(self, request):
        """
        POST /designer-products/ → Create new product
        """
        serializer = self.get_serializer(data=request.data,partial=True)

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

        serializer = self.get_serializer(product, data=request.data, partial=False)

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
    
    

class DesignerOrderViewSet(DesignerBaseViewSet):

    serializer_class = OrderItemSerializer
    lookup_field = "item_id"

    def get_queryset(self):

        return OrderItem.objects.filter(
            product__user=self.request.user
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
    
# ---------------- Return Requests ----------------
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



