# core/urls.py
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CollectionDetailView,
    ContactMessageView,
    CountryListView,
    CurrencyListView,
    DesignerListView,
    FeaturedProductsView,
    MediaAssetDeleteView,
    MediaAssetUploadView,
    MediaAssetViewSet,
    ProductDetailView,
    SearchSuggestions,
    SizesListView,
    StoryListView,
    StoryDetailView,
    FeaturedStoriesView,
    TrendingCollectionsView,
    TrendingProducts,
    UserSettingsView,
    CategoryListView,
    ProductListView,
    ReviewCreateView,
    ReviewListView,
    SeedDummyDataView,
)

# -------------------------------
# DRF Router
# -------------------------------
router = DefaultRouter(trailing_slash=False)
router.register(r"media", MediaAssetViewSet, basename="media")  # /media/<id>

# -------------------------------
# URL Patterns
# -------------------------------
urlpatterns = [
    # 🌍 Router URLs first (MediaAsset CRUD)
] + router.urls

# -------------------------------
# API Paths
# -------------------------------
urlpatterns += [
    # 🌍 General
    path("countries", CountryListView.as_view(), name="core-countries"),
    path("currencies", CurrencyListView.as_view(), name="core-currencies"),
    path("search-suggestions", SearchSuggestions.as_view(), name="core-search-suggestions"),
    path("sizes", SizesListView.as_view(), name="core-sizes"),

    # 🖼 Media
    path("media/upload", MediaAssetUploadView.as_view(), name="core-media-upload"),  # separate upload endpoint
    path("media/<str:id>/delete", MediaAssetDeleteView.as_view(), name="core-media-delete"),

    # 🏷 Categories
    path("categories", CategoryListView.as_view(), name="core-categories"),
    
    # 🧪 Seeding
    path("seed-dummy-data", SeedDummyDataView.as_view(), name="seed-dummy-data"),
    
    # ✉ Contact
    path("contact", ContactMessageView.as_view(), name="contact-message"),

    # 🛍 Products
    path("products", ProductListView.as_view(), name="core-products"),
    path("trending-products", TrendingProducts.as_view(), name="trending-products"),
    path("products/<str:id>", ProductDetailView.as_view(), name="core-product-detail"),
    path("products/featured", FeaturedProductsView.as_view(), name="featured-products"),
    path("product/<int:product_id>", ProductDetailView.as_view(), name="product-detail"),

    # ✍ Reviews
    path("reviews", ReviewListView.as_view(), name="core-reviews"),
    path("reviews/create", ReviewCreateView.as_view(), name="core-review-create"),

    # 🖼 Storytelling Endpoints
    path("stories", StoryListView.as_view(), name="core-stories"),
    path("stories/featured", FeaturedStoriesView.as_view(), name="core-stories-featured"),
    path("stories/<uuid:pk>", StoryDetailView.as_view(), name="core-story-detail"),

    # 🏢 Designers
    path("designers", DesignerListView.as_view(), name="designer-list"),
    path("settings", UserSettingsView.as_view(), name="user-settings"),
    path("collection/<slug:slug>", CollectionDetailView.as_view(), name="designer-collection-detail"),
    path("collections/trending", TrendingCollectionsView.as_view(), name="trending-collections"),
    # path("<slug:slug>", DesignerDetailView.as_view(), name="designer-detail"),

    # ⚙ User Settings
]

# -------------------------------
# Static / Media
# -------------------------------
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)