import hashlib
import json
import threading
from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Avg, Prefetch, Sum, Count
from google import genai
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.designers.models import DesignerStory
from apps.designers.serializers import StorySerializer
from apps.utils.email_sender import resend_sendmail
from django.db.models.functions import Lower
from .models import (
    Brand, Country, Currency, Category, MediaAsset, Product, Review, Sizes,
    UserSettings, SmartCollection, ProductView, DesignerDailyAnalytics,
    LoyaltyPoints, LoyaltyBalance, SizeRecommendation, UserLookbook,
    SubscriptionPlan, UserSubscription,
)
from .serializers import (
    ContactMessageSerializer, CountrySerializer, CurrencySerializer, MediaAssetSerializer,
    CategorySerializer, ProductSerializer, ReviewSerializer, SizesSerializer, UserSettingsSerializer,
    SmartCollectionSerializer, ProductViewSerializer, DesignerDailyAnalyticsSerializer,
    LoyaltyPointsSerializer, LoyaltyBalanceSerializer,
    SizeRecommendationSerializer, UserLookbookSerializer,
    SubscriptionPlanSerializer, UserSubscriptionSerializer,
)
from django.db.models import Q, Count, F, Window
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, NotFound
from .models import Country, Currency
from .serializers import (
    CountrySerializer,
    CurrencySerializer,
    MediaAssetSerializer,
)


class CountryListView(APIView):  # → core-countries
    """List all countries available for designers, customers, and shipping."""
    permission_classes = [AllowAny]

    def get(self, request):
        countries = Country.objects.all().order_by("name")
        serializer = CountrySerializer(countries, many=True)
        return Response({
            "status": "success",
            "data": serializer.data
        }, status=status.HTTP_200_OK)


class CurrencyListView(APIView):  # → core-currencies
    """List all active currencies."""
    permission_classes = [AllowAny]

    def get(self, request):
        currencies = Currency.objects.filter(is_active=True)
        serializer = CurrencySerializer(currencies, many=True)
        return Response({
            "status": "success",
            "data": serializer.data
        }, status=status.HTTP_200_OK)


