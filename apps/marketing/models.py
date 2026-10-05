from django.db import models
from django.conf import settings
from apps.utils.uuid_generator import generate_custom_id

class DesignerLead(models.Model):
    STATUS_CHOICES = (
        ('Discovered', 'Discovered'),
        ('Needs Review', 'Needs Review'),
        ('Qualified', 'Qualified'),
        ('Assigned', 'Assigned'),
        ('Contacted', 'Contacted'),
        ('Replied', 'Replied'),
        ('Meeting', 'Meeting'),
        ('Applied', 'Applied'),
        ('Approved', 'Approved'),
        ('Activated', 'Activated'),
        # Legacy stages kept for historical rows
        ('In Discussion', 'In Discussion'),
        ('Signed Up', 'Signed Up'),
        ('Rejected', 'Rejected'),
        ('Suppressed', 'Suppressed'),
    )

    # What kind of account this lead actually is. Directory/community/
    # aggregator/generic accounts must never become outreach targets.
    QUALIFICATION_CHOICES = (
        ('unclassified', 'Unclassified'),
        ('direct_designer', 'Direct Designer'),
        ('directory', 'Directory'),
        ('community', 'Community / Group'),
        ('aggregator', 'Aggregator / Ranking'),
        ('agency', 'Agency'),
        ('generic_account', 'Generic Account'),
        ('uncertain', 'Uncertain'),
        ('not_a_lead', 'Not a Lead'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    brand_name = models.CharField(max_length=255)
    designer_name = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=50, blank=True, null=True)
    social_media_links = models.JSONField(default=dict, blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    instagram_handle = models.CharField(max_length=100, blank=True, null=True)
    country_code = models.CharField(max_length=10, blank=True, null=True)
    followers_count = models.IntegerField(default=0, blank=True, null=True)
    category_tags = models.JSONField(default=list, blank=True, null=True)
    confidence_score = models.FloatField(default=0.0)
    provenance = models.JSONField(default=dict, blank=True, null=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Discovered')
    source = models.CharField(max_length=100, help_text="e.g., Instagram API, Web Scraper", blank=True, null=True)
    needs_review = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_designer_leads'
    )
    qualification_type = models.CharField(
        max_length=30, choices=QUALIFICATION_CHOICES,
        default='unclassified', db_index=True,
    )
    qualification_reason = models.TextField(blank=True, default='')
    qualified_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_designer_leads'
    )
    merged_into = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='merged_duplicates',
        help_text="Set when this duplicate was consolidated into another lead.",
    )
    dedupe_key = models.CharField(max_length=255, blank=True, null=True, unique=True, db_index=True)
    last_enriched_at = models.DateTimeField(null=True, blank=True)
    converted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='converted_designer_lead'
    )
    date_discovered = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.brand_name} ({self.status})"

