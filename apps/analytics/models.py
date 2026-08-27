from django.db import models
from django.conf import settings
from apps.utils.uuid_generator import generate_custom_id


class EventSchema(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(max_length=100, unique=True)
    version = models.CharField(max_length=10, default='1.0')
    required_params = models.JSONField(default=list, blank=True)
    optional_params = models.JSONField(default=list, blank=True)
    pii_params = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Event Schema'
        verbose_name_plural = 'Event Schemas'
        unique_together = ['name', 'version']

    def __str__(self):
        return f'{self.name} v{self.version}'


class Identity(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    anon_id = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='analytics_identities'
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    visit_count = models.PositiveIntegerField(default=0)
    first_utm = models.JSONField(default=dict, blank=True)
    last_utm = models.JSONField(default=dict, blank=True)
    first_landing_path = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        verbose_name = 'Identity'
        verbose_name_plural = 'Identities'

    def __str__(self):
        return f'{self.anon_id} ({self.visit_count} visits)'


class Session(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    session_id = models.CharField(max_length=64, unique=True, db_index=True)
    anon_id = models.CharField(max_length=64, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='analytics_sessions'
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    event_count = models.PositiveIntegerField(default=0)
    pageview_count = models.PositiveIntegerField(default=0)
    entry_path = models.CharField(max_length=500, blank=True, default='')
    exit_path = models.CharField(max_length=500, blank=True, default='')
    utm = models.JSONField(default=dict, blank=True)
    device_type = models.CharField(max_length=50, blank=True, default='')
    country_code = models.CharField(max_length=10, blank=True, default='')
    converted = models.BooleanField(default=False)
    revenue = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    class Meta:
        verbose_name = 'Session'
        verbose_name_plural = 'Sessions'
        indexes = [
            models.Index(fields=['anon_id', 'started_at']),
        ]

    def __str__(self):
        return f'{self.session_id} ({self.event_count} events)'


class Event(models.Model):
    SOURCE_CHOICES = (
        ('web', 'Web'),
        ('customer_app', 'Customer App'),
        ('designer_app', 'Designer App'),
        ('admin', 'Admin'),
        ('server', 'Server'),
        ('webhook', 'Webhook'),
        ('mobile', 'Mobile'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    event_id = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=100, db_index=True)
    schema_version = models.CharField(max_length=10, default='1.0')
    anon_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='analytics_events'
    )
    session_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    occurred_at = models.DateTimeField(db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='web')
    page_path = models.CharField(max_length=500, blank=True, default='')
    referrer = models.URLField(blank=True, default='')
    utm_source = models.CharField(max_length=100, blank=True, default='')
    utm_medium = models.CharField(max_length=100, blank=True, default='')
    utm_campaign = models.CharField(max_length=100, blank=True, default='')
    utm_term = models.CharField(max_length=100, blank=True, default='')
    utm_content = models.CharField(max_length=100, blank=True, default='')
    device_type = models.CharField(max_length=50, blank=True, default='')
    os = models.CharField(max_length=100, blank=True, default='')
    browser = models.CharField(max_length=100, blank=True, default='')
    country_code = models.CharField(max_length=10, blank=True, default='')
    region = models.CharField(max_length=100, blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    consent_analytics = models.BooleanField(default=False)
    consent_marketing = models.BooleanField(default=False)
    props = models.JSONField(default=dict, blank=True)
    items = models.JSONField(default=list, blank=True)
    revenue = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default='')
    transaction_id = models.CharField(max_length=255, blank=True, default='', db_index=True)
    is_bot = models.BooleanField(default=False)
    is_valid = models.BooleanField(default=True)
    invalid_reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        verbose_name = 'Event'
        verbose_name_plural = 'Events'
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['name', 'received_at']),
            models.Index(fields=['anon_id', 'received_at']),
            models.Index(fields=['session_id', 'received_at']),
            models.Index(fields=['transaction_id', 'received_at']),
        ]

    def __str__(self):
        return f'{self.name} ({self.occurred_at})'


class DailyMetric(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    date = models.DateField()
    metric_name = models.CharField(max_length=100, db_index=True)
    dimension_key = models.CharField(max_length=100, blank=True, default='')
    dimension_value = models.CharField(max_length=255, blank=True, default='')
    value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Daily Metric'
        verbose_name_plural = 'Daily Metrics'
        unique_together = ['date', 'metric_name', 'dimension_key', 'dimension_value']
        indexes = [
            models.Index(fields=['metric_name', 'date']),
        ]

    def __str__(self):
        return f'{self.metric_name} {self.date} = {self.value}'


class FunnelSnapshot(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(max_length=100)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Funnel Snapshot'
        verbose_name_plural = 'Funnel Snapshots'

    def __str__(self):
        return f'{self.name} ({self.created_at})'


class CohortSnapshot(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(max_length=100)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Cohort Snapshot'
        verbose_name_plural = 'Cohort Snapshots'

    def __str__(self):
        return f'{self.name} ({self.created_at})'


class MetricDefinition(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(max_length=100, unique=True)
    formula = models.TextField()
    grain = models.CharField(max_length=50, default='daily')
    window = models.CharField(max_length=50, default='30d')
    owner = models.CharField(max_length=255, blank=True, default='')
    is_input_metric = models.BooleanField(default=False)
    target = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True)
    description = models.TextField(blank=True, default='')
    source_table = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Metric Definition'
        verbose_name_plural = 'Metric Definitions'

    def __str__(self):
        return self.name


class MetricAlert(models.Model):
    RULE_CHOICES = (
        ('z_score', 'Z-Score'),
        ('threshold', 'Threshold'),
        ('pct_change', 'Percent Change'),
    )
    CHANNEL_CHOICES = (
        ('email', 'Email'),
        ('slack', 'Slack'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    metric = models.ForeignKey(MetricDefinition, on_delete=models.CASCADE, related_name='alerts')
    rule = models.CharField(max_length=20, choices=RULE_CHOICES)
    window = models.CharField(max_length=50, default='1h')
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    threshold = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True)
    last_fired_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Metric Alert'
        verbose_name_plural = 'Metric Alerts'

    def __str__(self):
        return f'{self.metric.name} {self.rule}'


class DeadLetterEvent(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    payload = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Dead Letter Event'
        verbose_name_plural = 'Dead Letter Events'

    def __str__(self):
        return f'{self.reason} ({self.created_at})'
