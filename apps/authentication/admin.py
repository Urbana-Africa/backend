from django.contrib import admin
from .models import User

# @admin.register(User)
# class UserAdmin(admin.ModelAdmin):
#     list_display = ('username', 'email', 'user_type', 'country', 'is_verified')
#     search_fields = ('username', 'email', 'phone_number')

admin.site.register([User])