class EmailTemplate(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    html_body = models.TextField()
    date_created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class EmailCampaign(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('sending', 'Sending'),
        ('paused', 'Paused'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(max_length=255)
    template = models.ForeignKey(EmailTemplate, on_delete=models.SET_NULL, null=True)
    target_leads = models.ManyToManyField(DesignerLead, related_name='campaigns', blank=True)
    is_active = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    # Snapshot of message content so broadcasts and ad-hoc campaigns can send
    # without a stored template; falls back to template fields when blank.
    subject = models.CharField(max_length=255, blank=True, default='')
    html_body = models.TextField(blank=True, default='')
    # Segment definition used when target_leads is empty, e.g. {"search": "lagos"}.
    # Empty filter = all qualified leads.
    audience_filter = models.JSONField(default=dict, blank=True)
    send_cap = models.PositiveIntegerField(
        default=0,
        help_text="Maximum successful sends. 0 = no cap.",
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_campaigns',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_campaigns',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    date_created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class EmailLog(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    campaign = models.ForeignKey(EmailCampaign, on_delete=models.SET_NULL, null=True, blank=True)
    lead = models.ForeignKey(DesignerLead, on_delete=models.CASCADE)
    subject = models.CharField(max_length=255)
    status = models.CharField(max_length=50, choices=(
        ('Sent', 'Sent'), ('Failed', 'Failed'), ('Opened', 'Opened'),
        ('Suppressed', 'Suppressed'), ('Skipped', 'Skipped'),
    ))
    reason = models.CharField(max_length=255, blank=True, default='')
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"To: {self.lead.email} - Status: {self.status}"


class ScrapeProviderConfig(models.Model):
    """Global or per-provider configuration for third-party scrapers."""
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(max_length=50, unique=True, help_text="Provider key, e.g. dataforseo")
    enabled = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True, help_text="API keys, zones, actor IDs")
    priority = models.PositiveSmallIntegerField(default=100)
    cost_per_1k_credits = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    monthly_budget = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    monthly_spend = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['priority', 'name']

    def __str__(self):
        return f"{self.name} — enabled: {self.enabled}"


class ScrapeJob(models.Model):
    STATUS_CHOICES = (
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('budget_stopped', 'Budget Stopped'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    query = models.CharField(max_length=500)
    provider_name = models.CharField(max_length=50, blank=True, default='')
    max_results = models.PositiveIntegerField(default=5)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scrape_jobs'
    )
    result_summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.query[:40]} — {self.status}"


class ScrapeCall(models.Model):
    """One API call to a third-party provider."""
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    job = models.ForeignKey(ScrapeJob, on_delete=models.CASCADE, related_name='calls')
    provider_name = models.CharField(max_length=50)
    call_type = models.CharField(max_length=20, choices=(('search', 'Search'), ('extract', 'Extract'), ('llm', 'LLM Parse')))
    input_url = models.URLField(blank=True, default='')
    cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    status = models.CharField(max_length=20, choices=(('ok', 'OK'), ('error', 'Error'), ('timeout', 'Timeout')))
    raw_payload = models.JSONField(default=dict, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default='')
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ScrapeCache(models.Model):
    """Cached raw extraction keyed by URL to avoid re-scraping the same page."""
    url_hash = models.CharField(max_length=64, unique=True, db_index=True)
    url = models.URLField()
    provider_name = models.CharField(max_length=50)
    extracted = models.JSONField(default=dict, blank=True)
    stale_after = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class LeadEnrichment(models.Model):
    """Extra raw data + confidence for a lead discovered by a scraper."""
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    lead = models.OneToOneField(DesignerLead, on_delete=models.CASCADE, related_name='enrichment')
    job = models.ForeignKey(ScrapeJob, on_delete=models.SET_NULL, null=True, blank=True, related_name='enrichments')
    raw_text = models.TextField(blank=True, default='')
    raw_html = models.TextField(blank=True, default='')
    extraction_source = models.CharField(max_length=50, blank=True, default='')
    confidence = models.FloatField(default=0.0)
    enrichment_status = models.CharField(max_length=20, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class LeadSuppression(models.Model):
    """Leads that should never be scraped or emailed again."""
    REASON_CHOICES = (
        ('manual', 'Manual'),
        ('unsubscribe', 'Unsubscribe'),
        ('bounce', 'Bounce'),
        ('complaint', 'Complaint'),
        ('rejected', 'Rejected'),
        ('request', 'Data Subject Request'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    email = models.EmailField(blank=True, default='', db_index=True)
    domain = models.CharField(max_length=255, blank=True, default='', db_index=True)
    handle = models.CharField(max_length=100, blank=True, default='', db_index=True)
    brand_name = models.CharField(max_length=255, db_index=True)
    reason = models.CharField(max_length=50, choices=REASON_CHOICES, default='manual')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lead_suppressions'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['brand_name', 'domain']]


class LeadQualificationDecision(models.Model):
    """Append-only audit of automated and human qualification outcomes.

    ``lead`` is null for candidates rejected at ingestion time (before a
    DesignerLead row exists); ``candidate_*`` fields preserve what was seen.
    """
    DECISION_CHOICES = DesignerLead.QUALIFICATION_CHOICES[1:] + (
        ('qualified', 'Qualified (human)'),
        ('rejected', 'Rejected (human)'),
        ('suppressed', 'Suppressed (human)'),
        ('merged', 'Merged (human)'),
    )
    DECIDED_BY_CHOICES = (
        ('rules', 'Rule classifier'),
        ('llm', 'LLM'),
        ('human', 'Human'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    lead = models.ForeignKey(
        DesignerLead, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='qualification_decisions',
    )
    job = models.ForeignKey(
        'ScrapeJob', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='qualification_decisions',
    )
    candidate_name = models.CharField(max_length=255, blank=True, default='')
    candidate_url = models.URLField(blank=True, default='')
    decision = models.CharField(max_length=30, choices=DECISION_CHOICES)
    decided_by = models.CharField(max_length=20, choices=DECIDED_BY_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_qualification_decisions',
    )
    reasons = models.JSONField(default=list, blank=True)
    confidence = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        target = self.lead_id or self.candidate_name or self.candidate_url
        return f"{target}: {self.decision} ({self.decided_by})"
