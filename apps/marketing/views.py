from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models import DesignerLead, EmailTemplate, EmailCampaign, EmailLog
from .serializers import DesignerLeadSerializer, EmailTemplateSerializer, EmailCampaignSerializer, EmailLogSerializer
from apps.administrator.permissions import IsMarketer
from django.db.models import Count

class DesignerLeadViewSet(viewsets.ModelViewSet):
    queryset = DesignerLead.objects.all().order_by('-date_discovered')
    serializer_class = DesignerLeadSerializer
    permission_classes = [IsMarketer]

class EmailTemplateViewSet(viewsets.ModelViewSet):
    queryset = EmailTemplate.objects.all().order_by('-date_created')
    serializer_class = EmailTemplateSerializer
    permission_classes = [IsMarketer]

class EmailCampaignViewSet(viewsets.ModelViewSet):
    queryset = EmailCampaign.objects.all().order_by('-date_created')
    serializer_class = EmailCampaignSerializer
    permission_classes = [IsMarketer]

class EmailLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EmailLog.objects.all().order_by('-sent_at')
    serializer_class = EmailLogSerializer
    permission_classes = [IsMarketer]

@api_view(['POST'])
@permission_classes([IsMarketer])
def scrape_leads_placeholder(request):
    """
    Endpoint for triggering AI-powered web scraping.
    Dispatches a background worker task to find and extract leads.
    """
    import threading
    from .services import run_scraping_job

    query = request.data.get('query', '')
    if not query:
        return Response({'error': 'Search query is required'}, status=status.HTTP_400_BAD_REQUEST)
    
    # Run the job in the background to avoid blocking
    threading.Thread(target=run_scraping_job, args=(query,)).start()

    return Response({
        'message': f'Scraping job started for query: {query}',
        'status': 'processing'
    }, status=status.HTTP_202_ACCEPTED)

@api_view(['GET'])
@permission_classes([IsMarketer])
def funnel_stats(request):
    """
    Returns high-level statistics of the marketing funnel.
    """
    total_leads = DesignerLead.objects.count()
    status_breakdown = DesignerLead.objects.values('status').annotate(count=Count('status'))
    
    return Response({
        'total_leads': total_leads,
        'status_breakdown': list(status_breakdown)
    })
