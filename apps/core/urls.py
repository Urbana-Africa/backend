# core/urls.py
from django.conf import settings
from django.urls import path
from .views import (
    CollectionDetailView,
    CountryListView,
    CurrencyListView,
    DesignerDetailView,
    DesignerListView,
    FeaturedProductsView,
    MediaAssetUploadView,
    ProductDetailView,
    SizesListView,
    StoryListView,
    StoryDetailView,
    FeaturedStoriesView,
    TrendingCollectionsView,
    UserSettingsView,
)
from django.urls import path
from .views import (
    CountryListView, CurrencyListView, MediaAssetUploadView,
    CategoryListView, ProductListView, ProductDetailView,
    ReviewCreateView
)
from django.conf.urls.static import static

urlpatterns = [
    # 🌍 General
    path("countries", CountryListView.as_view(), name="core-countries"),
    path("currencies", CurrencyListView.as_view(), name="core-currencies"),

    # 🖼 Media
    path("media/upload", MediaAssetUploadView.as_view(), name="core-media-upload"),

    # 🏷 Categories
    path("categories", CategoryListView.as_view(), name="core-categories"),

    # 🛍 Products
    path("products", ProductListView.as_view(), name="core-products"),
    path("products/<str:id>", ProductDetailView.as_view(),
         name="core-product-detail"),
   path("settings", UserSettingsView.as_view()),
    # ✍ Reviews
    path("reviews/create", ReviewCreateView.as_view(), name="core-review-create"),

    # 🌍 General Resources
    path("countries", CountryListView.as_view(), name="core-countries"),
    path("currencies", CurrencyListView.as_view(), name="core-currencies"),
    path("sizes", SizesListView.as_view(), name="core-sizes"),

    # 🖼 Media Management
    path("media/upload", MediaAssetUploadView.as_view(), name="core-media-upload"),

    # ✨ Storytelling Endpoints
    path("stories", StoryListView.as_view(), name="core-stories"),
    path("stories/featured", FeaturedStoriesView.as_view(),
         name="core-stories-featured"),
    path("stories/<uuid:pk>", StoryDetailView.as_view(), name="core-story-detail"),
    path('designers', DesignerListView.as_view(), name='designer-list'),
#     path('products', DesignerProductListView.as_view(), name='designer-products'),
    path('products/featured', FeaturedProductsView.as_view(),
         name='featured-products'),
    path('<slug:slug>', DesignerDetailView.as_view(), name='designer-detail'),
    path('collection/<slug:slug>', CollectionDetailView.as_view(),
         name='designer-collection-detail'),
    path('product/<int:product_id>',
         ProductDetailView.as_view(), name='product-detail'),
    path('collections/trending', TrendingCollectionsView.as_view(),
         name='trending-collections'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
 
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)