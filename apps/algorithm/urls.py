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
    # NOTE: /core/track is registered in apps/core/urls.py (TrackEventsView).
    # The algorithm TrackEventView was shadowed dead code and has been removed
    # from this URL config. The core view now feeds SessionIntentEngine directly.
    path("feed", FeedView.as_view(), name="algo-feed"),
    path("trending", TrendingView.as_view(), name="algo-trending"),
    path("recommendations", RecommendationsView.as_view(), name="algo-recommendations"),
    path("user-profile", UserProfileView.as_view(), name="algo-user-profile"),
]
