# newsletters/urls.py
from django.urls import path
from .views import (
    NewsletterListAPIView,
    NewsletterDetailAPIView,
    NewsletterSubscribeAPIView,
    NewsletterUnsubscribeAPIView,
)

app_name = 'newsletters'

urlpatterns = [
    # Public newsletter browsing (perfect for business directory users)
    path('', NewsletterListAPIView.as_view(), name='newsletter-list'),
    path('subscribe', NewsletterSubscribeAPIView.as_view(), name='newsletter-subscribe'),
    path('<slug:slug>', NewsletterDetailAPIView.as_view(), name='newsletter-detail'),
    
    # Subscription management
    path('unsubscribe', NewsletterUnsubscribeAPIView.as_view(), name='newsletter-unsubscribe'),
]