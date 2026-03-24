from django.db import models
from django.conf import settings
from django.utils import timezone


class Newsletter(models.Model):
    """
    Main newsletter model - each edition/issue of the newsletter
    """
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    
    subject = models.CharField(max_length=255, help_text="Email subject line")
    preview_text = models.CharField(
        max_length=150, 
        blank=True,
        help_text="Preview text shown in email clients (max 150 chars)"
    )
    
    content = models.TextField(
        help_text="HTML content of the newsletter"
    )
    
    is_draft = models.BooleanField(default=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='newsletters_created'
    )
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['sent_at']),
        ]

    def __str__(self):
        return f"{self.title} ({'Sent' if self.sent_at else 'Draft'})"

    def mark_as_sent(self):
        self.is_draft = False
        self.sent_at = timezone.now()
        self.save(update_fields=['is_draft', 'sent_at'])


class NewsletterSubscriber(models.Model):
    """
    People who subscribed to receive newsletters (e.g. business owners)
    """
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
        
    class Meta:
        ordering = ['-subscribed_at']

    def __str__(self):
        return self.email

    def unsubscribe(self):
        self.is_active = False
        self.unsubscribed_at = timezone.now()
        self.save(update_fields=['is_active', 'unsubscribed_at'])