from django.urls import path
from .views import CollectView
from .tracking import TrackView

urlpatterns = [
    path('collect', CollectView.as_view(), name='analytics-collect'),
    path('track', TrackView.as_view(), name='analytics-track'),
]
