from rest_framework import serializers
from .models import (
    DesignerLead,
    EmailTemplate,
    EmailCampaign,
    EmailLog,
    LeadQualificationDecision,
    LeadSuppression,
    ScrapeProviderConfig,
    ScrapeJob,
    ScrapeCall,
)

class DesignerLeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = DesignerLead
        fields = '__all__'
        read_only_fields = (
            'qualification_type', 'qualification_reason', 'qualified_at',
            'reviewed_by', 'assigned_to', 'converted_user',
            'dedupe_key', 'provenance', 'confidence_score',
            'date_discovered', 'date_updated', 'last_enriched_at',
        )

class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = '__all__'

class EmailCampaignSerializer(serializers.ModelSerializer):
    target_leads_details = DesignerLeadSerializer(source='target_leads', many=True, read_only=True)
    template_name = serializers.CharField(source='template.name', read_only=True, default=None)

    class Meta:
        model = EmailCampaign
        fields = '__all__'
        read_only_fields = (
            'status', 'created_by', 'approved_by', 'approved_at',
            'sent_count', 'failed_count', 'skipped_count',
        )

class LeadSuppressionSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadSuppression
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')

class LeadQualificationDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadQualificationDecision
        fields = '__all__'

class EmailLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailLog
        fields = '__all__'


class ScrapeProviderConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeProviderConfig
        fields = ['id', 'name', 'enabled', 'config', 'priority', 'cost_per_1k_credits', 'monthly_budget', 'monthly_spend', 'created_at', 'updated_at']
        # config holds API keys/passwords — writable but never served back out
        extra_kwargs = {'config': {'write_only': True}}


class ScrapeJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeJob
        fields = '__all__'


class ScrapeCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeCall
        fields = '__all__'
