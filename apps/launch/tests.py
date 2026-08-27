from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase, APIClient

from apps.launch.models import LaunchConfig, WaitlistSubscriber, EmailSuppression


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
)
class WaitlistAPITests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.config = LaunchConfig.get()
        self.config.consent_version = '1.0'
        self.config.save()

    def _payload(self, **overrides):
        return {
            'email': 'test@example.com',
            'consent_marketing': True,
            'consent_version': '1.0',
            **overrides,
        }

    def test_create_waitlist_requires_email(self):
        response = self.client.post('/launch/waitlist', self._payload(email=''))
        self.assertEqual(response.status_code, 400)

    def test_create_waitlist_requires_consent(self):
        response = self.client.post('/launch/waitlist', self._payload(consent_marketing=False))
        self.assertEqual(response.status_code, 400)

    def test_create_waitlist_sends_confirmation_email(self):
        response = self.client.post('/launch/waitlist', self._payload())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(WaitlistSubscriber.objects.count(), 1)
        subscriber = WaitlistSubscriber.objects.get()
        self.assertEqual(subscriber.status, 'pending')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(subscriber.confirm_token, mail.outbox[0].body)

    def test_create_waitlist_is_idempotent(self):
        first = self.client.post('/launch/waitlist', self._payload())
        self.assertEqual(first.status_code, 201)
        second = self.client.post('/launch/waitlist', self._payload())
        self.assertIn(second.status_code, [200, 201])
        self.assertEqual(WaitlistSubscriber.objects.count(), 1)

    def test_suppressed_email_cannot_join(self):
        EmailSuppression.objects.create(email='suppressed@example.com', reason='unsubscribed')
        response = self.client.post('/launch/waitlist', self._payload(email='suppressed@example.com'))
        self.assertEqual(response.status_code, 400)

    def test_confirm_email_activates_subscriber(self):
        create = self.client.post('/launch/waitlist', self._payload())
        subscriber = WaitlistSubscriber.objects.get()
        token = subscriber.confirm_token
        response = self.client.get(f'/launch/waitlist/confirm?token={token}')
        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertEqual(subscriber.status, 'confirmed')

    def test_referral_credited_only_after_confirm(self):
        referrer = WaitlistSubscriber.objects.create(
            email='referrer@example.com',
            consent_marketing=True,
            consent_version='1.0'
        )
        code = referrer.referral_code

        self.client.post('/launch/waitlist', self._payload(
            email='referee@example.com',
            referral_code=code,
        ))
        referrer.refresh_from_db()
        # count is not credited until the referee confirms
        self.assertEqual(referrer.referral_count, 0)

        referee = WaitlistSubscriber.objects.get(email='referee@example.com')
        self.client.get(f'/launch/waitlist/confirm?token={referee.confirm_token}')
        referrer.refresh_from_db()
        self.assertEqual(referrer.referral_count, 1)
        referee.refresh_from_db()
        self.assertEqual(referee.referred_by, referrer)
        self.assertGreater(referee.spots_skipped, 0)

    def test_unsubscribe_creates_suppression(self):
        create = self.client.post('/launch/waitlist', self._payload())
        subscriber = WaitlistSubscriber.objects.get()
        token = subscriber.unsubscribe_token
        response = self.client.post('/launch/waitlist/unsubscribe', {'token': token})
        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertEqual(subscriber.status, 'unsubscribed')
        self.assertTrue(EmailSuppression.objects.filter(email='test@example.com').exists())

    def test_public_stats_endpoint(self):
        self.client.post('/launch/waitlist', self._payload())
        WaitlistSubscriber.objects.update(status='confirmed')
        response = self.client.get('/launch/stats/public')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['confirmed_count'], 1)

    def test_designer_cta_beacon(self):
        response = self.client.post('/launch/designer-cta', {
            'anon_id': 'abc123',
            'referrer_url': 'https://www.urbanaafrica.com/',
        })
        self.assertEqual(response.status_code, 200)