class MediaAssetUploadView(APIView):  # → core-media-upload
    """Upload media assets (images, videos, documents)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = MediaAssetSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": "success",
                "message": "Media uploaded successfully",
                "media": serializer.data
            }, status=status.HTTP_201_CREATED)
        return Response({
            "status": "error",
            "message": "Invalid media upload",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


class StoryListView(APIView):  # → core-stories
    """Public list of published stories celebrating African creativity."""
    permission_classes = [AllowAny]

    def get(self, request):
        stories = DesignerStory.objects.filter(is_active=True).order_by("-created_at")
        serializer = StorySerializer(stories, many=True)
        return Response({
            "status": "success",
            "data": serializer.data
        }, status=status.HTTP_200_OK)


class StoryDetailView(APIView):  # → core-story-detail
    """Retrieve a single story with its full cultural narrative."""
    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            story = DesignerStory.objects.get(pk=pk, is_active=True)
            serializer = StorySerializer(story)
            return Response({
                "status": "success",
                "story": serializer.data
            }, status=status.HTTP_200_OK)
        except ObjectDoesNotExist:
            return Response({
                "status": "error",
                "message": "DesignerStory not found"
            }, status=status.HTTP_404_NOT_FOUND)


class FeaturedStoriesView(APIView):  # → core-stories-featured
    """Return featured storytelling posts."""
    permission_classes = [AllowAny]

    def get(self, request):
        stories = DesignerStory.objects.filter(is_active=True, featured=True)
        serializer = StorySerializer(stories, many=True)
        return Response({
            "status": "success",
            "data": serializer.data
        }, status=status.HTTP_200_OK)


from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Count
from apps.designers.models import Designer, Collection, DesignerProduct
from apps.designers.serializers import DesignerSerializer, CollectionSerializer, DesignerProductSerializer
from apps.core.models import Product

# -------------------------------
# Designer Views
# -------------------------------

class DesignerListView(APIView):
    """List all designers with search and ordering."""
    permission_classes = [AllowAny]

    def get(self, request):
        search = request.GET.get('search')
        designer_id = request.GET.get('id')
        designer_slug = request.GET.get('slug')
        ordering = request.GET.get('ordering', 'user__username')

        lookup_val = designer_slug or designer_id
        if lookup_val:
            try:
                designer = Designer.objects.prefetch_related('lookbook_files').get(
                    Q(slug=lookup_val) | Q(id=lookup_val),
                    status=Designer.Status.APPROVED
                )
            except Designer.DoesNotExist:
                return Response({
                    "status": "error",
                    "message": "Designer not found.",
                    "data": None
                }, status=status.HTTP_404_NOT_FOUND)

            serializer = DesignerSerializer(designer)
            return Response({
                "status":"success",
                "message": "Designer retrieved successfully.",
                "data": serializer.data
            }, status=status.HTTP_200_OK)

        designers = Designer.objects.filter(status=Designer.Status.APPROVED).prefetch_related('lookbook_files')
        if search:
            designers = designers.filter(
                Q(user__username__icontains=search) |
                Q(brand_name__icontains=search)
            )
        designers = designers.order_by(ordering)
        serializer = DesignerSerializer(designers, many=True)
        return Response({
            "status":"success",
            "message": "Designers retrieved successfully.",
            "data": serializer.data
        }, status=status.HTTP_200_OK)


class DesignerDetailView(APIView):
    """Retrieve a single designer with products and collections."""
    permission_classes = [AllowAny]

    def get(self, request, slug):
        try:
            designer = Designer.objects.prefetch_related('collections', 'products__product').get(slug=slug)
            serializer = DesignerSerializer(designer)
            return Response({
                "status":"success",
                "message": "Designer retrieved successfully.",
                "data": serializer.data
            }, status=status.HTTP_200_OK)
        except Designer.DoesNotExist:
            return Response({
                "status":"error",
                "message": "Designer not found.",
                "data": None
            }, status=status.HTTP_404_NOT_FOUND)


# -------------------------------
# Collection Views
# -------------------------------

class CollectionDetailView(APIView):
    """Retrieve a single collection with filtered products."""
    permission_classes = [AllowAny]

    def get(self, request, slug):
        try:
            collection = Collection.objects.prefetch_related('designer__products__product').get(slug=slug)
            
            # Filter products in the collection
            products = collection.designer.products.filter(product__collection__id=collection.id, is_active=True)
            
            # Search
            search_query = request.GET.get('search')
            if search_query:
                products = products.filter(product__title__icontains=search_query)
            
            # Price range
            min_price = request.GET.get('min_price')
            max_price = request.GET.get('max_price')
            if min_price:
                products = products.filter(product__price__gte=min_price)
            if max_price:
                products = products.filter(product__price__lte=max_price)
            
            # Sorting
            sort_by = request.GET.get('sort')  # 'price', '-price', 'created_at', '-created_at'
            if sort_by in ['price', '-price', 'created_at', '-created_at']:
                products = products.order_by(f'product__{sort_by}')

            collection_serializer = CollectionSerializer(collection)
            products_serializer = DesignerProductSerializer(products, many=True)

            data = collection_serializer.data
            data['products'] = products_serializer.data

            return Response({
                "status":"success",
                "message": "Collection retrieved successfully.",
                "data": data
            }, status=status.HTTP_200_OK)
        except Collection.DoesNotExist:
            return Response({
                "status":"error",
                "message": "Collection not found.",
                "data": None
            }, status=status.HTTP_404_NOT_FOUND)


# -------------------------------
# Product Views
# -------------------------------

class ProductDetailView(APIView):
    """Retrieve a single product with images and designer info."""
    permission_classes = [AllowAny]

    def get(self, request, product_id):
        try:
            designer_product = DesignerProduct.objects.select_related('product', 'user').get(product__id=product_id)
            serializer = DesignerProductSerializer(designer_product)
            return Response({
                "status":"success",
                "message": "Product retrieved successfully.",
                "data": serializer.data
            }, status=status.HTTP_200_OK)
        except DesignerProduct.DoesNotExist:
            return Response({
                "status":"error",
                "message": "Product not found.",
                "data": None
            }, status=status.HTTP_404_NOT_FOUND)


class DesignerProductListView(APIView):
    """List all products for designers with filtering and sorting."""
    permission_classes = [AllowAny]

    def get(self, request):
        products = DesignerProduct.objects.select_related('product', 'user').filter(is_active=True)
        
        # Filter by designer
        designer_slug = request.GET.get('designer')
        if designer_slug:
            products = products.filter(designer__slug=designer_slug)
        
        # Filter by category
        category = request.GET.get('category')
        if category:
            products = products.filter(category__slug=category)
        
        # Search
        search_query = request.GET.get('search')
        if search_query:
            products = products.filter(product__title__icontains=search_query)
        
        # Sorting
        sort_by = request.GET.get('sort')
        if sort_by in ['price', '-price', 'created_at', '-created_at', 'featured']:
            products = products.order_by(f'product__{sort_by}' if sort_by != 'featured' else '-featured')
        
        serializer = DesignerProductSerializer(products, many=True)
        return Response({
            "status":"success",
            "message": "Products retrieved successfully.",
            "data": serializer.data
        }, status=status.HTTP_200_OK)


# -------------------------------
# Featured & Trending
# -------------------------------

class FeaturedProductsView(APIView):
    """Retrieve all featured products."""
    permission_classes = [AllowAny]

    def get(self, request):
        products = DesignerProduct.objects.filter(featured=True, is_active=True).select_related('product', 'user')
        serializer = DesignerProductSerializer(products, many=True)
        return Response({
            "status":"success",
            "message": "Featured products retrieved successfully.",
            "data": serializer.data
        }, status=status.HTTP_200_OK)


class TrendingCollectionsView(APIView):
    """Retrieve top trending collections based on number of products."""
    permission_classes = [AllowAny]

    def get(self, request):
        collections = Collection.objects.annotate(num_products=Count('designer__products')).order_by('-num_products')[:10]
        serializer = CollectionSerializer(collections, many=True)
        return Response({
            "status":"success",
            "message": "Trending collections retrieved successfully.",
            "data": serializer.data
        }, status=status.HTTP_200_OK)


# ---------------------------
# Country / Currency
# ---------------------------
class CountryListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        countries = Country.objects.all().order_by("name")
        serializer = CountrySerializer(countries, many=True)
        return Response({"status": "success", "data": serializer.data}, status=status.HTTP_200_OK)


class CurrencyListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        currencies = Currency.objects.filter(is_active=True)
        serializer = CurrencySerializer(currencies, many=True)
        return Response({"status": "success", "data": serializer.data}, status=status.HTTP_200_OK)
    
class SizesListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        sizes = Sizes.objects.all()
        serializer = SizesSerializer(sizes, many=True)
        return Response({"status": "success", "data": serializer.data}, status=status.HTTP_200_OK)


# ---------------------------
# Media
# ---------------------------


class MediaAssetViewSet(ModelViewSet):
    queryset = MediaAsset.objects.all()
    serializer_class = MediaAssetSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"  # matches your custom string ID

    def get_queryset(self):
        # Only return files uploaded by the current user
        return MediaAsset.objects.filter(user=self.request.user)
    
    def perform_create(self, serializer):
        # Automatically attach the logged-in user
        serializer.save(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()  # looks up by custom id
        print(request.data)
        if instance.user != request.user:
            print("Permission error")
            raise PermissionDenied("You cannot delete this file.")
        self.perform_destroy(instance)
        return Response(
            {"status": "success", "message": "File deleted successfully."},
            status=status.HTTP_200_OK
        )


class MediaAssetDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, id, *args, **kwargs):
        """
        Delete a media asset by its custom ID.
        Only the user who uploaded it can delete it.
        """
        try:
            asset = MediaAsset.objects.get(id=id)
        except MediaAsset.DoesNotExist:
            raise NotFound("Media asset not found.")

        # if asset.user != request.user:
        #     return Response(
        #         {"status": "success", "message": "Permission denied"},
        #         status=status.HTTP_400_BAD_REQUEST
        #     )
        asset.delete()
        return Response(
            {"status": "success", "message": "File deleted successfully."},
            status=status.HTTP_200_OK
        )


# ---------------------------
# Categories
# ---------------------------
class CategoryListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        categories = Category.objects.filter(is_active=True)
        serializer = CategorySerializer(categories, many=True)
        return Response({"status": "success", "data": serializer.data}, status=status.HTTP_200_OK)


class BrandListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        brands = Brand.objects.all()
        serializer = BrandSerializer(brands, many=True)
        return Response({"status": "success", "data": serializer.data}, status=status.HTTP_200_OK)


# ---------------------------
# Products
# ---------------------------
class ProductListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        # -----------------------
        # BASE QUERYSET
        # -----------------------
        queryset = (
            Product.objects.filter(is_published=True, is_admin_published=True, is_active=True)
            .annotate(media_count=Count("media"))
            .filter(media_count__gt=0)
            .annotate(color_count=Count("colors", distinct=True))
            .annotate(size_count=Count("sizes", distinct=True))
            .filter(color_count__gt=0, size_count__gt=0)
            .select_related(
                "user",
                "currency",
                "category",
                "subcategory",
                "brand",
                "country_of_origin",
            )
            .prefetch_related(
                "media",
                "sizes",
                "user__designer_profile",
            )
        )

        # -----------------------
        # SORTING (Frontend Driven)
        # -----------------------
        # -----------------------
        # SEARCH FILTER
        # -----------------------
        search = request.GET.get("search", "").strip()

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(brand__name__icontains=search) |
                Q(category__name__icontains=search) |
                Q(subcategory__name__icontains=search) |
                Q(user__designer_profile__brand_name__icontains=search)
            )

        sort = request.GET.get("sort")

        if sort:
            if sort == "price_asc":
                queryset = queryset.order_by("price")

            elif sort == "price_desc":
                queryset = queryset.order_by("-price")

            elif sort == "name_asc":
                queryset = queryset.order_by("name")

            elif sort == "newest":
                queryset = queryset.order_by("-created_at")

            else:
                queryset = queryset.order_by("-created_at")

        else:
            # fallback to tab behavior if no explicit sort
            tab = request.GET.get("tab", "new")

            if tab == "new":
                queryset = queryset.order_by("-created_at")

            elif tab == "trending":
                queryset = queryset.order_by("-popularity_score", "-created_at")

            elif tab == "sustainable":
                queryset = queryset.filter(is_sustainable=True).order_by("-created_at")

            else:
                queryset = queryset.order_by("-created_at")
        # -----------------------
        # DESIGNER COUNTRY FILTER
        # -----------------------
        allowed_countries = ["NG", "KE", "GH", "US", "GB", "ZA"]
        country = request.GET.get("country")

        if country and country in allowed_countries:
            queryset = queryset.filter(
                user__designer_profile__country__iexact=country
            )

        # -----------------------
        # OTHER FILTERS
        # -----------------------
        subcategory = request.GET.get("subcategory")
        if subcategory:
            queryset = queryset.filter(subcategory__slug=subcategory)

        category = request.GET.get("category")
        if category:
            queryset = queryset.filter(category__slug=category)

        designer = request.GET.get("designer")
        if designer:
            queryset = queryset.filter(user__designer_profile__id=designer)

        brand = request.GET.get("brand")
        if brand:
            queryset = queryset.filter(brand_id=brand)

        color = request.GET.get("color")
        if color:
            queryset = queryset.filter(colors_id=color)

        size = request.GET.get("size")
        if size:
            queryset = queryset.filter(sizes__name__iexact=size)

        min_price = request.GET.get("min_price")
        if min_price:
            queryset = queryset.filter(price__gte=min_price)

        max_price = request.GET.get("max_price")
        if max_price:
            queryset = queryset.filter(price__lte=max_price)

        featured = request.GET.get("featured")
        if featured == "true":
            queryset = queryset.filter(featured=True)

        # -----------------------
        # PRD V1 FILTERS
        # -----------------------
        availability = request.GET.get("availability")
        if availability:
            queryset = queryset.filter(availability_type=availability)

        print_type = request.GET.get("print_type")
        if print_type:
            queryset = queryset.filter(print_type=print_type)

        occasion = request.GET.get("occasion")
        if occasion:
            queryset = queryset.filter(occasion=occasion)

        country_origin = request.GET.get("country_origin")
        if country_origin:
            queryset = queryset.filter(country_of_origin__code__iexact=country_origin)

        sustainable = request.GET.get("sustainable")
        if sustainable == "true":
            queryset = queryset.filter(is_sustainable=True)

        # -----------------------
        # PAGINATION (Infinite Scroll)
        # -----------------------
        page = int(request.GET.get("page", 1))
        limit = int(request.GET.get("limit", 20))

        paginator = Paginator(queryset.distinct(), limit)
        page_obj = paginator.get_page(page)

        serializer = ProductSerializer(page_obj, many=True)
        # -----------------------
        # API RESPONSE
        # -----------------------
        return Response(
            {
                "status": "success",
                "data": serializer.data,
                "pagination": {
                    "current_page": page,
                    "total_pages": paginator.num_pages,
                    "total_items": paginator.count,
                    "has_next": page_obj.has_next(),
                },
            },
            status=status.HTTP_200_OK,
        )

# ---------------------------
# Trending Products
# ---------------------------
class TrendingProducts(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        # -----------------------
        # BASE QUERYSET
        # -----------------------
        queryset = (
            Product.objects.filter(is_published=True,is_admin_published = True, is_active=True)
            .exclude(media=False)
            .select_related(
                "user",
                "currency",
                "category",
                "subcategory",
                "brand",
            )
            .prefetch_related(
                "media",
                "user__designer_profile",
            )
        )

        queryset = queryset.order_by("-popularity_score", "-created_at")

        page = int(request.GET.get("page", 1))
        limit = int(request.GET.get("limit", 10))

        paginator = Paginator(queryset.distinct(), limit)
        page_obj = paginator.get_page(page)

        serializer = ProductSerializer(page_obj, many=True)
        # -----------------------
        # API RESPONSE
        # -----------------------
        return Response(
            {
                "status": "success",
                "data": serializer.data,
                "pagination": {
                    "current_page": page,
                    "total_pages": paginator.num_pages,
                    "total_items": paginator.count,
                    "has_next": page_obj.has_next(),
                },
            },
            status=status.HTTP_200_OK,
        )


class ProductDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, id):
        print(id)
        try:
            product = Product.objects.prefetch_related(
                Prefetch('reviews', queryset=Review.objects.filter(is_approved=True)),
                Prefetch('media')
            ).get(id=id,)
            product.popularity_score+=1
            product.save()
            print(product.popularity_score)
            avg_rating = product.reviews.aggregate(Avg('rating'))['rating__avg'] or 0
            return Response({
                "status": "success",
                "product": ProductSerializer(product).data,
                "reviews": ReviewSerializer(product.reviews.all(), many=True).data,
                "average_rating": round(avg_rating, 1),
                "review_count": product.reviews.count()
            }, status=status.HTTP_200_OK)
        except ObjectDoesNotExist:
            return Response({"status": "error", "message": "Product not found"}, status=status.HTTP_404_NOT_FOUND)


# ---------------------------
# Reviews
# ---------------------------
class ReviewListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        product_id = request.GET.get("product")
        designer_id = request.GET.get("designer")

        if product_id:
            reviews = Review.objects.filter(
                product__id=product_id, is_approved=True
            ).order_by("-created_at")
        elif designer_id:
            reviews = Review.objects.filter(
                product__user__id=designer_id, is_approved=True
            ).order_by("-created_at")
        else:
            return Response(
                {"status": "error", "detail": "product or designer query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReviewSerializer(reviews, many=True)
        return Response({"results": serializer.data, "count": len(serializer.data)})


class ReviewCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.customers.models import OrderItem
        from django.db.models import Q

        product_id = request.data.get("product")
        if not product_id:
            return Response({"status": "error", "errors": {"product": "Product is required"}}, status=status.HTTP_400_BAD_REQUEST)

        # Verify the user has actually purchased this product
        has_purchased = OrderItem.objects.filter(
            product__id=product_id,
            order__customer__user=request.user,
        ).filter(
            Q(order__invoice__payment__is_paid=True) |
            Q(order__invoice__payment__status="success")
        ).exists()

        if not has_purchased:
            return Response(
                {"status": "error", "message": "You can only review products you have purchased."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Prevent duplicate reviews
        already_reviewed = Review.objects.filter(
            product__id=product_id,
            customer__user=request.user
        ).exists()

        if already_reviewed:
            return Response(
                {"status": "error", "message": "You have already reviewed this product."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReviewSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(customer=request.user.customer)
            return Response({"status": "success", "review": serializer.data}, status=status.HTTP_201_CREATED)
        return Response({"status": "error", "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


class CanReviewView(APIView):
    """
    GET /core/can-review?product_id=<id>
    Returns whether the authenticated user can review a given product.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.customers.models import OrderItem
        from django.db.models import Q

        product_id = request.GET.get("product_id")
        if not product_id:
            return Response({"can_review": False}, status=status.HTTP_400_BAD_REQUEST)

        has_purchased = OrderItem.objects.filter(
            product__id=product_id,
            order__customer__user=request.user,
        ).filter(
            Q(order__invoice__payment__is_paid=True) |
            Q(order__invoice__payment__status="success")
        ).exists()

        already_reviewed = Review.objects.filter(
            product__id=product_id,
            customer__user=request.user
        ).exists()

        return Response({
            "can_review": has_purchased and not already_reviewed,
            "has_purchased": has_purchased,
            "already_reviewed": already_reviewed,
        })




class UserSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        settings_obj, _ = UserSettings.objects.get_or_create(
            user=request.user
        )
        serializer = UserSettingsSerializer(settings_obj)
        return Response({'data':serializer.data})

    def put(self, request):
        settings_obj, _ = UserSettings.objects.get_or_create(
            user=request.user
        )
        serializer = UserSettingsSerializer(
            settings_obj,
            data=request.data,
            partial=True
        )

        if serializer.is_valid():
            serializer.save()
            return Response({"data":serializer.data})

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )




class ContactMessageView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ContactMessageSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contact = serializer.save(
            user=request.user if request.user.is_authenticated else None
        )

        # -----------------------
        # SEND EMAIL TO SUPPORT
        # -----------------------
        subject = f"New Contact Message from {contact.name}"

        message = f"""
        <p>You have received a new contact message on Urbana.</p>
        <br>
        <p><strong>Name:</strong> {contact.name}</p>
        <p><strong>Email:</strong> {contact.email}</p>
        <br>
        <p><strong>Message:</strong></p>
        <p>{contact.message}</p>
        """

        threading.Thread(
            target=resend_sendmail,
            args=(
                subject,
                ["supporturbanaafrica@gmail.com"],
                message,
            ),
        ).start()

        return Response(
            {
                "status": "success",
                "message": "Your message has been sent successfully.",
            },
            status=status.HTTP_201_CREATED,
        )



