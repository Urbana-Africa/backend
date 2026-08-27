from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .admin_views import (
    LaunchConfigAdminView,
    WaitlistSubscriberAdminViewSet,
    LaunchCampaignAdminViewSet,
    WaitlistAnalyticsView,
    DSARView,
)

router = DefaultRouter(trailing_slash=False)
router.register(r'waitlist', WaitlistSubscriberAdminViewSet, basename='admin-waitlist')
router.register(r'campaigns', LaunchCampaignAdminViewSet, basename='admin-launch-campaigns')

urlpatterns = [
    path('config', LaunchConfigAdminView.as_view(), name='admin-launch-config'),
    path('analytics', WaitlistAnalyticsView.as_view(), name='admin-launch-analytics'),
    path('dsar', DSARView.as_view(), name='admin-dsar'),
] + router.urls
