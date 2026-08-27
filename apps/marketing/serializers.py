from rest_framework import serializers
from .models import (
    DesignerLead,
    EmailTemplate,
    EmailCampaign,
    EmailLog,
    ScrapeProviderConfig,
    ScrapeJob,
    ScrapeCall,
)

class DesignerLeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = DesignerLead
        fields = '__all__'

class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = '__all__'

class EmailCampaignSerializer(serializers.ModelSerializer):
    target_leads_details = DesignerLeadSerializer(source='target_leads', many=True, read_only=True)
    
    class Meta:
        model = EmailCampaign
        fields = '__all__'

class EmailLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailLog
        fields = '__all__'


class ScrapeProviderConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeProviderConfig
        fields = ['id', 'name', 'enabled', 'config', 'priority', 'cost_per_1k_credits', 'monthly_budget', 'monthly_spend', 'created_at', 'updated_at']


class ScrapeJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeJob
        fields = '__all__'


class ScrapeCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeCall
        fields = '__all__'
