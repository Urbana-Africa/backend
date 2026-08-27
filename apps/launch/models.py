import secrets
import string
from django.db import models, transaction
from django.conf import settings
from django.utils import timezone

from apps.utils.uuid_generator import generate_custom_id


def _generate_token(length=64):
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


def _generate_referral_code(length=8):
    return ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(length))


def _next_waitlist_position():
    from django.db.models import Max
    result = WaitlistSubscriber.objects.aggregate(m=Max('position'))
    return (result['m'] or 0) + 1


class LaunchConfig(models.Model):
    CAPTURE_MODE_CHOICES = (
        ('email_only', 'Email only'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(default='default', max_length=50, unique=True)
    gate_enabled = models.BooleanField(default=False)
    capture_mode = models.CharField(max_length=20, choices=CAPTURE_MODE_CHOICES, default='email_only')
    headline = models.CharField(max_length=255, default='African fashion is launching soon.')
    subhead = models.CharField(max_length=255, default='Be the first to discover and shop designs from African creators.')
    cta_label = models.CharField(max_length=100, default='Get early access')
    trust_line = models.CharField(max_length=255, default="We'll email you once at launch. Nothing else.")
    hero_image_path = models.CharField(max_length=255, default='/images/dresses.jpg')
    hero_image_alt = models.CharField(max_length=255, default='African fashion dresses')
    show_signup_counter = models.BooleanField(default=True)
    counter_offset = models.PositiveIntegerField(default=0)
    referral_enabled = models.BooleanField(default=True)
    referral_spots_per_ref = models.PositiveSmallIntegerField(default=10)
    double_optin_enabled = models.BooleanField(default=True)
    designer_cta_enabled = models.BooleanField(default=True)
    designer_cta_label = models.CharField(max_length=255, default='Are you a designer? Apply to sell on Urbana')
    designer_cta_url = models.URLField(default=settings.DESIGNER_URL)
    allowed_paths = models.JSONField(default=list, blank=True)
    consent_text = models.TextField(
        default='I want to be notified by email when Urbana launches and agree to the Privacy Policy.'
    )
    consent_version = models.CharField(max_length=20, default='1.0')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='launch_config_updates'
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Launch Config'
        verbose_name_plural = 'Launch Config'

    def __str__(self):
        return f'Launch Config ({self.name}) — gate: {self.gate_enabled}'

    @classmethod
    def get(cls):
        defaults = {
            'gate_enabled': False,
            'capture_mode': 'email_only',
            'headline': 'African fashion is launching soon.',
            'subhead': 'Be the first to discover and shop designs from African creators.',
            'cta_label': 'Get early access',
            'trust_line': "We'll email you once at launch. Nothing else.",
            'hero_image_path': '/images/dresses.jpg',
            'hero_image_alt': 'African fashion dresses',
            'show_signup_counter': True,
            'counter_offset': 0,
            'referral_enabled': True,
            'referral_spots_per_ref': 10,
            'double_optin_enabled': True,
            'designer_cta_enabled': True,
            'designer_cta_label': 'Are you a designer? Apply to sell on Urbana',
            'designer_cta_url': settings.DESIGNER_URL,
            'allowed_paths': [
                '/privacy-policy', '/terms-of-service', '/contact-us',
                '/our-story', '/about-us', '/help-center', '/waitlist'
            ],
            'consent_text': 'I want to be notified by email when Urbana launches and agree to the Privacy Policy.',
            'consent_version': '1.0',
        }
        config, _ = cls.objects.get_or_create(name='default', defaults=defaults)
        return config

    def is_allowed(self, path):
        return any(path.startswith(a) for a in (self.allowed_paths or []))


class WaitlistSubscriber(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('unsubscribed', 'Unsubscribed'),
        ('bounced', 'Bounced'),
        ('complained', 'Complained'),
        ('converted', 'Converted'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True, default='')
    country_code = models.CharField(max_length=10, blank=True, default='')
    interests = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    confirm_token = models.CharField(max_length=128, blank=True, default='')
    confirm_token_expires_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unsubscribe_token = models.CharField(max_length=128, blank=True, default='')

    position = models.PositiveIntegerField(unique=True, db_index=True)
    referral_code = models.CharField(max_length=32, unique=True, db_index=True)
    referred_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='referrals')
    referral_count = models.PositiveIntegerField(default=0)
    spots_skipped = models.PositiveIntegerField(default=0)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='waitlist'
    )

    consent_marketing = models.BooleanField(default=False)
    consent_version = models.CharField(max_length=20, blank=True, default='')
    consent_at = models.DateTimeField(null=True, blank=True)
    consent_ip = models.GenericIPAddressField(null=True, blank=True)
    consent_user_agent = models.TextField(blank=True, default='')

    source = models.CharField(max_length=100, blank=True, default='')
    medium = models.CharField(max_length=100, blank=True, default='')
    campaign = models.CharField(max_length=100, blank=True, default='')
    term = models.CharField(max_length=100, blank=True, default='')
    content = models.CharField(max_length=100, blank=True, default='')
    landing_path = models.CharField(max_length=255, blank=True, default='')
    referrer_url = models.URLField(blank=True, default='')

    anon_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    visit_count = models.PositiveIntegerField(default=0)
    returned_after_email_at = models.DateTimeField(null=True, blank=True)

    notified_at = models.DateTimeField(null=True, blank=True)
    first_order_at = models.DateTimeField(null=True, blank=True)

    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Waitlist Subscriber'
        verbose_name_plural = 'Waitlist Subscribers'
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['anon_id', 'created_at']),
            models.Index(fields=['referral_code']),
        ]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if not self.confirm_token:
            self.confirm_token = _generate_token()
        if not self.confirm_token_expires_at:
            self.confirm_token_expires_at = timezone.now() + timezone.timedelta(hours=48)
        if not self.unsubscribe_token:
            self.unsubscribe_token = _generate_token()
        if not self.referral_code:
            self.referral_code = _generate_referral_code()
            while WaitlistSubscriber.objects.filter(referral_code=self.referral_code).exists():
                self.referral_code = _generate_referral_code()
        if not self.position:
            self.position = _next_waitlist_position()
        super().save(*args, **kwargs)

    def confirm(self):
        if self.status != 'pending':
            return False
        self.status = 'confirmed'
        self.confirmed_at = timezone.now()
        self.save(update_fields=['status', 'confirmed_at'])
        return True

    def apply_referral(self, referrer):
        """Set the referrer on signup; do NOT credit the count until this subscriber confirms."""
        if referrer and referrer.id != self.id:
            self.referred_by = referrer
            self.save(update_fields=['referred_by'])

    def confirm_referral(self):
        """Credit the referring user once this subscriber confirms."""
        if self.referred_by and self.status == 'confirmed' and self.spots_skipped == 0:
            referrer = self.referred_by
            with transaction.atomic():
                # refresh count to avoid races
                from django.db.models import F
                WaitlistSubscriber.objects.filter(pk=referrer.pk).update(
                    referral_count=models.F('referral_count') + 1
                )
                referrer.refresh_from_db()
                self.spots_skipped = min(
                    referrer.referral_count * LaunchConfig.get().referral_spots_per_ref,
                    self.position - 1
                )
                self.save(update_fields=['spots_skipped'])


