from rest_framework.routers import DefaultRouter

from .views import (
    # ViewSets
    DesignerStoryViewSet,
    DesignerProductUploadViewSet,
    DesignerOrderViewSet,
    DesignerProfileViewSet,
    DesignerDashboardViewSet,
    DesignerReturnRequestViewSet,
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


urlpatterns = router.urls