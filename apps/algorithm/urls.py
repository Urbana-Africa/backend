from django.urls import path
from apps.algorithm.views import (
    FeedView,
    RecommendationsView,
    TrackEventView,
    TrendingView,
    UserProfileView,
)

urlpatterns = [
    # Public / Customer
    path("track", TrackEventView.as_view(), name="algo-track"),
    path("feed", FeedView.as_view(), name="algo-feed"),
    path("trending", TrendingView.as_view(), name="algo-trending"),
    path("recommendations", RecommendationsView.as_view(), name="algo-recommendations"),
    path("user-profile", UserProfileView.as_view(), name="algo-user-profile"),
]
