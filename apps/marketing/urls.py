from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DesignerLeadViewSet,
    EmailTemplateViewSet,
    EmailCampaignViewSet,
    EmailLogViewSet,
    ScrapeProviderConfigViewSet,
    ScrapeJobViewSet,
    ScrapeCallViewSet,
    scrape_leads_placeholder,
    funnel_stats,
)

router = DefaultRouter()
router.register(r'leads', DesignerLeadViewSet)
router.register(r'templates', EmailTemplateViewSet)
router.register(r'campaigns', EmailCampaignViewSet)
router.register(r'logs', EmailLogViewSet)
router.register(r'scrape-providers', ScrapeProviderConfigViewSet)
router.register(r'scrape-jobs', ScrapeJobViewSet)
router.register(r'scrape-calls', ScrapeCallViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('scrape/', scrape_leads_placeholder, name='scrape-leads'),
    path('funnel-stats/', funnel_stats, name='funnel-stats'),
]