class WaitlistEvent(models.Model):
    EVENT_CHOICES = (
        ('submitted', 'Submitted'),
        ('confirm_email_sent', 'Confirm Email Sent'),
        ('confirmed', 'Confirmed'),
        ('referral_shared', 'Referral Shared'),
        ('referred_signup', 'Referred Signup'),
        ('email_sent', 'Email Sent'),
        ('email_opened', 'Email Opened'),
        ('email_clicked', 'Email Clicked'),
        ('returned_to_site', 'Returned to Site'),
        ('designer_cta_clicked', 'Designer CTA Clicked'),
        ('unsubscribed', 'Unsubscribed'),
        ('bounced', 'Bounced'),
        ('complained', 'Complained'),
        ('converted', 'Converted'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    subscriber = models.ForeignKey(
        WaitlistSubscriber,
        on_delete=models.CASCADE,
        related_name='events',
        null=True,
        blank=True
    )
    event_type = models.CharField(max_length=30, choices=EVENT_CHOICES)
    campaign = models.ForeignKey(
        'LaunchCampaign',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events'
    )
    metadata = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Waitlist Event'
        verbose_name_plural = 'Waitlist Events'


class LaunchCampaign(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('cancelled', 'Cancelled'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    html_body = models.TextField()
    preview_text = models.CharField(max_length=150, blank=True, default='')
    segment_query = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='launch_campaigns'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Launch Campaign'
        verbose_name_plural = 'Launch Campaigns'

    def __str__(self):
        return self.name


class EmailSuppression(models.Model):
    REASON_CHOICES = (
        ('unsubscribed', 'Unsubscribed'),
        ('bounced', 'Bounced'),
        ('complained', 'Complained'),
        ('manual', 'Manual'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    email = models.EmailField(unique=True, db_index=True)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Email Suppression'
        verbose_name_plural = 'Email Suppressions'

    def __str__(self):
        return f'{self.email} — {self.reason}'
