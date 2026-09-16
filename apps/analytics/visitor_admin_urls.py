from django.urls import path

from . import visitor_admin_views as v

# Mounted under /manage/analytics/visitors/ (see apps.administrator.urls).
urlpatterns = [
    path('overview', v.VisitorOverviewView.as_view(), name='visitor-overview'),
    path('pages', v.VisitorPagesView.as_view(), name='visitor-pages'),
    path('events', v.VisitorEventsView.as_view(), name='visitor-events'),
    path('funnel', v.VisitorFunnelView.as_view(), name='visitor-funnel'),
    path('page-detail', v.VisitorPageDetailView.as_view(), name='visitor-page-detail'),
    path('event-detail', v.VisitorEventDetailView.as_view(), name='visitor-event-detail'),
    path('designers', v.VisitorDesignersView.as_view(), name='visitor-designers'),
    path('sessions', v.VisitorSessionsView.as_view(), name='visitor-sessions'),
    path('session-detail', v.VisitorSessionDetailView.as_view(), name='visitor-session-detail'),
    path('referrers', v.VisitorReferrersView.as_view(), name='visitor-referrers'),
]
