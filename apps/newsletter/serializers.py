# newsletters/serializers.py
from rest_framework import serializers
from .models import Newsletter, NewsletterSubscriber


class NewsletterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Newsletter
        fields = [
            'id', 'title', 'slug', 'subject', 'preview_text',
            'content', 'sent_at', 'created_at'
        ]
        read_only_fields = ['slug', 'sent_at', 'created_at']


class NewsletterSubscriberSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsletterSubscriber
        fields = ['email', 'full_name', 'business']
        extra_kwargs = {
            'email': {'required': True},
        }


class SubscribeSerializer(serializers.Serializer):
    """Used only for the subscribe endpoint"""
    email = serializers.EmailField(required=True)
    full_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    business_id = serializers.IntegerField(required=False, allow_null=True)  # optional link to your Business model