from rest_framework import serializers
from .models import LaunchConfig, WaitlistSubscriber, WaitlistEvent, LaunchCampaign, EmailSuppression


class LaunchConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = LaunchConfig
        fields = [
            'gate_enabled', 'capture_mode', 'headline', 'subhead',
            'cta_label', 'trust_line', 'hero_image_path', 'hero_image_alt',
            'show_signup_counter', 'counter_offset', 'referral_enabled',
            'referral_spots_per_ref', 'double_optin_enabled', 'designer_cta_enabled',
            'designer_cta_label', 'designer_cta_url', 'allowed_paths',
            'consent_text', 'consent_version'
        ]


class WaitlistCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    consent_marketing = serializers.BooleanField(required=True)
    consent_version = serializers.CharField(required=True)
    referral_code = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='', max_length=32)
    source = serializers.CharField(required=False, allow_blank=True, max_length=100)
    medium = serializers.CharField(required=False, allow_blank=True, max_length=100)
    campaign = serializers.CharField(required=False, allow_blank=True, max_length=100)
    term = serializers.CharField(required=False, allow_blank=True, max_length=100)
    content = serializers.CharField(required=False, allow_blank=True, max_length=100)
    landing_path = serializers.CharField(required=False, allow_blank=True, max_length=255)
    referrer_url = serializers.URLField(required=False, allow_blank=True)
    anon_id = serializers.CharField(required=False, allow_blank=True, max_length=64)

    def validate_email(self, value):
        return value.lower().strip()

    def validate_consent_marketing(self, value):
        if not value:
            raise serializers.ValidationError('Marketing consent is required to join the waitlist.')
        return value


class WaitlistResponseSerializer(serializers.ModelSerializer):
    referral_url = serializers.SerializerMethodField()

    class Meta:
        model = WaitlistSubscriber
        fields = [
            'id', 'email', 'status', 'position', 'referral_code',
            'referral_url', 'created_at'
        ]

    def get_referral_url(self, obj):
        store_url = 'https://www.urbanaafrica.com'
        return f'{store_url}/waitlist?ref={obj.referral_code}'


class WaitlistMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WaitlistSubscriber
        fields = ['position', 'referral_count', 'spots_skipped', 'referral_code']


class WaitlistConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(required=True, max_length=128)


class WaitlistUnsubscribeSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True, max_length=128)
    email = serializers.EmailField(required=False, allow_blank=True)


class WaitlistStatsSerializer(serializers.Serializer):
    confirmed_count = serializers.IntegerField()
    counter_offset = serializers.IntegerField()
    total = serializers.IntegerField()


class WaitlistEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = WaitlistEvent
        fields = ['id', 'event_type', 'metadata', 'created_at']


class LaunchCampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = LaunchCampaign
        fields = [
            'id', 'name', 'subject', 'html_body', 'preview_text',
            'segment_query', 'status', 'scheduled_at', 'sent_at', 'stats', 'created_at'
        ]
        read_only_fields = ['status', 'sent_at', 'stats']


class EmailSuppressionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailSuppression
        fields = ['id', 'email', 'reason', 'notes', 'created_at']


class WaitlistSubscriberAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = WaitlistSubscriber
        fields = '__all__'

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['referred_by_email'] = instance.referred_by.email if instance.referred_by else None
        data['utm_source'] = instance.source
        data['ip_address'] = instance.consent_ip or instance.anon_id or ''
        return data