class SearchSuggestions(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        query = request.GET.get("q", "").strip()

        if not query:
            return Response({"data": []})

        products = (
            Product.objects
            .filter(name__icontains=query, is_published=True, is_admin_published=True, is_active=True)
            .prefetch_related("media")
            .order_by("name")
            .distinct()[:6]
        )

        data = []
        for product in products:
            first_image = product.media.filter(media_type="image").first()
            data.append({
                "id": product.id,
                "name": product.name,
                "slug": product.slug,
                "price": str(product.price),
                "thumbnail": first_image.file.url if first_image else None,
            })

        return Response({"data": data})




# class ProductViewSet(ModelViewSet):
#     queryset = Product.objects.all()
#     serializer_class = ProductSerializer
#     filter_backends = [SearchFilter]
#     search_fields = ["name"]


class SeedDummyDataView(APIView):
    """
    Endpoint to generate a dummy approved Designer and 10 dummy Products.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        from django.contrib.auth import get_user_model
        import random
        import requests
        from django.core.files.base import ContentFile
        from apps.designers.models import Designer, DesignerProduct
        from apps.core.models import Category, Product, Currency, MediaAsset, Sizes, Color

        User = get_user_model()

        # 1. Create or get Category
        category, _ = Category.objects.get_or_create(
            name="Dummy Category",
            defaults={"slug": "dummy-category"}
        )

        currency, _ = Currency.objects.get_or_create(
            code="NGN",
            defaults={"name": "Naira", "symbol": "₦", "is_active": True}
        )

        # 2. Create or get Dummy User
        username = f"designer_{random.randint(1000, 9999)}"
        email = f"{username}@urbanaafrica.com"
        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": username,
                "first_name": "Dummy",
                "last_name": "Designer",
                "is_active": True
            }
        )
        if user_created:
            user.set_password("password123")
            user.save()

        # 3. Create or update Designer profile
        designer, _ = Designer.objects.get_or_create(
            user=user,
            defaults={
                "brand_name": f"Dummy Brand {username}",
                "status": Designer.Status.APPROVED,
                "is_verified": True,
                "country": "NG",
            }
        )
        designer.status = Designer.Status.APPROVED
        
        try:
            profile_url = f"https://lh3.googleusercontent.com/aida-public/AB6AXuA8tDKjTl6IryZVSGy3zfH575kPuZ0hC8JMAvsrqbJPyOAq74BuRzeLtodBr8VN2C-gSyCU5GnM2j1CNQ7EvWqBjEodKj4O1A9EVtSfLUeHT7jokO7uTOpOGxlNLEVsgY90SwTywyQJC-1tVx6jkx_OxC-qXCkR81S7sZ5S_Fc0H6-sTO6lT2nJbvJym1QrlPR2W-Trb4SdmvMePmieH-sPUS-4cTyS8GPmtkGdO5D0tWJEu51PWQuc2yRjyPygCPwCpuCi9PY-jsQ"
            resp_prof = requests.get(profile_url, timeout=10)
            if resp_prof.status_code == 200:
                designer.profile_picture.save(f"profile_{username}.jpg", ContentFile(resp_prof.content), save=False)
                designer.banner_image.save(f"banner_{username}.jpg", ContentFile(resp_prof.content), save=False)
        except Exception as e:
            print(f"Failed to fetch profile picture for {username}: {e}")

        designer.save()

        # Ensure some sizes exist globally
        size_s, _ = Sizes.objects.get_or_create(name="S", defaults={"description": "Small"})
        size_m, _ = Sizes.objects.get_or_create(name="M", defaults={"description": "Medium"})
        size_l, _ = Sizes.objects.get_or_create(name="L", defaults={"description": "Large"})

        # 4. Create 10 Dummy Products
        created_products = []
        for i in range(1, 6):
            prod_name = f"Dummy Product {i} - {random.randint(100, 999)}"
            slug_base = prod_name.lower().replace(" ", "-")

            product = Product.objects.create(
                user=user,
                name=prod_name,
                slug=slug_base,
                description="This is an exclusive dummy product featuring modern designs and high-quality materials.",
                price=random.randint(5000, 50000),
                currency=currency,
                category=category,
                stock=50,
                is_published=True,
                is_admin_published=True,
                is_active=True,
                featured=True
            )

            # Attach Sizes
            product.sizes.add(size_s, size_m, size_l)

            # Attach Colors (simple color names)
            Color.objects.get_or_create(name="Black", hex_code="#000000", product=product)
            Color.objects.get_or_create(name="White", hex_code="#FFFFFF", product=product)

            # Generate and attach 1 dummy picture from picsum
            try:
                img_url = f"https://lh3.googleusercontent.com/aida-public/AB6AXuCOIN4HqXs7bddfJeL_Gr93Ms_8dvuFHy7E_3y36ftHUHnt6ZGPp_oTBXI9nyhE-Ho9HDK3NdDQjE4777OEYJesmDxyexbfbqJbK6Mh8zHHNzHh5iZBWo5CVFqSd4C7br0_4LVVYjfzxBhMhb-0EgJFhWQsOq4zCcjGMLEzHo7eDHWtFSNKDmtzHYzv1QvBxDwhduahPQVfzlcSpkhZju7yQpaIusTC_KgPVvoUSLqj7z97FioLknzG8hvv-AvAra8vxei-PIvB02k"
                resp = requests.get(img_url, timeout=10)
                if resp.status_code == 200:
                    asset = MediaAsset.objects.create(
                        user=user,
                        media_type=MediaAsset.MediaType.IMAGE,
                        alt_text=f"Dummy Image for {prod_name}"
                    )
                    asset.file.save(f"{slug_base}.jpg", ContentFile(resp.content), save=True)
                    product.media.add(asset)
            except Exception as e:
                print(f"Failed to fetch image for {prod_name}: {e}")

            DesignerProduct.objects.create(
                designer=designer,
                product=product,
                featured=True,
                is_active=True,
                stock=50
            )
            created_products.append(product.name)

        return Response({
            "status": "success",
            "message": "Successfully seeded dummy designer, products, and images.",
            "designer": designer.brand_name,
            "created_products": created_products
        })


# --------------------------------------------------
# Support Tickets
# --------------------------------------------------
from .models import SupportTicket
from .serializers import SupportTicketSerializer


class SupportTicketCreateView(APIView):
    """
    POST /core/support/tickets
    Create a new support ticket. If the user is authenticated, the ticket is
    linked to their account. Guest users must supply guest_name and guest_email.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SupportTicketSerializer(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket = serializer.save(
            user=request.user if request.user.is_authenticated else None
        )

        # ── Determine submitter details for email ──
        submitter_name = (
            request.user.get_full_name() or request.user.username
            if request.user.is_authenticated
            else ticket.guest_name
        )
        submitter_email = (
            request.user.email
            if request.user.is_authenticated
            else ticket.guest_email
        )

        # ── Email to support team ──
        support_subject = f"[{ticket.reference}] New Support Ticket – {ticket.get_category_display()}"
        support_body = f"""
        <h2>New Support Ticket Received</h2>
        <table cellpadding="6" style="border-collapse:collapse;">
          <tr><td><strong>Reference</strong></td><td>{ticket.reference}</td></tr>
          <tr><td><strong>From</strong></td><td>{submitter_name} ({submitter_email})</td></tr>
          <tr><td><strong>Category</strong></td><td>{ticket.get_category_display()}</td></tr>
          <tr><td><strong>Priority</strong></td><td>{ticket.get_priority_display()}</td></tr>
          <tr><td><strong>Subject</strong></td><td>{ticket.subject}</td></tr>
          <tr><td><strong>Description</strong></td><td>{ticket.description}</td></tr>
        </table>
        <br>
        <p>Please respond via the admin panel.</p>
        """

        # ── Confirmation email to designer/submitter ──
        designer_subject = f"We received your request – {ticket.reference}"
        designer_body = f"""
        <p>Hi {submitter_name},</p>
        <p>Thank you for reaching out to Urbana Support. We have received your ticket and our team will respond within <strong>24–48 hours</strong>.</p>
        <br>
        <table cellpadding="6" style="border-collapse:collapse;background:#faf7f4;border-radius:8px;">
          <tr><td><strong>Ticket Reference</strong></td><td>{ticket.reference}</td></tr>
          <tr><td><strong>Subject</strong></td><td>{ticket.subject}</td></tr>
          <tr><td><strong>Category</strong></td><td>{ticket.get_category_display()}</td></tr>
          <tr><td><strong>Priority</strong></td><td>{ticket.get_priority_display()}</td></tr>
        </table>
        <br>
        <p>You can track the status of your ticket by logging into your designer dashboard and visiting <strong>Help &amp; Support → My Tickets</strong>.</p>
        <br>
        <p>Warm regards,<br><strong>Urbana Support Team</strong></p>
        """

        def send_emails():
            resend_sendmail(support_subject, ["supporturbanaafrica@gmail.com"], support_body)
            if submitter_email:
                resend_sendmail(designer_subject, [submitter_email], designer_body)

        threading.Thread(target=send_emails).start()

        return Response(
            {
                "status": "success",
                "message": "Your support ticket has been submitted. Check your email for confirmation.",
                "ticket": SupportTicketSerializer(ticket).data,
            },
            status=status.HTTP_201_CREATED,
        )


class SupportTicketListView(APIView):
    """
    GET /core/support/tickets
    Returns all tickets submitted by the logged-in designer.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tickets = SupportTicket.objects.filter(user=request.user)

        # Optional status filter
        status_filter = request.GET.get("status")
        if status_filter:
            tickets = tickets.filter(status=status_filter)

        serializer = SupportTicketSerializer(tickets, many=True)
        return Response(
            {"status": "success", "data": serializer.data},
            status=status.HTTP_200_OK,
        )


class SupportTicketDetailView(APIView):
    """
    GET /core/support/tickets/<id>
    Returns a single ticket belonging to the logged-in designer.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, ticket_id):
        try:
            ticket = SupportTicket.objects.get(id=ticket_id, user=request.user)
        except SupportTicket.DoesNotExist:
            return Response(
                {"status": "error", "message": "Ticket not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = SupportTicketSerializer(ticket)
        return Response(
            {"status": "success", "data": serializer.data},
            status=status.HTTP_200_OK,
        )


# -------------------------------
# Smart Collections (Phase 2)
# -------------------------------
class SmartCollectionListView(APIView):
    """List active smart collections."""
    permission_classes = [AllowAny]

    def get(self, request):
        collections = SmartCollection.objects.filter(is_active=True).prefetch_related("products")
        serializer = SmartCollectionSerializer(collections, many=True)
        return Response({"status": "success", "data": serializer.data})


class SmartCollectionDetailView(APIView):
    """Get a single smart collection by slug."""
    permission_classes = [AllowAny]

    def get(self, request, slug):
        try:
            collection = SmartCollection.objects.prefetch_related("products").get(slug=slug, is_active=True)
        except SmartCollection.DoesNotExist:
            return Response({"status": "error", "message": "Collection not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = SmartCollectionSerializer(collection)
        return Response({"status": "success", "data": serializer.data})


# -------------------------------
# Event Tracking (Phase 2)
# -------------------------------
class TrackEventsView(APIView):
    """POST /core/track — batch event ingestion from frontend."""
    permission_classes = [AllowAny]

    def post(self, request):
        events = request.data if isinstance(request.data, list) else [request.data]
        created = 0
        for ev in events:
            product_id = ev.get("metadata", {}).get("product_id") or ev.get("product_id")
            designer_id = ev.get("metadata", {}).get("designer_id") or ev.get("designer_id")
            source = ev.get("metadata", {}).get("source", "organic")
            event_type = ev.get("event_type", "product_view")
            session_id = ev.get("session_id", "")

            if not product_id:
                continue

            product = Product.objects.filter(id=product_id).first()
            if not product:
                continue

            designer = None
            if designer_id:
                try:
                    designer = Designer.objects.get(id=designer_id)
                except Designer.DoesNotExist:
                    pass

            ProductView.objects.create(
                product=product,
                designer=designer,
                session_id=session_id,
                event_type=event_type,
                source=source,
                metadata=ev.get("metadata", {}),
            )
            created += 1

        return Response({"status": "success", "tracked": created}, status=status.HTTP_201_CREATED)


# -------------------------------
# Designer Analytics (Phase 2)
# -------------------------------
class DesignerAnalyticsView(APIView):
    """GET /designers/analytics — return aggregated stats for logged-in designer."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            designer = Designer.objects.get(user=request.user)
        except Designer.DoesNotExist:
            return Response({"status": "error", "message": "Designer profile not found."}, status=status.HTTP_404_NOT_FOUND)

        # Time ranges
        from django.utils import timezone
        from datetime import timedelta
        today = timezone.now().date()
        last_7 = today - timedelta(days=6)
        last_30 = today - timedelta(days=29)

        # Aggregate from raw events
        views_7d = ProductView.objects.filter(designer=designer, created_at__date__gte=last_7).count()
        views_30d = ProductView.objects.filter(designer=designer, created_at__date__gte=last_30).count()

        # Unique sessions in last 7 days
        unique_7d = (
            ProductView.objects.filter(designer=designer, created_at__date__gte=last_7)
            .values("session_id")
            .distinct()
            .count()
        )

        # Top products by views (last 30d)
        top_products = (
            ProductView.objects.filter(designer=designer, created_at__date__gte=last_30)
            .values("product__name")
            .annotate(views=models.Count("id"))
            .order_by("-views")[:5]
        )

        # Daily breakdown (last 7 days)
        daily = (
            ProductView.objects.filter(designer=designer, created_at__date__gte=last_7)
            .values("created_at__date")
            .annotate(views=models.Count("id"))
            .order_by("created_at__date")
        )

        return Response({
            "status": "success",
            "data": {
                "designer_id": str(designer.id),
                "views_last_7d": views_7d,
                "views_last_30d": views_30d,
                "unique_visitors_7d": unique_7d,
                "top_products": list(top_products),
                "daily_views": [
                    {"date": str(d["created_at__date"]), "views": d["views"]} for d in daily
                ],
            },
        })


# -------------------------------
# Loyalty Points (Phase 2)
# -------------------------------
class LoyaltyBalanceView(APIView):
    """GET /core/loyalty/balance — current user loyalty balance."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        balance, _ = LoyaltyBalance.objects.get_or_create(user=request.user)
        serializer = LoyaltyBalanceSerializer(balance)
        return Response({"status": "success", "data": serializer.data})


class LoyaltyHistoryView(APIView):
    """GET /core/loyalty/history — transaction history."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        transactions = LoyaltyPoints.objects.filter(user=request.user)[:50]
        serializer = LoyaltyPointsSerializer(transactions, many=True)
        return Response({"status": "success", "data": serializer.data})


# -------------------------------
# Size Recommendation (Phase 2)
# -------------------------------
class SizeRecommendationViewSet(ModelViewSet):
    """CRUD /core/size-recommendations — user-specific size recommendations."""
    permission_classes = [IsAuthenticated]
    serializer_class = SizeRecommendationSerializer

    def get_queryset(self):
        return SizeRecommendation.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# -------------------------------
# User Lookbook (Phase 2)
# -------------------------------
class UserLookbookViewSet(ModelViewSet):
    """CRUD /core/lookbooks — user-curated lookbooks."""
    permission_classes = [IsAuthenticated]
    serializer_class = UserLookbookSerializer

    def get_queryset(self):
        return UserLookbook.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

# ─────────────────────────────────────────────────────────────────────────────
# AI Search & Suggestions
# ─────────────────────────────────────────────────────────────────────────────
from google import genai
from django.conf import settings
import json
import random


class AiSearchView(APIView):
    """
    POST /core/ai-search
    Accepts a natural language query, uses Gemini 1.5 Flash to parse it into
    structured filters, queries the database and returns matching products.

    Fallback tiers (applied when results < MIN_RESULTS):
      1. Exact Gemini filters
      2. Drop price / availability / sustainability constraints
      3. Keyword-only match
      4. Trending products
    """
    permission_classes = [AllowAny]
    MIN_RESULTS = 6

    # ── DB helpers ────────────────────────────────────────────────────────

    def _base_qs(self):
        return (
            Product.objects
            .filter(is_published=True, is_admin_published=True, is_active=True, stock__gt=0)
            .exclude(media=False)
            .select_related("user", "currency", "category", "subcategory", "brand", "country_of_origin")
            .prefetch_related("media", "sizes", "user__designer_profile")
        )

    def _apply_keyword(self, qs, term):
        if not term:
            return qs
        return qs.filter(
            Q(name__icontains=term) |
            Q(brand__name__icontains=term) |
            Q(category__name__icontains=term) |
            Q(subcategory__name__icontains=term) |
            Q(description__icontains=term) |
            Q(user__designer_profile__brand_name__icontains=term)
        )

    def _apply_exact_filters(self, qs, f):
        if f.get("occasion"):
            qs = qs.filter(occasion=f["occasion"])
        if f.get("print_type"):
            qs = qs.filter(print_type=f["print_type"])
        if f.get("availability_type"):
            qs = qs.filter(availability_type=f["availability_type"])
        if f.get("is_sustainable") is True:
            qs = qs.filter(is_sustainable=True)
        if f.get("min_price"):
            qs = qs.filter(price__gte=f["min_price"])
        if f.get("max_price"):
            qs = qs.filter(price__lte=f["max_price"])
        return qs

    def _apply_loose_filters(self, qs, f):
        """Style/occasion/print only — drop price, availability, sustainability."""
        if f.get("occasion"):
            qs = qs.filter(occasion=f["occasion"])
        if f.get("print_type"):
            qs = qs.filter(print_type=f["print_type"])
        return qs

# ── Gemini helper (module-level so both views can use it) ─────────────

def _call_gemini(message, gemini_key, user_context=""):
    system_prompt = f"""You are Zuri, a warm, personal fashion companion for Urbana Africa, a pan-African fashion marketplace. Speak like a friendly human stylist — not a robot. Use "I", "me", and "my" naturally. Avoid stiff or overly formal language.
Analyze the user's natural language query.
First, determine if the message is:
A) a customer support/service question (returns, refunds, order tracking, wallet balance, custom tailoring guide)
B) a fashion advice/trending question ("suggest trending styles", "what should I wear", "what is in fashion", general fashion tips)
C) a specific shopping/product search query (looking for dresses, suits, shoes, etc.)
D) completely off-topic (not related to fashion, shopping, or Urbana — e.g. weather, politics, math homework, general trivia)

If A or B:
- Set `is_support` to true.
- In `style_note`, write a direct, highly helpful, friendly and complete answer. For fashion advice, give concrete, inspiring suggestions with examples.
- Set all other parameters (occasion, print_type, price, search, etc.) to null.

If C:
- Set `is_support` to false.
- Extract structured search parameters from the user's natural language query.
- Set `style_note` to a warm, friendly 1-2 sentence summary of what you understood.

If D (off-topic):
- Set `is_support` to true.
- In `style_note`, write a polite but firm message telling the user you only answer fashion, shopping, and Urbana marketplace questions. Do NOT answer the off-topic question at all. Redirect them clearly. Example: "I'm Zuri, and I only help with fashion, shopping, and Urbana marketplace questions. Ask me about African fashion, outfits, orders, or tailoring — I'd love to help with that. What are we looking for today?"
- Set all other parameters to null.

{user_context}
Available product fields:
- occasion: "wedding" | "work" | "casual" | "party" | "traditional" | "other"
- print_type: "ankara" | "adire" | "kente" | "bogolan" | "other"
- availability_type: "ready_to_ship" | "made_to_order" | "pre_order" | "rentable"
- is_sustainable: true | false
- min_price: number in USD (only if user explicitly mentions a minimum price)
- max_price: number in USD (only if user mentions a budget or maximum)
- search: 1-3 keywords describing the clothing type (e.g. "dress", "agbada", "jumpsuit", "headpiece")
- style_note: The response text to display to the user.

Rules:
- Only set fields clearly implied by the query — do not guess.
- If price is vague ("affordable", "cheap"), do NOT set min_price/max_price.
- style_note must always be present.
- If the user mentions gender (male/female) in their query, respect it. Otherwise use the user profile context provided above.

Respond ONLY with valid JSON. No markdown, no extra text.
Example for support: {{"is_support": true, "style_note": "To return a product, navigate to your Profile, go to 'Orders', select the item, and click 'Return Item' within 14 days of delivery.", "search": null}}
Example for advice: {{"is_support": true, "style_note": "This season's top trends:\n1. Modern Ankara Power Suits\n2. Adire Minimalist Dresses\n3. Kente Statement Jackets\n4. Bogolan Streetwear\n5. Sustainable Capsule Wardrobes\n\nWould you like me to find specific pieces?", "search": null}}
Example for off-topic: {{"is_support": true, "style_note": "I'm Zuri, and I only help with fashion, shopping, and Urbana marketplace questions. Ask me about African fashion, outfits, orders, or tailoring — I'd love to help with that. What are we looking for today?", "search": null}}
Example for shopping: {{"is_support": false, "occasion": "wedding", "print_type": "ankara", "max_price": 500, "search": "dress", "style_note": "Looking for bold Ankara wedding guest dresses under USD 500 — here's what our designers have for you."}}"""

    # Cache key: hash of message + user_context
    cache_key = f"ai_search:gemini:{hashlib.sha256((message + user_context).encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    client = genai.Client(api_key=gemini_key)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model="gemini-1.5-flash-latest",
            contents=message,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        try:
            response = future.result(timeout=8)
        except concurrent.futures.TimeoutError:
            raise TimeoutError("Gemini response timed out")

    parsed = json.loads(response.text)
    cache.set(cache_key, parsed, timeout=300)  # 5 minutes
    return parsed


class AiSearchView(APIView):
    # ── Main handler ──────────────────────────────────────────────────────

    def post(self, request):
        message = request.data.get("message", "").strip() or request.data.get("query", "").strip()
        if not message:
            return Response(
                {"status": "error", "message": "Message or query is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        gemini_key = getattr(settings, "GEMINI_SECRET_KEY", None)
        if not gemini_key:
            return Response(
                {"status": "error", "message": "AI service is currently unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Check for direct local support queries (matches quick action chips exactly)
        exact_query = message.lower().strip("?. ")
        support_answers = {
            "help me track my last order": "To track your order, navigate to your Profile dashboard, click on 'Orders', and select your active order to see real-time shipping status and tracking updates.",
            "show custom tailors sizing guide": "Urbana provides a detailed Bespoke Measurement Vault under your Profile dashboard where you can store your parameters. You can also use our interactive camera FitMe tool for live visual pose alignment.",
            "how do i return a product": "To return a product, go to the 'Orders' list under your Profile dashboard, select the order, and click 'Return Item'. We accept returns within 14 days of delivery in original, unused condition.",
            "check my urbana wallet balance": "You can view your current Urbana Wallet balance and transaction history in the 'Wallet' tab of your Profile. You can also fund it directly using standard checkout gateways (Card, Transfer, or Mobile Money).",
        }
        advice_answers = {
            "suggest trending fashion styles": "Here are the hottest trends our community is loving right now:\n\n1. Modern Ankara Power Suits – Bold prints meet corporate chic.\n2. Adire Minimalist Dresses – Clean silhouettes with hand-dyed textures.\n3. Kente Statement Jackets – Layered over neutrals for maximum impact.\n4. Bogolan Streetwear – Earth-tone mud cloth paired with contemporary cuts.\n5. Sustainable Capsule Wardrobes – Mix-and-match pieces from eco-conscious designers.\n\nWould you like me to find specific pieces in any of these styles?",
            "what is trending": "This season's top trends include:\n\n• **Ankara Blazers** – Tailored outerwear with vibrant West-African prints.\n• **Adire Jumpsuits** – One-piece hand-dyed garments for effortless elegance.\n• **Kente Accents** – Woven strips on cuffs, collars, and accessories.\n• **Minimalist Neutral Sets** – Beige, cream, and clay tones for everyday luxury.\n• **Sustainable Linens** – Breathable, ethically sourced fabrics.\n\nTap any style above to see matching pieces.",
            "what should i wear": "I'd love to help you decide! Tell me more about the occasion (wedding, work, casual, party, traditional) and I'll curate the perfect outfit for you.",
        }

        if exact_query in support_answers:
            return Response(
                {"status": "success", "style_note": support_answers[exact_query], "data": []},
                status=status.HTTP_200_OK,
            )
        if exact_query in advice_answers:
            return Response(
                {"status": "success", "style_note": advice_answers[exact_query], "data": []},
                status=status.HTTP_200_OK,
            )

        # Step 1 — Build user context
        user = request.user if request.user.is_authenticated else None
        user_context = ""
        if user:
            parts = []
            if getattr(user, 'gender', ''):
                parts.append(f"gender: {user.gender}")
            if getattr(user, 'height', ''):
                parts.append(f"height: {user.height}")
            if getattr(user, 'size', ''):
                parts.append(f"typical size: {user.size}")
            if parts:
                user_context = f"User profile context (use if query doesn't contradict): {', '.join(parts)}.\n"

        # Step 2 — Parse with Gemini
        try:
            parsed_filters = _call_gemini(message, gemini_key, user_context)
        except Exception:
            parsed_filters = {
                "search": message,
                "style_note": "Here\u2019s what I found based on your description.",
            }

        # Handle support response from Gemini
        if parsed_filters.get("is_support") is True:
            return Response(
                {
                    "status": "success",
                    "style_note": parsed_filters.get("style_note"),
                    "data": [],
                },
                status=status.HTTP_200_OK,
            )

        limit = int(request.GET.get("limit", 6))
        is_fallback = False
        fallback_reason = None

        def _has_enough(queryset, min_needed):
            """Check if queryset has >= min_needed results without a full COUNT."""
            return queryset[:min_needed].exists()

        # Step 2 — Tier 1: Exact filters
        qs = self._base_qs()
        qs = self._apply_keyword(qs, parsed_filters.get("search"))
        qs = self._apply_exact_filters(qs, parsed_filters)
        qs = qs.order_by("-popularity_score", "-created_at").distinct()
        has_results = _has_enough(qs, self.MIN_RESULTS)

        # Step 3 — Tier 2: Loosen price/availability/sustainability
        if not has_results:
            qs2 = self._base_qs()
            qs2 = self._apply_keyword(qs2, parsed_filters.get("search"))
            qs2 = self._apply_loose_filters(qs2, parsed_filters)
            qs2 = qs2.order_by("-popularity_score", "-created_at").distinct()
            if _has_enough(qs2, self.MIN_RESULTS):
                qs = qs2
                has_results = True
                is_fallback, fallback_reason = True, "loosened"
                original = parsed_filters.get("style_note", "")
                if parsed_filters.get("max_price"):
                    parsed_filters["style_note"] = (
                        f"{original} I couldn\u2019t find enough pieces within that exact budget, "
                        f"so I\u2019ve broadened the results \u2014 prices may vary."
                    )

        # Step 4 — Tier 3: Keyword-only
        if not has_results and parsed_filters.get("search"):
            qs3 = self._base_qs()
            qs3 = self._apply_keyword(qs3, parsed_filters.get("search"))
            qs3 = qs3.order_by("-popularity_score", "-created_at").distinct()
            if _has_enough(qs3, self.MIN_RESULTS):
                qs = qs3
                has_results = True
                is_fallback, fallback_reason = True, "keyword_only"
                parsed_filters["style_note"] = (
                    "I couldn\u2019t find an exact match, but here are the closest pieces "
                    "our designers have to offer \u2014 styled with your vibe in mind."
                )

        # Step 5 — Tier 4: Trending fallback
        if not has_results:
            qs = self._base_qs().order_by("-popularity_score", "-created_at").distinct()
            is_fallback, fallback_reason = True, "trending_fallback"
            parsed_filters["style_note"] = (
                "I couldn\u2019t find pieces that exactly match your description right now \u2014 "
                "but here are the most popular looks our community is loving this season."
            )

        paginator = Paginator(qs, limit)
        page_obj = paginator.get_page(1)
        serializer = ProductSerializer(page_obj, many=True)

        return Response(
            {
                "status": "success",
                "style_note": parsed_filters.get("style_note", "Here are the pieces I found for you."),
                "parsed_filters": parsed_filters,
                "is_fallback": is_fallback,
                "fallback_reason": fallback_reason,
                "data": serializer.data,
                "pagination": {
                    "current_page": 1,
                    "total_pages": paginator.num_pages,
                    "total_items": paginator.count,
                    "has_next": page_obj.has_next(),
                },
            },
            status=status.HTTP_200_OK,
        )


class AiSuggestionsView(APIView):
    """
    GET /core/ai-suggestions
    Returns 5 search prompt suggestions curated from real products in the DB
    by parsing them through Gemini.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        gemini_key = getattr(settings, "GEMINI_SECRET_KEY", None)
        if not gemini_key:
            return Response(
                {"status": "error", "message": "AI service is unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Pull up to 20 distinct products
        products = (
            Product.objects
            .filter(is_published=True, is_admin_published=True, is_active=True)
            .select_related("category")
            .values("name", "occasion", "print_type", "category__name", "price")
            .order_by("-popularity_score", "-created_at")[:20]
        )

        products_list = list(products)
        random.shuffle(products_list)
        sample = products_list[:10]  # Take 10 to give Gemini variety

        DEFAULT_SUGGESTIONS = [
            "Style me for a Nigerian Wedding party",
            "Adire capsule wardrobe under $200",
            "Ankara gifts for a special occasion",
            "Custom tailored Agbada fit sizes",
            "Modern African streetwear"
        ]

        if not sample:
            return Response(
                {"status": "success", "data": DEFAULT_SUGGESTIONS}, status=status.HTTP_200_OK
            )

        # Format DB sample for Gemini
        context_items = []
        for p in sample:
            price = p.get("price")
            context_items.append(
                f"- {p.get('name')} (Type: {p.get('category__name')}, Print: {p.get('print_type')}, Occasion: {p.get('occasion')}, Price: USD {price})"
            )
        context_text = "\n".join(context_items)

        system_prompt = f"""You are Zuri, a creative fashion companion for Urbana Africa.
Your task is to generate 5 inspiring, natural-sounding search prompts that a user could use in our AI Search, BASED EXCLUSIVELY on the actual products in our catalog.

Here is a sample of our current inventory:
{context_text}

Rules for generating prompts:
- Each prompt must be a natural sentence fragment someone would type (e.g., "A bold Ankara dress for a wedding under USD 500").
- The prompts must map back to the products provided in the inventory sample above.
- Make them diverse (mix up occasions, prints, prices, clothing types).
- Keep them under 15 words each.

Respond ONLY with a valid JSON array of 5 strings. No markdown formatting, no extra text.
Example: ["Show me elegant Adire outfits for work", "Ankara wedding guest dress under 400 USD"]"""

        try:
            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model="gemini-1.5-flash-latest",
                contents="Generate 5 search prompt suggestions.",
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.7,
                    response_mime_type="application/json",
                ),
            )
            suggestions = json.loads(response.text)
            if not isinstance(suggestions, list):
                raise ValueError("Expected a list")
            suggestions = suggestions[:5]
        except Exception:
            # Dynamic fallback: build diverse prompts from actual DB sample
            suggestions = self._build_dynamic_suggestions(sample)

        return Response(
            {"status": "success", "data": suggestions[:5]}, status=status.HTTP_200_OK
        )

    def _build_dynamic_suggestions(self, products):
        """Build AI-style suggestions from real product data when Gemini is unavailable."""
        DEFAULT_SUGGESTIONS = [
            "Style me for a Nigerian Wedding party",
            "Adire capsule wardrobe under $200",
            "Ankara gifts for a special occasion",
            "Custom tailored Agbada fit sizes",
            "Modern African streetwear"
        ]
        if not products:
            return DEFAULT_SUGGESTIONS

        suggestions = []
        occasions = {}
        prints = {}
        categories = {}

        for p in products:
            occ = p.get("occasion", "").replace("_", " ").title()
            prt = p.get("print_type", "").replace("_", " ").title()
            cat = p.get("category__name", "")
            name = p.get("name", "")

            if occ and occ != "Other":
                occasions.setdefault(occ, []).append(name)
            if prt and prt != "Other":
                prints.setdefault(prt, []).append(name)
            if cat:
                categories.setdefault(cat, []).append(name)

        # Build diverse prompts from actual data
        occasion_list = list(occasions.keys())
        print_list = list(prints.keys())
        cat_list = list(categories.keys())

        templates = [
            "Show me {print} {category} for a {occasion}",
            "Elegant {print} styles for {occasion}",
            "{occasion} {category} in {print} print",
            "Casual {print} {category} for the weekend",
            "Sustainable {category} outfits",
            "Bold {print} pieces for {occasion}",
            "Shop {print} {category} under {price}",
            "Traditional {category} for a {occasion}",
            "Modern {print} {category} collection",
            "Find {category} perfect for {occasion}",
        ]

        import random
        random.shuffle(templates)

        for template in templates:
            if len(suggestions) >= 5:
                break
            try:
                suggestion = template.format(
                    print=random.choice(print_list) if print_list else "African print",
                    category=random.choice(cat_list) if cat_list else "outfits",
                    occasion=random.choice(occasion_list) if occasion_list else "special occasion",
                    price="USD " + str(random.choice([50, 100, 150, 200, 300])),
                )
                if suggestion not in suggestions:
                    suggestions.append(suggestion)
            except (IndexError, KeyError):
                continue

        if not suggestions:
            suggestions = DEFAULT_SUGGESTIONS

        return suggestions[:5]


class AiOutfitBuilderView(APIView):
    """
    POST /core/ai-outfit-builder
    Accepts a product ID or natural-language request and returns a curated
    outfit (primary item + complementary pieces) using actual DB relationships.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        product_id = request.data.get("product_id")
        query = request.data.get("query", "").strip().lower()
        user = request.user if request.user.is_authenticated else None

        # 1. Resolve primary product
        primary = None
        if product_id:
            try:
                primary = Product.objects.select_related("user__designer_profile", "category").get(id=product_id)
            except Product.DoesNotExist:
                return Response({"status": "error", "message": "Product not found."}, status=status.HTTP_404_NOT_FOUND)
        elif query:
            # Quick keyword search for primary
            primary = Product.objects.filter(
                Q(name__icontains=query) | Q(description__icontains=query)
            ).select_related("user__designer_profile", "category").annotate(
                avg_rating=Avg("reviews__rating", filter=Q(reviews__is_approved=True))
            ).order_by("-avg_rating").first()
            if not primary:
                return Response({"status": "error", "message": "No matching product found."}, status=status.HTTP_404_NOT_FOUND)
        else:
            return Response({"status": "error", "message": "Provide product_id or query."}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Find complementary categories
        complement_map = {
            "dress": ["shoes", "bag", "jewelry"],
            "top": ["bottom", "shoes", "bag"],
            "bottom": ["top", "shoes", "bag"],
            "shoes": ["dress", "bottom", "bag"],
            "bag": ["dress", "top", "bottom"],
            "jewelry": ["dress", "top"],
            "blazer": ["bottom", "shoes", "bag"],
            "skirt": ["top", "shoes", "bag"],
        }
        cat_name = (primary.category.name if primary.category else "").lower()
        target_complements = complement_map.get(cat_name, ["shoes", "bag", "jewelry"])

        # 3. Fetch complementary products
        complements = []
        for comp_cat in target_complements:
            qs = Product.objects.filter(
                category__name__icontains=comp_cat
            ).exclude(id=primary.id).annotate(
                avg_rating=Avg("reviews__rating", filter=Q(reviews__is_approved=True))
            )
            # Prioritize same designer, then by popularity/rating
            same_designer = qs.filter(user=primary.user).order_by("-avg_rating").first()
            if same_designer:
                complements.append(same_designer)
            else:
                top = qs.order_by("-popularity_score", "-avg_rating").first()
                if top:
                    complements.append(top)

        # 4. De-duplicate and limit
        seen = {primary.id}
        unique_complements = []
        for p in complements:
            if p.id not in seen:
                seen.add(p.id)
                unique_complements.append(p)
                if len(unique_complements) >= 3:
                    break

        # 5. Personalize if user is authenticated
        personalization_note = ""
        if user:
            lookbook_ids = Product.objects.filter(lookbooks__user=user).values_list("id", flat=True)
            review_designers = Review.objects.filter(customer__user=user).values_list("product__user", flat=True)
            if primary.user.id in review_designers:
                personalization_note = "You have loved this designer's work before."

        return Response({
            "status": "success",
            "data": {
                "primary": ProductSerializer(primary, context={"request": request}).data,
                "outfit": ProductSerializer(unique_complements, many=True, context={"request": request}).data,
                "style_note": personalization_note or f"Complete your look with these {', '.join(target_complements[:3])} picks.",
                "total_price": str(sum([p.price for p in unique_complements]) + primary.price),
            }
        }, status=status.HTTP_200_OK)


class AiTrendingView(APIView):
    """
    GET /core/ai-trending
    Returns live trending data and AI-generated prompts based on actual DB activity.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        since = timezone.now() - timedelta(days=7)

        # Top trending categories by views
        trending_cats = ProductView.objects.filter(
            created_at__gte=since
        ).values("product__category__name").annotate(
            view_count=Count("id")
        ).order_by("-view_count")[:5]

        # Top trending designers by analytics
        trending_designers = DesignerDailyAnalytics.objects.filter(
            date__gte=since.date()
        ).values("designer__brand_name").annotate(
            total_views=Sum("page_views")
        ).order_by("-total_views")[:5]

        # Best-selling products (highest views in last 7 days)
        hot_products = Product.objects.filter(
            view_events__created_at__gte=since
        ).annotate(
            recent_views=Count("view_events")
        ).order_by("-recent_views")[:6]

        # AI-generated prompts based on trending data
        gemini_key = getattr(settings, "GEMINI_API_KEY", None)
        suggestions = []
        if gemini_key:
            try:
                cat_context = ", ".join([c["product__category__name"] for c in trending_cats if c["product__category__name"]])
                designer_context = ", ".join([d["designer__brand_name"] for d in trending_designers if d["designer__brand_name"]])
                system_prompt = f"""You are Zuri, a creative fashion companion for Urbana Africa.
Generate 5 inspiring, natural-sounding search prompts based on current trends:
Trending categories: {cat_context}
Trending designers: {designer_context}
Respond ONLY with a valid JSON array of 5 strings.""" 
                client = genai.Client(api_key=gemini_key)
                response = client.models.generate_content(
                    model="gemini-1.5-flash-latest",
                    contents="Generate trending search prompts.",
                    config=genai.types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.9,
                    ),
                )
                raw = response.text.strip()
                if raw.startswith("["):
                    suggestions = json.loads(raw)
                elif "```" in raw:
                    suggestions = json.loads(raw.split("```")[1].strip("json").strip())
            except Exception:
                suggestions = []

        if not suggestions:
            suggestions = [
                f"Trending now: {c['product__category__name']}" for c in trending_cats[:3]
            ] + [
                f"Shop {d['designer__brand_name']}" for d in trending_designers[:2]
            ]

        return Response({
            "status": "success",
            "data": {
                "trending_categories": list(trending_cats),
                "trending_designers": list(trending_designers),
                "hot_products": ProductSerializer(hot_products, many=True, context={"request": request}).data,
                "suggestions": suggestions[:5],
            }
        }, status=status.HTTP_200_OK)


