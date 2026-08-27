from django.contrib import admin
from .models import (
    DesignerLead,
    EmailTemplate,
    EmailCampaign,
    EmailLog,
    ScrapeProviderConfig,
    ScrapeJob,
    ScrapeCall,
    ScrapeCache,
    LeadEnrichment,
    LeadSuppression,
)

admin.site.register(ScrapeProviderConfig)
admin.site.register(ScrapeJob)
admin.site.register(ScrapeCall)
admin.site.register(ScrapeCache)
admin.site.register(LeadEnrichment)
admin.site.register(LeadSuppression)
