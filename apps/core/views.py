import threading
from django.core.paginator import Paginator
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Avg, Prefetch
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.designers.models import DesignerStory
from apps.designers.serializers import StorySerializer
from apps.utils.email_sender import resend_sendmail
from django.db.models.functions import Lower
from .models import Country, Currency, Category, Product, Review, Sizes, UserSettings
from .serializers import (
    ContactMessageSerializer, CountrySerializer, CurrencySerializer, MediaAssetSerializer,
    CategorySerializer, ProductSerializer, ReviewSerializer, SizesSerializer, UserSettingsSerializer
)
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

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
        ordering = request.GET.get('ordering', 'user__username')
        if designer_id:
            designer = Designer.objects.get(slug=designer_id)
            serializer = DesignerSerializer(designer)
            return Response({
                "status":"success",
                "message": "Designer retrieved successfully.",
                "data": serializer.data
            }, status=status.HTTP_200_OK)
        designers = Designer.objects.all()
        print(designers)
        if search:
            designers = designers.filter(user__username__icontains=search)
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
class MediaAssetUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = MediaAssetSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({"status": "success", "media": serializer.data}, status=status.HTTP_201_CREATED)
        return Response({"status": "error", "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------
# Categories
# ---------------------------
class CategoryListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        categories = Category.objects.filter(is_active=True)
        serializer = CategorySerializer(categories, many=True)
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
class ReviewCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ReviewSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(customer=request.user.customer)  # assuming user has related Customer
            return Response({"status": "success", "review": serializer.data}, status=status.HTTP_201_CREATED)
        return Response({"status": "error", "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)




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
            # user=request.user if request.user.is_authenticated else None
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
            return Response([])

        names = (
            Product.objects
            .filter(name__icontains=query)
            .annotate(lower_name=Lower("name"))
            .order_by("lower_name")
            .values_list("name", flat=True)
            .distinct()[:6]
        )

        return Response({'data':list(names)})




# class ProductViewSet(ModelViewSet):
#     queryset = Product.objects.all()
#     serializer_class = ProductSerializer
#     filter_backends = [SearchFilter]
#     search_fields = ["name"]