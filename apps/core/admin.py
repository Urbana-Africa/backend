from django.contrib import admin

from apps.core.models import Category, Product, ShippingMethod, Sizes

# Register your models here.


admin.site.register([Product,Category,ShippingMethod, Sizes])