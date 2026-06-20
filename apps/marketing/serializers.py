from rest_framework import serializers
from .models import DesignerLead, EmailTemplate, EmailCampaign, EmailLog

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
