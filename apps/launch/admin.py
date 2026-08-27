from django.contrib import admin
from .models import LaunchConfig, WaitlistSubscriber, WaitlistEvent, LaunchCampaign, EmailSuppression


@admin.register(LaunchConfig)
class LaunchConfigAdmin(admin.ModelAdmin):
    list_display = ['name', 'gate_enabled', 'capture_mode', 'updated_at', 'updated_by']
    fields = [
        'name', 'gate_enabled', 'capture_mode', 'headline', 'subhead',
        'cta_label', 'trust_line', 'hero_image_path', 'hero_image_alt',
        'show_signup_counter', 'counter_offset', 'referral_enabled',
        'referral_spots_per_ref', 'double_optin_enabled', 'designer_cta_enabled',
        'designer_cta_label', 'designer_cta_url', 'allowed_paths',
        'consent_text', 'consent_version'
    ]


@admin.register(WaitlistSubscriber)
class WaitlistSubscriberAdmin(admin.ModelAdmin):
    list_display = ['email', 'status', 'position', 'referral_code', 'referral_count', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['email', 'referral_code']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(WaitlistEvent)
class WaitlistEventAdmin(admin.ModelAdmin):
    list_display = ['subscriber', 'event_type', 'created_at']
    list_filter = ['event_type', 'created_at']


@admin.register(LaunchCampaign)
class LaunchCampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'status', 'scheduled_at', 'sent_at', 'created_at']
    list_filter = ['status']


@admin.register(EmailSuppression)
class EmailSuppressionAdmin(admin.ModelAdmin):
    list_display = ['email', 'reason', 'created_at']
    search_fields = ['email']
