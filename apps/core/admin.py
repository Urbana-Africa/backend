from django.contrib import admin

from apps.core.models import (
    Category, Product, ShippingMethod, Sizes, MediaAsset,
    Review, SmartCollection, ProductView, DesignerDailyAnalytics,
    LoyaltyPoints, LoyaltyBalance, SizeRecommendation, UserLookbook,
)

# Register your models here.


admin.site.register([
    Product, Category, ShippingMethod, Sizes, MediaAsset,
    Review, SmartCollection, ProductView, DesignerDailyAnalytics,
    LoyaltyPoints, LoyaltyBalance, SizeRecommendation, UserLookbook,
])