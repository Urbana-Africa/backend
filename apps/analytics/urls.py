from django.urls import path
from .views import CollectView

urlpatterns = [
    path('collect', CollectView.as_view(), name='analytics-collect'),
]
