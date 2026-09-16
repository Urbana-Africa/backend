from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.analytics.models import PageView, VisitorEvent

User = get_user_model()

TRACK_URL = '/analytics/track'
ADMIN_BASE = '/manage/analytics/visitors'


class TrackTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _post(self, payload, **extra):
        return self.client.post(TRACK_URL, payload, format='json', **extra)

    def test_records_pageviews_and_events(self):
        res = self._post({
            'source': 'web',
            'pageviews': [
                {'path': '/', 'title': 'Home', 'referrer': 'https://google.com/search?q=x'},
                {'path': '/products?sort=new', 'duration_ms': 1500},
            ],
            'events': [
                {'name': 'signup', 'category': 'conversion', 'props': {'method': 'email'}},
                {'name': 'payout_requested', 'category': 'conversion',
                 'props': {'currency': 'NGN'}, 'value': '5000.00'},
            ],
        })

        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.data['status'], 'success')
        self.assertEqual(res.data['data']['pageviews_recorded'], 2)
        self.assertEqual(res.data['data']['events_recorded'], 2)

        self.assertEqual(PageView.objects.count(), 2)
        self.assertEqual(VisitorEvent.objects.count(), 2)

        # path query strings stripped
        self.assertTrue(PageView.objects.filter(path='/products').exists())

        # external referrer kept without its query string
        pv = PageView.objects.get(path='/')
        self.assertEqual(pv.referrer, 'https://google.com/search')

        # event value stored as decimal
        ev = VisitorEvent.objects.get(name='payout_requested')
        self.assertEqual(str(ev.value), '5000.00')
        self.assertEqual(ev.props['currency'], 'NGN')

    def test_session_hash_derived_server_side(self):
        res = self._post({
            'source': 'web',
            'pageviews': [{'path': '/'}],
        })
        self.assertEqual(res.status_code, 202)
        pv = PageView.objects.get()
        self.assertEqual(len(pv.session_hash), 16)

    def test_internal_referrer_dropped(self):
        # CORS_ALLOWED_ORIGINS includes urbanaafrica.com in settings
        res = self._post({
            'source': 'web',
            'pageviews': [
                {'path': '/products', 'referrer': 'https://urbanaafrica.com/'},
                {'path': '/cart', 'referrer': '/products'},
            ],
        })
        self.assertEqual(res.status_code, 202)
        self.assertEqual(
            list(PageView.objects.order_by('path').values_list('referrer', flat=True)),
            ['', ''],
        )

    def test_invalid_payload_rejected(self):
        res = self.client.post(TRACK_URL, 'not json', content_type='text/plain')
        self.assertEqual(res.status_code, 400)

    def test_device_inference(self):
        self._post(
            {'source': 'web', 'pageviews': [{'path': '/'}]},
            HTTP_USER_AGENT='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)',
        )
        self.assertEqual(PageView.objects.get().device, 'mobile')


class VisitorAdminReportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_superuser(
            email='admin@urbana.com', password='pass1234',
        )
        self.customer = User.objects.create_user(
            email='customer@urbana.com', password='pass1234',
        )

        now = timezone.now()
        PageView.objects.create(session_hash='aaaabbbbccccdddd', path='/', device='desktop', created_at=now)
        PageView.objects.create(session_hash='aaaabbbbccccdddd', path='/d/brand-x', device='desktop', created_at=now)
        PageView.objects.create(session_hash='eeeeffff00001111', path='/', device='mobile', created_at=now)
        VisitorEvent.objects.create(session_hash='aaaabbbbccccdddd', name='signup', category='conversion',
                                    props={'method': 'email'}, path='/auth/signup', created_at=now)
        VisitorEvent.objects.create(session_hash='aaaabbbbccccdddd', name='purchase', category='conversion',
                                    value='120.00', path='/pay/make-payment/inv1', created_at=now)

    def test_requires_staff(self):
        self.client.force_authenticate(self.customer)
        res = self.client.get(f'{ADMIN_BASE}/overview')
        self.assertEqual(res.status_code, 403)

        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/overview')
        self.assertEqual(res.status_code, 200)

    def test_overview(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/overview?days=7')
        data = res.data['data']
        self.assertEqual(data['totals']['pageviews'], 3)
        self.assertEqual(data['totals']['unique_visitors'], 2)
        self.assertEqual(data['totals']['events'], 2)
        self.assertEqual(len(data['timeseries']), 7)
        self.assertEqual(data['devices'], {'desktop': 2, 'mobile': 1})

    def test_pages(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/pages?days=7')
        pages = {p['path']: p for p in res.data['data']['pages']}
        self.assertEqual(pages['/']['views'], 2)
        self.assertEqual(pages['/']['visitors'], 2)
        self.assertEqual(pages['/d/brand-x']['visitors'], 1)

    def test_events(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/events?days=7')
        events = {e['name']: e for e in res.data['data']['events']}
        self.assertEqual(events['purchase']['count'], 1)
        self.assertEqual(events['purchase']['value_total'], '120.00')
        self.assertEqual(events['signup']['category'], 'conversion')

    def test_funnel(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/funnel?days=7')
        funnel = {f['key']: f for f in res.data['data']['funnel']}
        self.assertEqual(funnel['landing']['count'], 2)
        self.assertEqual(funnel['signup']['count'], 1)
        self.assertEqual(funnel['purchase']['count'], 1)
        self.assertIsNone(funnel['landing']['conversion_rate'])
        self.assertEqual(funnel['signup']['conversion_rate'], 50.0)

    def test_page_detail(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/page-detail', {'path': '/d/brand-x', 'days': 7})
        data = res.data['data']
        self.assertEqual(data['totals']['views'], 1)
        self.assertEqual(data['totals']['visitors'], 1)
        # the viewing session later completed a purchase
        self.assertEqual(data['totals']['completed'], 1)
        self.assertEqual(data['totals']['conversion_rate'], 100.0)

    def test_page_detail_requires_path(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/page-detail')
        self.assertEqual(res.status_code, 400)

    def test_event_detail(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/event-detail', {'name': 'purchase', 'days': 7})
        data = res.data['data']
        self.assertEqual(data['totals']['count'], 1)
        self.assertEqual(data['totals']['sessions'], 1)
        self.assertEqual(data['totals']['value_total'], '120.00')
        self.assertEqual(data['top_paths'][0]['path'], '/pay/make-payment/inv1')

    def test_sessions_and_detail(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/sessions')
        sessions = {s['session']: s for s in res.data['data']['sessions']}
        self.assertIn('aaaabbbbccccdddd', sessions)
        sess = sessions['aaaabbbbccccdddd']
        self.assertTrue(sess['converted'])
        self.assertEqual(sess['views'], 2)
        self.assertEqual(sess['events'], 2)
        self.assertEqual(sess['entry_path'], '/')
        self.assertEqual(sess['exit_path'], '/d/brand-x')

        res = self.client.get(f'{ADMIN_BASE}/session-detail', {'session': 'aaaabbbbccccdddd'})
        data = res.data['data']
        self.assertEqual(data['views'], 2)
        self.assertEqual(data['events'], 2)
        self.assertEqual(len(data['timeline']), 4)
        self.assertEqual(data['timeline'][0]['type'], 'pageview')

    def test_referrers(self):
        PageView.objects.create(
            session_hash='ffffeeee11112222', path='/', referrer='https://twitter.com/post',
            device='mobile', created_at=timezone.now(),
        )
        VisitorEvent.objects.create(
            session_hash='ffffeeee11112222', name='signup', category='conversion',
            created_at=timezone.now(),
        )
        self.client.force_authenticate(self.staff)
        res = self.client.get(f'{ADMIN_BASE}/referrers?days=7')
        refs = res.data['data']['referrers']
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['referrer'], 'https://twitter.com/post')
        self.assertEqual(refs[0]['top_landing_path'], '/')
        self.assertEqual(refs[0]['conversions'], 1)
