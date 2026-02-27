from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter(trailing_slash=False)
# =====================================================
# CUSTOMER MANAGEMENT
# =====================================================
router.register(r"customers", AdminCustomerViewSet, basename="admin-customers")
router.register(r"addresses", AdminAddressViewSet, basename="admin-addresses")
router.register(r"wishlists", AdminWishlistViewSet, basename="admin-wishlists")
router.register(r"cart-items", AdminCartItemViewSet, basename="admin-cart-items")

# =====================================================
# PRODUCT & CATALOG
# =====================================================
router.register(r"products", AdminProductViewSet, basename="admin-products")
router.register(r"categories", AdminCategoryViewSet, basename="admin-categories")
router.register(r"brands", AdminBrandViewSet, basename="admin-brands")
router.register(r"currencies", AdminCurrencyViewSet, basename="admin-currencies")
router.register(r"sizes", AdminSizesViewSet, basename="admin-sizes")
router.register(r"media-assets", AdminMediaAssetViewSet, basename="admin-media")
router.register(r"reviews", AdminReviewViewSet, basename="admin-reviews")
router.register(r"shipping-methods", AdminShippingMethodViewSet, basename="admin-shipping-methods")
router.register(r"countries", AdminCountryViewSet, basename="admin-countries")

# =====================================================
# ORDER MANAGEMENT
# =====================================================
router.register(r"orders", AdminOrderViewSet, basename="admin-orders")
router.register(r"order-items", AdminOrderItemViewSet, basename="admin-order-items")
router.register(r"order-tracking", AdminOrderTrackingViewSet, basename="admin-order-tracking")

# =====================================================
# RETURN MANAGEMENT
# =====================================================
router.register(r"returns", AdminReturnRequestViewSet, basename="admin-returns")

# =====================================================
# DESIGNER MANAGEMENT
# =====================================================
router.register(r"designers", AdminDesignerViewSet, basename="admin-designers")
router.register(r"designer-products", AdminDesignerProductViewSet, basename="admin-designer-products")
router.register(r"collections", AdminCollectionViewSet, basename="admin-collections")
router.register(r"designer-analytics", AdminDesignerAnalyticsViewSet, basename="admin-designer-analytics")
router.register(r"designer-shipping-options", AdminShippingOptionViewSet, basename="admin-designer-shipping-options")
router.register(r"designer-orders", AdminDesignerOrderViewSet, basename="admin-designer-orders")
router.register(r"designer-shipments", AdminShipmentTrackingViewSet, basename="admin-designer-shipments")
router.register(r"inventory-alerts", AdminInventoryAlertViewSet, basename="admin-inventory-alerts")
router.register(r"promotions", AdminPromotionViewSet, basename="admin-promotions")

urlpatterns = router.urls