from django.contrib import admin

from apps.designers.models import DesignerOrder, Designer

# Register your models here.


admin.site.register([DesignerOrder, Designer])