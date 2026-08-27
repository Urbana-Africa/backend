from django.contrib import admin
from .models import (
    Event,
    EventSchema,
    Identity,
    Session,
    DailyMetric,
    FunnelSnapshot,
    CohortSnapshot,
    MetricDefinition,
    MetricAlert,
    DeadLetterEvent,
)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['name', 'source', 'anon_id', 'received_at', 'is_valid']
    list_filter = ['name', 'source', 'is_valid', 'consent_analytics']
    search_fields = ['name', 'anon_id', 'session_id', 'transaction_id']


@admin.register(EventSchema)
class EventSchemaAdmin(admin.ModelAdmin):
    list_display = ['name', 'version', 'enabled']
    list_filter = ['enabled']


@admin.register(DeadLetterEvent)
class DeadLetterEventAdmin(admin.ModelAdmin):
    list_display = ['reason', 'created_at']
    list_filter = ['reason']


@admin.register(MetricDefinition)
class MetricDefinitionAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'is_input_metric', 'target']


@admin.register(MetricAlert)
class MetricAlertAdmin(admin.ModelAdmin):
    list_display = ['metric', 'rule', 'channel', 'is_active']


admin.site.register(Identity)
admin.site.register(Session)
admin.site.register(DailyMetric)
admin.site.register(FunnelSnapshot)
admin.site.register(CohortSnapshot)
