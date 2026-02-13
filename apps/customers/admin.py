from django.contrib import admin

from apps.customers.models import Address, Order, OrderItem, CartItem

# Register your models here.


admin.site.register([OrderItem, Order, CartItem, Address])