class AiSmartCollectionView(APIView):
    """
    POST /core/ai-smart-collection
    Auto-generates a SmartCollection from a user query + matched products.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        query = request.data.get("query", "").strip()
        product_ids = request.data.get("product_ids", [])
        if not query and not product_ids:
            return Response({"status": "error", "message": "Provide query or product_ids."}, status=status.HTTP_400_BAD_REQUEST)

        products = []
        if product_ids:
            products = list(Product.objects.filter(id__in=product_ids))
        else:
            products = list(Product.objects.filter(
                Q(name__icontains=query) |
                Q(description__icontains=query) |
                Q(category__name__icontains=query)
            )[:20])

        if not products:
            return Response({"status": "error", "message": "No products matched."}, status=status.HTTP_404_NOT_FOUND)

        collection = SmartCollection.objects.create(
            user=request.user,
            name=f"AI: {query.title() or 'Curated Picks'}",
            description=f"Auto-generated collection for '{query}'",
            auto_generated=True,
            query=query,
        )
        collection.products.set(products)

        return Response({
            "status": "success",
            "data": {
                "collection": SmartCollectionSerializer(collection, context={"request": request}).data,
                "matched_count": len(products),
            }
        }, status=status.HTTP_201_CREATED)


class AiPersonalizedSearchView(APIView):
    """
    POST /core/ai-personalized-search
    Enhanced AI search that factors in user's lookbook, reviews, and size recommendations.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        query = request.data.get("query", "").strip()
        if not query:
            return Response({"status": "error", "message": "Query is required."}, status=status.HTTP_400_BAD_REQUEST)

        # --- Support & Advice handling (bypass product search) ---
        exact_query = query.lower().strip("?. ")
        support_answers = {
            "help me track my last order": "To track your order, navigate to your Profile dashboard, click on 'Orders', and select your active order to see real-time shipping status and tracking updates.",
            "show custom tailors sizing guide": "Urbana provides a detailed Bespoke Measurement Vault under your Profile dashboard where you can store your parameters. You can also use our interactive camera FitMe tool for live visual pose alignment.",
            "how do i return a product": "To return a product, go to the 'Orders' list under your Profile dashboard, select the order, and click 'Return Item'. We accept returns within 14 days of delivery in original, unused condition.",
            "check my urbana wallet balance": "You can view your current Urbana Wallet balance and transaction history in the 'Wallet' tab of your Profile. You can also fund it directly using standard checkout gateways (Card, Transfer, or Mobile Money).",
        }
        advice_answers = {
            "suggest trending fashion styles": "Here are the hottest trends our community is loving right now:\n\n1. Modern Ankara Power Suits – Bold prints meet corporate chic.\n2. Adire Minimalist Dresses – Clean silhouettes with hand-dyed textures.\n3. Kente Statement Jackets – Layered over neutrals for maximum impact.\n4. Bogolan Streetwear – Earth-tone mud cloth paired with contemporary cuts.\n5. Sustainable Capsule Wardrobes – Mix-and-match pieces from eco-conscious designers.\n\nWould you like me to find specific pieces in any of these styles?",
            "what is trending": "This season's top trends include:\n\n• **Ankara Blazers** – Tailored outerwear with vibrant West-African prints.\n• **Adire Jumpsuits** – One-piece hand-dyed garments for effortless elegance.\n• **Kente Accents** – Woven strips on cuffs, collars, and accessories.\n• **Minimalist Neutral Sets** – Beige, cream, and clay tones for everyday luxury.\n• **Sustainable Linens** – Breathable, ethically sourced fabrics.\n\nTap any style above to see matching pieces.",
            "what should i wear": "I'd love to help you decide! Tell me more about the occasion (wedding, work, casual, party, traditional) and I'll curate the perfect outfit for you.",
        }

        if exact_query in support_answers:
            return Response(
                {"status": "success", "style_note": support_answers[exact_query], "data": []},
                status=status.HTTP_200_OK,
            )
        if exact_query in advice_answers:
            return Response(
                {"status": "success", "style_note": advice_answers[exact_query], "data": []},
                status=status.HTTP_200_OK,
            )

        # --- Gemini parsing for shopping vs off-topic classification ---
        gemini_key = getattr(settings, "GEMINI_SECRET_KEY", None)
        if gemini_key:
            user = request.user
            user_context = ""
            if user:
                parts = []
                if getattr(user, 'gender', ''):
                    parts.append(f"gender: {user.gender}")
                if getattr(user, 'height', ''):
                    parts.append(f"height: {user.height}")
                if getattr(user, 'size', ''):
                    parts.append(f"typical size: {user.size}")
                if parts:
                    user_context = f"User profile context (use if query doesn't contradict): {', '.join(parts)}.\n"
            try:
                parsed = _call_gemini(query, gemini_key, user_context)
                if parsed.get("is_support") is True:
                    return Response(
                        {"status": "success", "style_note": parsed.get("style_note"), "data": []},
                        status=status.HTTP_200_OK,
                    )
                # Use Gemini-extracted search keyword if available
                if parsed.get("search"):
                    query = parsed["search"]
            except Exception:
                pass  # Fall back to raw query text search

        user = request.user

        # 1. Gather user context
        lookbook_ids = set(Product.objects.filter(lookbooks__user=user).values_list("id", flat=True))
        review_designers = set(Review.objects.filter(customer__user=user).values_list("product__user", flat=True))
        size_recs = SizeRecommendation.objects.filter(user=user).first()
        user_sizes = set()
        if size_recs:
            user_sizes = set(filter(None, [size_recs.top_size, size_recs.bottom_size, size_recs.shoe_size]))

        MIN_RESULTS = 6

        # 2. Base search
        base_qs = Product.objects.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(category__name__icontains=query),
            stock__gt=0
        ).select_related("user__designer_profile", "category").prefetch_related("sizes").annotate(
            avg_rating=Avg("reviews__rating", filter=Q(reviews__is_approved=True))
        )

        def _score_products(products):
            scored = []
            for p in products:
                score = 0
                notes = []

                # Lookbook affinity
                if p.id in lookbook_ids:
                    score += 30
                    notes.append("You saved a similar item.")

                # Designer affinity
                if p.user_id in review_designers:
                    score += 25
                    brand_name = getattr(p.user.designer_profile, 'brand_name', 'this designer')
                    notes.append(f"You have reviewed {brand_name} positively.")

                # Size match (use prefetched sizes)
                if user_sizes:
                    for s in p.sizes.all():
                        if s.name and s.name in user_sizes:
                            score += 20
                            notes.append("Available in your size.")
                            break

                # Popularity
                score += min(p.popularity_score / 100, 15)
                score += p.avg_rating * 2 if p.avg_rating else 0

                scored.append({
                    "product": p,
                    "score": score,
                    "notes": notes,
                })
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored

        # 3. Score base results
        products = list(base_qs[:6])
        is_fallback = False

        # 4. Fallback to trending if base search yields too few results
        if len(products) < MIN_RESULTS:
            fallback_qs = Product.objects.filter(
                is_published=True, is_admin_published=True, is_active=True, stock__gt=0
            ).exclude(media=False).select_related("user__designer_profile", "category").prefetch_related("sizes").annotate(
                avg_rating=Avg("reviews__rating", filter=Q(reviews__is_approved=True))
            ).order_by("-popularity_score", "-created_at")[:6]

            # Exclude products already in base results to avoid duplicates
            existing_ids = {p.id for p in products}
            fallback_products = [p for p in fallback_qs if p.id not in existing_ids]

            # Combine base + fallback, prioritizing base results
            products = products + fallback_products
            is_fallback = True

        scored = _score_products(products)

        # 5. Build response
        results = []
        for item in scored[:6]:
            ser = ProductSerializer(item["product"], context={"request": request}).data
            ser["ai_score"] = item["score"]
            ser["ai_notes"] = item["notes"]
            results.append(ser)

        is_personalized = bool(lookbook_ids or review_designers or user_sizes)

        if is_fallback and not (lookbook_ids or review_designers or user_sizes):
            style_note = (
                "I couldn't find exact matches for that right now, "
                "but here are some popular pieces our community is loving."
            )
        elif is_fallback:
            style_note = (
                "I couldn't find exact matches for that right now, "
                "but here are some personalized picks based on your profile."
            )
        else:
            style_note = (
                "Here are personalized recommendations based on your style profile."
                if is_personalized
                else "Here are matching pieces from our collection."
            )

        return Response({
            "status": "success",
            "style_note": style_note,
            "data": results,
            "personalized": is_personalized,
            "is_fallback": is_fallback,
        }, status=status.HTTP_200_OK)


