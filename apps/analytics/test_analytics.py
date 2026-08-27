from django.test import TestCase
from rest_framework.test import APIClient

from apps.analytics.models import Event, DeadLetterEvent, EventSchema


class AnalyticsCollectTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        EventSchema.objects.create(
            name='view_item',
            version='1.0',
            required_params=['item_id'],
        )

    def test_collects_valid_event(self):
        response = self.client.post('/analytics/collect', [
            {
                'event_id': 'evt-001',
                'name': 'view_item',
                'occurred_at': '2026-08-09T12:00:00Z',
                'anon_id': 'anon-001',
                'session_id': 'sess-001',
                'props': {'item_id': '123'},
                'source': 'web',
                'consent_analytics': True,
            }
        ], format='json')

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['accepted'], 1)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(Event.objects.first().name, 'view_item')

    def test_rejects_missing_required_param(self):
        response = self.client.post('/analytics/collect', [
            {
                'event_id': 'evt-002',
                'name': 'view_item',
                'props': {},
                'source': 'web',
            }
        ], format='json')

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['rejected'], 1)
        self.assertEqual(DeadLetterEvent.objects.count(), 1)
