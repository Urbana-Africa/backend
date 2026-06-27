from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/support/tickets/(?P<ticket_id>[^/]+)/$', consumers.SupportTicketConsumer.as_asgi()),
]