class AiPhotoFitMeView(APIView):
    """POST /core/ai-photo-fitme — analyze user photo + product, return fit report.

    Fast path: size recommendation and fit score are computed locally without
    calling Gemini. Gemini is used only for the narrative text analysis, with a
    short timeout so the user never waits for AI text if the model is slow.
    """
    permission_classes = [AllowAny]

    @staticmethod
    def _compute_fast_fit(product):
        """Compute recommended size and fit score from product data only."""
        size_names = [s.name for s in product.sizes.all() if s.name]
        if not size_names:
            return {"recommended_size": "One Size", "fit_score": 85}

        # Pick middle size as the safest default recommendation
        sorted_sizes = sorted(size_names)
        recommended = sorted_sizes[len(sorted_sizes) // 2]

        # Fit score: more sizes available = higher confidence
        base_score = 70
        size_bonus = min(len(size_names) * 4, 20)
        material_bonus = 5 if product.material else 0
        desc_bonus = 5 if product.description else 0
        fit_score = min(base_score + size_bonus + material_bonus + desc_bonus, 98)

        return {"recommended_size": recommended, "fit_score": fit_score}

    @staticmethod
    def _build_fallback_analysis(product, recommended_size):
        """Build a basic analysis when Gemini is unavailable or slow."""
        category = product.category.name if product.category else "this item"
        material = product.material or "quality fabric"
        return (
            f"This {category} is crafted from {material}. "
            f"We recommend size {recommended_size} based on standard measurements. "
            f"For the best fit, compare your bust/waist/hip measurements to the designer's size chart."
        )

    def post(self, request):
        product_id = request.data.get("product_id")
        photo = request.FILES.get("user_photo")
        if not photo or not product_id:
            return Response({"status": "error", "message": "Photo and product_id required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            product = Product.objects.prefetch_related("sizes").get(id=product_id)
        except Product.DoesNotExist:
            return Response({"status": "error", "message": "Product not found"}, status=status.HTTP_404_NOT_FOUND)
        except (ValueError, TypeError):
            return Response({"status": "error", "message": "Invalid product_id"}, status=status.HTTP_400_BAD_REQUEST)

        # ── Fast path: compute fit data locally (sub-100ms) ──
        fast_data = self._compute_fast_fit(product)
        size_names = [s.name for s in product.sizes.all() if s.name]
        sizes_text = ", ".join(size_names) if size_names else "One size"

        # ── Try Gemini for narrative text (best-effort, 5s timeout) ──
        gemini_key = getattr(settings, "GEMINI_SECRET_KEY", None)
        analysis_text = None
        if gemini_key:
            try:
                client = genai.Client(api_key=gemini_key)
                photo.seek(0)
                photo_bytes = photo.read()
                mime = photo.content_type or "image/jpeg"

                prompt = f"""You are Zuri, a personal fashion companion for Urbana Africa.
Analyze the user's body in the uploaded photo and the product details below.

Product: {product.name}
Category: {product.category.name if product.category else 'N/A'}
Price: ₦{product.price}
Sizes available: {sizes_text}
Material: {product.material or 'N/A'}
Description: {product.description or 'N/A'}

Provide:
1. A detailed fit analysis (body type assessment, how the product would fit, style advice, color matching notes)
2. Recommended size from the available sizes
3. A fit confidence score (0-100)

Respond ONLY with valid JSON in this exact structure:
{{"analysis": "detailed text", "recommended_size": "M", "fit_score": 87}}
"""
                from google.genai import types
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        client.models.generate_content,
                        model="gemini-1.5-flash-latest",
                        contents=[
                            types.Part.from_bytes(data=photo_bytes, mime_type=mime),
                            prompt,
                        ],
                        config=types.GenerateContentConfig(temperature=0.2, response_mime_type="application/json"),
                    )
                    try:
                        response = future.result(timeout=5)
                        parsed = json.loads(response.text)
                        analysis_text = parsed.get("analysis")
                        # Prefer Gemini's size/score if it provided them, else keep fast values
                        if parsed.get("recommended_size"):
                            fast_data["recommended_size"] = parsed["recommended_size"]
                        if parsed.get("fit_score") is not None:
                            fast_data["fit_score"] = parsed["fit_score"]
                    except concurrent.futures.TimeoutError:
                        pass  # Fall back to locally computed data
            except Exception:
                pass  # Silently fall back to fast data on any Gemini error

        if not analysis_text:
            analysis_text = self._build_fallback_analysis(product, fast_data["recommended_size"])

        fast_data["analysis"] = analysis_text
        return Response({"status": "success", "data": fast_data}, status=status.HTTP_200_OK)


class TryOnProvidersView(APIView):
    """GET /core/tryon-providers — list available generative try-on models.

    Returns each provider with an ``enabled`` flag so the frontend can render a
    temporary model selector. During testing only Gemini is enabled.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        from .services.vton import list_providers, DEFAULT_PROVIDER
        return Response(
            {"status": "success", "data": list_providers(), "default": DEFAULT_PROVIDER},
            status=status.HTTP_200_OK,
        )


class AiTryOnView(APIView):
    """POST /core/ai-tryon — generative garment replacement.

    Takes the user's photo + a product and returns a photorealistic image of the
    user wearing the product's garment, using the selected VTON provider.
    """
    permission_classes = [AllowAny]

    @staticmethod
    def _read_garment(product):
        """Return (bytes, mime) for the product's garment image."""
        field = getattr(product, "fit_me_image", None)
        if field:
            try:
                field.open("rb")
                data = field.read()
                field.close()
                if data:
                    return data, "image/png"
            except Exception:
                pass
        media = product.media.first()
        if media and getattr(media, "file", None):
            try:
                media.file.open("rb")
                data = media.file.read()
                media.file.close()
                if data:
                    return data, "image/jpeg"
            except Exception:
                pass
        return None, None

    def post(self, request):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        from .services.vton import get_provider, list_providers, VtonError
        import uuid

        product_id = request.data.get("product_id")
        provider_key = request.data.get("provider") or "gemini"
        photo = request.FILES.get("user_photo")

        if not photo or not product_id:
            return Response(
                {"status": "error", "message": "Photo and product_id required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Guard: only enabled + configured providers may run.
        providers = {p["key"]: p for p in list_providers()}
        meta = providers.get(provider_key)
        if not meta:
            return Response(
                {"status": "error", "message": "Unknown try-on model."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not meta["enabled"]:
            return Response(
                {"status": "error", "message": f"{meta['label']} is currently disabled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            product = Product.objects.prefetch_related("media", "sizes").get(id=product_id)
        except Product.DoesNotExist:
            return Response({"status": "error", "message": "Product not found"}, status=status.HTTP_404_NOT_FOUND)
        except (ValueError, TypeError):
            return Response({"status": "error", "message": "Invalid product_id"}, status=status.HTTP_400_BAD_REQUEST)

        garment_bytes, garment_mime = self._read_garment(product)
        if not garment_bytes:
            return Response(
                {"status": "error", "message": "This product has no try-on image."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        photo.seek(0)
        person_bytes = photo.read()
        person_mime = photo.content_type or "image/jpeg"

        provider = get_provider(provider_key)
        try:
            result_bytes = provider.generate(
                person_bytes, person_mime, garment_bytes, garment_mime, product
            )
        except VtonError as exc:
            return Response(
                {"status": "error", "message": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            return Response(
                {"status": "error", "message": f"Try-on failed: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Persist the generated image and return its URL.
        name = f"ai_tryon/{uuid.uuid4().hex}.png"
        path = default_storage.save(name, ContentFile(result_bytes))
        url = request.build_absolute_uri(default_storage.url(path))

        return Response(
            {
                "status": "success",
                "data": {"image_url": url, "provider": provider_key},
            },
            status=status.HTTP_200_OK,
        )


class SubscriptionPlanListView(APIView):
    """GET /core/subscription-plans — list all active plans."""
    permission_classes = [AllowAny]

    def get(self, request):
        plans = SubscriptionPlan.objects.filter(is_active=True)
        serializer = SubscriptionPlanSerializer(plans, many=True)
        return Response({"status": "success", "data": serializer.data}, status=status.HTTP_200_OK)


class UserSubscriptionView(APIView):
    """GET /core/my-subscription — current user's subscription."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            sub = request.user.subscription
            serializer = UserSubscriptionSerializer(sub)
            return Response({"status": "success", "data": serializer.data}, status=status.HTTP_200_OK)
        except UserSubscription.DoesNotExist:
            return Response({"status": "success", "data": None}, status=status.HTTP_200_OK)
