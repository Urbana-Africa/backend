from django.db import models
from apps.utils.uuid_generator import generate_custom_id

class DesignerLead(models.Model):
    STATUS_CHOICES = (
        ('Discovered', 'Discovered'),
        ('Contacted', 'Contacted'),
        ('In Discussion', 'In Discussion'),
        ('Signed Up', 'Signed Up'),
        ('Rejected', 'Rejected'),
    )

    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    brand_name = models.CharField(max_length=255)
    designer_name = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=50, blank=True, null=True)
    social_media_links = models.JSONField(default=dict, blank=True, null=True)
    followers_count = models.IntegerField(default=0, blank=True, null=True)
    category_tags = models.JSONField(default=list, blank=True, null=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Discovered')
    source = models.CharField(max_length=100, help_text="e.g., Instagram API, Web Scraper", blank=True, null=True)
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
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    name = models.CharField(max_length=255)
    template = models.ForeignKey(EmailTemplate, on_delete=models.SET_NULL, null=True)
    target_leads = models.ManyToManyField(DesignerLead, related_name='campaigns')
    is_active = models.BooleanField(default=False)
    date_created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class EmailLog(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    campaign = models.ForeignKey(EmailCampaign, on_delete=models.SET_NULL, null=True, blank=True)
    lead = models.ForeignKey(DesignerLead, on_delete=models.CASCADE)
    subject = models.CharField(max_length=255)
    status = models.CharField(max_length=50, choices=(('Sent', 'Sent'), ('Failed', 'Failed'), ('Opened', 'Opened')))
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"To: {self.lead.email} - Status: {self.status}"
