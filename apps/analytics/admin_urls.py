from django.urls import path
from .views import (
    KPIView,
    MetricCatalogView,
    SummaryView,
    FunnelView,
    RetentionView,
    SupplyView,
    OpsView,
    PlatformHealthView,
    SearchInsightsView,
)

urlpatterns = [
    path('kpis', KPIView.as_view(), name='analytics-kpis'),
    path('summary', SummaryView.as_view(), name='analytics-summary'),
    path('funnel', FunnelView.as_view(), name='analytics-funnel'),
    path('retention', RetentionView.as_view(), name='analytics-retention'),
    path('supply', SupplyView.as_view(), name='analytics-supply'),
    path('ops', OpsView.as_view(), name='analytics-ops'),
    path('health', PlatformHealthView.as_view(), name='analytics-health'),
    path('search', SearchInsightsView.as_view(), name='analytics-search'),
    path('metrics/catalog', MetricCatalogView.as_view(), name='analytics-metric-catalog'),
]
