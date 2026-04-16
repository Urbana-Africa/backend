from django.urls import path
from .views import (
    CheckoutView, CustomerProfileView, AddressView, OrderDetailView, OrderTrackingView,
    ShippingMethodListView, WishlistView, CartView,
    OrderListView, ReturnRequestView, ReturnResolveView, ReturnDetailView, DisputeView
)

urlpatterns = [
    path('profile', CustomerProfileView.as_view(), name='customer-profile'),
    path('addresses', AddressView.as_view(), name='customer-addresses'),
    path('wishlist', WishlistView.as_view(), name='customer-wishlist'),
    path('cart', CartView.as_view(), name='customer-cart'),
    path('orders', OrderListView.as_view(), name='customer-orders'),
    path('returns', ReturnRequestView.as_view(), name='customer-returns'),
    path('returns/<str:return_id>/resolve', ReturnResolveView.as_view(), name='return-resolve'),
    path('returns/<str:return_id>', ReturnDetailView.as_view(), name='return-detail'),
    path('disputes', DisputeView.as_view(), name='customer-disputes'),
    path('orderdetail', OrderDetailView.as_view()),
    path('shipping-methods', ShippingMethodListView.as_view(), name='shipping-methods'),
    path('orders/<str:order_id>/tracking', OrderTrackingView.as_view(), name='order-tracking'),
    path('checkout', CheckoutView.as_view(), name='checkout'),
]
