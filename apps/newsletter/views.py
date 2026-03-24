# newsletters/views.py
from rest_framework import status, generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Newsletter, NewsletterSubscriber
from .serializers import (
    NewsletterSerializer,
    SubscribeSerializer,
)


class NewsletterListAPIView(generics.ListAPIView):
    """
    GET /api/newsletters/
    Returns all published newsletters (public endpoint)
    Perfect for Refresh Ghana users to browse past editions
    """
    queryset = Newsletter.objects.filter(is_draft=False).order_by('-sent_at')
    serializer_class = NewsletterSerializer
    permission_classes = [permissions.AllowAny]


class NewsletterDetailAPIView(generics.RetrieveAPIView):
    """
    GET /api/newsletters/<slug>/
    Single newsletter detail (public)
    """
    queryset = Newsletter.objects.filter(is_draft=False)
    serializer_class = NewsletterSerializer
    lookup_field = 'slug'
    permission_classes = [permissions.AllowAny]


class NewsletterSubscribeAPIView(APIView):
    """
    POST /api/newsletters/subscribe/
    Subscribe a user (or business owner) to the newsletter
    Works even if they previously unsubscribed
    """
    permission_classes = ([permissions.AllowAny])
    authentication_classes=()

    def post(self, request):
        serializer = SubscribeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email']
        full_name = serializer.validated_data.get('full_name', '')

        subscriber, created = NewsletterSubscriber.objects.get_or_create(
            email=email,
            defaults={
                'full_name': full_name,
                'is_active': True
            }
        )

        if not created:
            # Already exists → reactivate if unsubscribed
            if not subscriber.is_active:
                subscriber.is_active = True
                subscriber.unsubscribed_at = None
                subscriber.full_name = full_name or subscriber.full_name
                subscriber.save()

        return Response({
            "message": "Successfully subscribed to Refresh Ghana Newsletter!",
            "email": email,
            "status": "active"
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class NewsletterUnsubscribeAPIView(APIView):
    """
    POST /api/newsletters/unsubscribe/
    Unsubscribe using email (secure & simple for API)
    Used by unsubscribe links in emails or mobile app
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            subscriber = NewsletterSubscriber.objects.get(email=email, is_active=True)
            subscriber.unsubscribe()
            return Response({
                "message": "You have been successfully unsubscribed from Refresh Ghana Newsletter.",
                "email": email
            }, status=status.HTTP_200_OK)
        except NewsletterSubscriber.DoesNotExist:
            return Response({
                "message": "No active subscription found for this email."
            }, status=status.HTTP_200_OK)