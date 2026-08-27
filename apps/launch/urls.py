from django.urls import path
from . import views

urlpatterns = [
    path('config', views.LaunchConfigView.as_view(), name='launch-config'),
    path('waitlist', views.WaitlistCreateView.as_view(), name='waitlist-create'),
    path('waitlist/confirm', views.WaitlistConfirmView.as_view(), name='waitlist-confirm'),
    path('waitlist/me', views.WaitlistMeView.as_view(), name='waitlist-me'),
    path('waitlist/unsubscribe', views.WaitlistUnsubscribeView.as_view(), name='waitlist-unsubscribe'),
    path('stats/public', views.WaitlistStatsView.as_view(), name='waitlist-stats-public'),
    path('designer-cta', views.DesignerCtaBeaconView.as_view(), name='designer-cta-beacon'),
    path('webhooks/resend', views.ResendWebhookView.as_view(), name='resend-webhook'),
    path('r/<str:referral_code>', views.ReferralRedirectView.as_view(), name='waitlist-referral-redirect'),
    path('t/<str:token>', views.EmailClickView.as_view(), name='waitlist-email-click'),
]
