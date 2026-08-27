from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from .models import (
    DesignerLead,
    EmailTemplate,
    EmailCampaign,
    EmailLog,
    ScrapeProviderConfig,
    ScrapeJob,
    ScrapeCall,
)
from .serializers import (
    DesignerLeadSerializer,
    EmailTemplateSerializer,
    EmailCampaignSerializer,
    EmailLogSerializer,
    ScrapeProviderConfigSerializer,
    ScrapeJobSerializer,
    ScrapeCallSerializer,
)
from apps.administrator.permissions import IsMarketer
from django.db.models import Count

class DesignerLeadViewSet(viewsets.ModelViewSet):
    queryset = DesignerLead.objects.all().order_by('-date_discovered')
    serializer_class = DesignerLeadSerializer
    permission_classes = [IsMarketer]

    def get_queryset(self):
        qs = super().get_queryset()
        needs_review = self.request.query_params.get('needs_review')
        if needs_review is not None:
            value = needs_review.lower()
            if value in ('true', '1', 'yes'):
                qs = qs.filter(needs_review=True)
            elif value in ('false', '0', 'no'):
                qs = qs.filter(needs_review=False)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    @action(detail=True, methods=['post'])
    def send_email(self, request, pk=None):
        lead = self.get_object()
        template_id = request.data.get('template_id')
        custom_subject = request.data.get('subject')
        custom_html_body = request.data.get('html_body')
        
        if not template_id and not custom_html_body:
            return Response({'error': 'Template ID or custom body is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        template = None
        if template_id:
            template = EmailTemplate.objects.filter(id=template_id).first()
            if not template:
                return Response({'error': 'Template not found'}, status=status.HTTP_404_NOT_FOUND)

        from .email_services import compile_and_send_lead_email
        success = compile_and_send_lead_email(lead, template, custom_html_body, custom_subject)
        
        if success:
            return Response({'message': 'Email sent successfully'})
        return Response({'error': 'Failed to send email. Check logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def send_broadcast(self, request):
        template_id = request.data.get('template_id')
        custom_subject = request.data.get('subject')
        custom_html_body = request.data.get('html_body')
        search_query = request.data.get('search', '').lower()
        
        if not template_id and not custom_html_body:
            return Response({'error': 'Template ID or custom body is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        template = None
        if template_id:
            template = EmailTemplate.objects.filter(id=template_id).first()
            if not template:
                return Response({'error': 'Template not found'}, status=status.HTTP_404_NOT_FOUND)

        from django.db.models import Q
        # Get all leads matching search filter
        queryset = self.get_queryset()
        if search_query:
            queryset = queryset.filter(
                Q(brand_name__icontains=search_query) |
                Q(designer_name__icontains=search_query) |
                Q(email__icontains=search_query)
            )

        # Filter out leads without email
        leads_with_email = queryset.exclude(email__isnull=True).exclude(email__exact='')
        count = leads_with_email.count()

        if count == 0:
            return Response({'error': 'No matching leads with email addresses found'}, status=status.HTTP_400_BAD_REQUEST)

        # Process in background to avoid timeout
        import threading
        from .email_services import compile_and_send_lead_email
        def process_broadcast(leads_qs, tpl, cust_body, cust_subj):
            for l in leads_qs:
                compile_and_send_lead_email(l, tpl, cust_body, cust_subj)

        threading.Thread(target=process_broadcast, args=(list(leads_with_email), template, custom_html_body, custom_subject)).start()

        return Response({'message': f'Broadcast started for {count} leads'})

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
    Endpoint for triggering third-party API scraping.
    Creates a ScrapeJob and dispatches the provider engine.
    """
    import threading
    from .services import run_scraping_job

    query = request.data.get('query', '')
    if not query:
        return Response({'error': 'Search query is required'}, status=status.HTTP_400_BAD_REQUEST)

    provider_name = request.data.get('provider', '')
    max_results = request.data.get('max_results', 5)

    job = run_scraping_job(
        query=query,
        max_results=int(max_results),
        created_by=request.user,
        provider_name=provider_name,
    )

    return Response({
        'message': f'Scraping job started for query: {query}',
        'job_id': job.id,
        'status': job.status,
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


class ScrapeProviderConfigViewSet(viewsets.ModelViewSet):
    queryset = ScrapeProviderConfig.objects.all().order_by('priority', 'name')
    serializer_class = ScrapeProviderConfigSerializer
    permission_classes = [IsMarketer]


class ScrapeJobViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ScrapeJob.objects.all().order_by('-created_at')
    serializer_class = ScrapeJobSerializer
    permission_classes = [IsMarketer]


class ScrapeCallViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ScrapeCall.objects.all().order_by('-created_at')
    serializer_class = ScrapeCallSerializer
    permission_classes = [IsMarketer]
