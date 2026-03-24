from django.utils import timezone
from django.contrib import admin
from .models import Newsletter, NewsletterSubscriber


@admin.register(Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ('title', 'subject', 'is_draft', 'sent_at', 'created_by')
    list_filter = ('is_draft', 'sent_at')
    search_fields = ('title', 'subject', 'content')
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ('sent_at', 'created_at', 'updated_at')
    date_hierarchy = 'sent_at'


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'full_name', 'is_active', 'subscribed_at', )
    list_filter = ('is_active',)
    search_fields = ('email', 'full_name')
    readonly_fields = ('subscribed_at', 'unsubscribed_at')
    actions = ['make_inactive']

    def make_inactive(self, request, queryset):
        queryset.update(is_active=False, unsubscribed_at=timezone.now())
    make_inactive.short_description = "Mark selected subscribers as unsubscribed"