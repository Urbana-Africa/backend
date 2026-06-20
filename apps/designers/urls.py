from rest_framework.routers import DefaultRouter

from django.urls import path
from .views import (
    # ViewSets
    DesignerStoryViewSet,
    DesignerProductUploadViewSet,
    DesignerOrderViewSet,
    DesignerProfileViewSet,
    DesignerDashboardViewSet,
    DesignerReturnRequestViewSet,
    NotificationViewSet,
    DesignerSearchView,
    PromotionViewSet,
)

router = DefaultRouter(trailing_slash=False)

# -----------------------------
# Designer APIs
# -----------------------------
router.register(r"stories", DesignerStoryViewSet, basename="designer-stories")
router.register(r"products", DesignerProductUploadViewSet, basename="designer-products")
router.register(r"orders", DesignerOrderViewSet, basename="designer-orders")
router.register(r"profile", DesignerProfileViewSet, basename="designer-profile")
router.register(r"dashboard", DesignerDashboardViewSet, basename="designer-dashboard")
router.register(r"returns", DesignerReturnRequestViewSet, basename="designer-returns")
router.register(r"notifications", NotificationViewSet, basename="designer-notifications")
router.register(r"promotions", PromotionViewSet, basename="designer-promotions")


urlpatterns = router.urls + [
    path('search', DesignerSearchView.as_view(), name='designer-search'),
]