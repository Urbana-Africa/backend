from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.marketing.models import (
    DesignerLead, LeadEnrichment, LeadSuppression,
    ScrapeCall, ScrapeCache, ScrapeJob, ScrapeProviderConfig,
)
from apps.marketing.scraping.engine import (
    BudgetExceeded,
    _consume_budget,
    _first_url,
    _get_cached_or_extract,
    _is_platform_domain,
    _is_suppressed,
    _lead_data_from_instagram,
    _lead_from_extracted,
    _looks_like_instagram_profile,
    _slim_payload,
    _url_hash,
    run_scrape_engine,
)
from apps.marketing.scraping.providers.brightdata import BrightDataProvider
from apps.marketing.scraping.providers.dataforseo import DataForSEOProvider


IG_RECORD = {
    "account": "testbrand",
    "full_name": "Test Brand",
    "biography": "Lagos fashion designer. Email: hello@testbrand.com",
    "followers": 12500,
    "posts_count": 42,
    "external_url": "https://testbrand.com",
    "email_address": "hello@testbrand.com",
    "business_category_name": "Fashion Designer",
}


def make_job(**kw):
    kw.setdefault("query", "test query")
    kw.setdefault("status", "running")
    kw.setdefault("provider_name", "brightdata")
    return ScrapeJob.objects.create(**kw)


def ig_extracted(url="https://www.instagram.com/testbrand/"):
    return {
        "url": url,
        "json": dict(IG_RECORD),
        "text": "Instagram profile: @testbrand",
        "source": "brightdata",
    }


class PureHelpersTests(TestCase):
    def test_first_url_variants(self):
        self.assertEqual(_first_url("https://a.com"), "https://a.com")
        self.assertEqual(_first_url(["https://a.com", "https://b.com"]), "https://a.com")
        self.assertEqual(
            _first_url([{"url": "https://a.com", "title": "x"}]), "https://a.com"
        )
        self.assertEqual(_first_url([]), "")
        self.assertEqual(_first_url(None), "")
        self.assertEqual(_first_url({"url": "https://a.com"}), "https://a.com")

    def test_url_hash_includes_provider(self):
        self.assertNotEqual(
            _url_hash("brightdata", "https://x.com"),
            _url_hash("dataforseo", "https://x.com"),
        )

    def test_platform_domain_subdomains(self):
        self.assertTrue(_is_platform_domain("instagram.com"))
        self.assertTrue(_is_platform_domain("help.instagram.com"))
        self.assertTrue(_is_platform_domain("m.facebook.com"))
        self.assertFalse(_is_platform_domain("testbrand.com"))
        self.assertFalse(_is_platform_domain("notinstagram.com"))

    def test_looks_like_instagram_profile(self):
        self.assertTrue(_looks_like_instagram_profile({"account": "x"}))
        self.assertTrue(
            _looks_like_instagram_profile({"biography": "b", "followers": 1})
        )
        self.assertFalse(_looks_like_instagram_profile({"title": "web page"}))
        self.assertFalse(_looks_like_instagram_profile(None))

    def test_slim_payload_strips_bulky_fields(self):
        extracted = {
            "url": "u",
            "html": "h" * 500,
            "text": "t" * 500,
            "json": {"account": "a", "posts": [1, 2, 3], "highlights": [1]},
        }
        slim = _slim_payload(extracted)
        self.assertNotIn("html", slim)
        self.assertNotIn("text", slim)
        self.assertEqual(slim["content_chars"], 500)
        self.assertNotIn("posts", slim["json"])
        self.assertNotIn("highlights", slim["json"])
        self.assertEqual(slim["json"]["account"], "a")

    def test_lead_data_from_instagram_maps_fields(self):
        data = _lead_data_from_instagram(
            IG_RECORD, "https://www.instagram.com/testbrand/"
        )
        self.assertEqual(data["brand_name"], "Test Brand")
        self.assertEqual(data["email"], "hello@testbrand.com")
        self.assertEqual(data["followers_count"], 12500)
        self.assertEqual(data["category_tags"], ["Fashion Designer"])
        self.assertEqual(
            data["social_media_links"]["instagram"],
            "https://www.instagram.com/testbrand/",
        )

    def test_lead_data_from_instagram_finds_email_in_bio(self):
        rec = dict(IG_RECORD, email_address="", business_email="", email="")
        data = _lead_data_from_instagram(rec, "https://ig.com/x/")
        self.assertEqual(data["email"], "hello@testbrand.com")


class BudgetTests(TestCase):
    def setUp(self):
        self.job = make_job()

    def test_no_cap_still_tracks_spend(self):
        # Sub-cent call costs must accumulate — monthly_spend needs 4dp.
        ScrapeProviderConfig.objects.create(
            name="p1", cost_per_1k_credits=Decimal("1.5"), monthly_budget=0
        )
        _consume_budget(self.job, "p1")
        _consume_budget(self.job, "p1")
        cfg = ScrapeProviderConfig.objects.get(name="p1")
        self.assertEqual(cfg.monthly_spend, Decimal("0.0030"))

    def test_cap_blocks_and_stops_job(self):
        ScrapeProviderConfig.objects.create(
            name="p2", cost_per_1k_credits=Decimal("10"), monthly_budget=Decimal("0.02")
        )
        _consume_budget(self.job, "p2")
        _consume_budget(self.job, "p2")
        with self.assertRaises(BudgetExceeded):
            _consume_budget(self.job, "p2")
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "budget_stopped")


class LeadCreationTests(TestCase):
    def setUp(self):
        self.job = make_job()

    def test_instagram_record_creates_lead_without_gemini(self):
        lead = _lead_from_extracted(ig_extracted(), "brightdata", self.job)
        self.assertIsNotNone(lead)
        self.assertEqual(lead.brand_name, "Test Brand")
        self.assertEqual(lead.instagram_handle, "testbrand")
        self.assertEqual(lead.email, "hello@testbrand.com")
        self.assertEqual(lead.website, "https://testbrand.com")
        self.assertEqual(lead.followers_count, 12500)
        self.assertFalse(lead.needs_review)
        self.assertEqual(lead.source, "brightdata")
        self.assertTrue(LeadEnrichment.objects.filter(lead=lead).exists())
        # No LLM call recorded — structured IG records skip Gemini entirely
        self.assertFalse(
            ScrapeCall.objects.filter(job=self.job, call_type="llm").exists()
        )

    def test_same_brand_second_source_is_skipped(self):
        lead1 = _lead_from_extracted(ig_extracted(), "brightdata", self.job)
        self.assertIsNotNone(lead1)
        # Same brand found via its own domain — different URL, same identifiers
        dup = _lead_from_extracted(ig_extracted(), "brightdata", self.job)
        self.assertEqual(dup, "skipped")
        self.assertEqual(DesignerLead.objects.count(), 1)

    def test_cross_identifier_dedupe_by_handle(self):
        DesignerLead.objects.create(
            brand_name="Someone Else", instagram_handle="testbrand"
        )
        res = _lead_from_extracted(ig_extracted(), "brightdata", self.job)
        self.assertEqual(res, "skipped")

    def test_suppression_by_domain_normalises_www(self):
        LeadSuppression.objects.create(brand_name="X", domain="testbrand.com")
        res = _lead_from_extracted(
            ig_extracted("https://www.testbrand.com/about"), "brightdata", self.job
        )
        self.assertEqual(res, "skipped")
        self.assertEqual(DesignerLead.objects.count(), 0)

    def test_suppression_by_brand_name(self):
        LeadSuppression.objects.create(brand_name="Test Brand")
        res = _lead_from_extracted(ig_extracted(), "brightdata", self.job)
        self.assertEqual(res, "skipped")

    def test_missing_contacts_flag_needs_review(self):
        rec = dict(IG_RECORD, biography="Fashion house in Lagos",
                   email_address="", business_email="", email="")
        extracted = ig_extracted()
        extracted["json"] = rec
        lead = _lead_from_extracted(extracted, "brightdata", self.job)
        self.assertTrue(lead.needs_review)
        self.assertEqual(lead.email, "")


class CacheAndRefundTests(TestCase):
    def setUp(self):
        self.job = make_job()
        # Provider rows are seeded by migration 0005 — update, don't create.
        ScrapeProviderConfig.objects.update_or_create(
            name="brightdata",
            defaults={
                "enabled": True,
                "cost_per_1k_credits": Decimal("1.5"),
                "monthly_budget": Decimal("50"),
                "monthly_spend": Decimal("0"),
            },
        )

    def test_free_direct_fetch_refunds_budget(self):
        provider = MagicMock()
        provider.name = "brightdata"
        provider.extract.return_value = {
            "url": "https://site.com",
            "markdown": "real content " * 50,
            "source": "direct",
            "_free_call": True,
        }
        _get_cached_or_extract(self.job, provider, "https://site.com")
        cfg = ScrapeProviderConfig.objects.get(name="brightdata")
        self.assertEqual(cfg.monthly_spend, Decimal("0"))
        call = ScrapeCall.objects.get(job=self.job)
        self.assertEqual(call.cost_usd, 0)

    def test_failed_extract_refunds_budget(self):
        provider = MagicMock()
        provider.name = "brightdata"
        provider.extract.side_effect = RuntimeError("boom")
        res = _get_cached_or_extract(self.job, provider, "https://site.com")
        self.assertIsNone(res)
        cfg = ScrapeProviderConfig.objects.get(name="brightdata")
        self.assertEqual(cfg.monthly_spend, Decimal("0"))
        call = ScrapeCall.objects.get(job=self.job)
        self.assertEqual(call.status, "error")

    def test_cache_hit_skips_provider_and_records_call(self):
        h = _url_hash("brightdata", "https://site.com")
        ScrapeCache.objects.create(
            url_hash=h, url="https://site.com", provider_name="brightdata",
            extracted={"url": "https://site.com", "text": "cached"},
            stale_after=timezone.now() + timezone.timedelta(hours=1),
        )
        provider = MagicMock()
        provider.name = "brightdata"
        out = _get_cached_or_extract(self.job, provider, "https://site.com")
        self.assertEqual(out["text"], "cached")
        provider.extract.assert_not_called()
        self.assertEqual(ScrapeCall.objects.filter(job=self.job).count(), 1)

    def test_stale_cache_is_not_served(self):
        h = _url_hash("brightdata", "https://site.com")
        ScrapeCache.objects.create(
            url_hash=h, url="https://site.com", provider_name="brightdata",
            extracted={"url": "https://site.com", "text": "old"},
            stale_after=timezone.now() - timezone.timedelta(hours=1),
        )
        provider = MagicMock()
        provider.name = "brightdata"
        provider.extract.return_value = {"url": "https://site.com", "text": "fresh"}
        out = _get_cached_or_extract(self.job, provider, "https://site.com")
        self.assertEqual(out["text"], "fresh")
        provider.extract.assert_called_once()


class ProviderUnitTests(TestCase):
    def test_dataforseo_filters_non_organic(self):
        resp = MagicMock()
        resp.json.return_value = {
            "status_code": 20000,
            "tasks": [{
                "status_code": 20000,
                "result": [{"items": [
                    {"type": "paid", "url": "https://ad.example"},
                    {"type": "organic", "url": "https://real.example",
                     "title": "t", "description": "d"},
                    {"type": "featured_snippet", "url": "https://fs.example"},
                ]}],
            }],
        }
        with patch(
            "apps.marketing.scraping.providers.dataforseo.request_with_retry",
            return_value=resp,
        ):
            p = DataForSEOProvider({"login": "l", "password": "p"})
            results = p.search("q", max_results=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://real.example")

    def test_dataforseo_raises_on_api_error(self):
        resp = MagicMock()
        resp.json.return_value = {
            "status_code": 40100, "status_message": "Unauthorized",
        }
        with patch(
            "apps.marketing.scraping.providers.dataforseo.request_with_retry",
            return_value=resp,
        ):
            p = DataForSEOProvider({"login": "l", "password": "p"})
            with self.assertRaises(RuntimeError):
                p.search("q")

    def test_direct_fetch_accepts_real_page(self):
        body = "<html><body><p>" + ("real brand content " * 40) + "</p></body></html>"
        resp = MagicMock(status_code=200, text=body)
        p = BrightDataProvider({"api_key": "k"})
        with patch(
            "apps.marketing.scraping.providers.brightdata.requests.get",
            return_value=resp,
        ):
            out = p._direct_fetch("https://brand.example")
        self.assertIsNotNone(out)
        self.assertTrue(out["_free_call"])

    def test_direct_fetch_detects_block_page(self):
        body = "<html><body>Just a moment... checking your browser</body></html>"
        resp = MagicMock(status_code=200, text=body)
        p = BrightDataProvider({"api_key": "k"})
        with patch(
            "apps.marketing.scraping.providers.brightdata.requests.get",
            return_value=resp,
        ):
            self.assertIsNone(p._direct_fetch("https://brand.example"))

    def test_direct_fetch_detects_empty_js_shell(self):
        resp = MagicMock(status_code=200, text="<html><body><div id=app></div></body></html>")
        p = BrightDataProvider({"api_key": "k"})
        with patch(
            "apps.marketing.scraping.providers.brightdata.requests.get",
            return_value=resp,
        ):
            self.assertIsNone(p._direct_fetch("https://spa.example"))


class EngineEndToEndTests(TestCase):
    def setUp(self):
        # Rows are seeded by migration 0005 — update in place.
        ScrapeProviderConfig.objects.filter(name="dataforseo").update(
            enabled=True, priority=1,
            config={"login": "l", "password": "p"},
            cost_per_1k_credits=Decimal("0.8"), monthly_budget=Decimal("20"),
            monthly_spend=Decimal("0"),
        )
        ScrapeProviderConfig.objects.filter(name="brightdata").update(
            enabled=True, priority=2,
            config={"api_key": "k"},
            cost_per_1k_credits=Decimal("1.5"), monthly_budget=Decimal("50"),
            monthly_spend=Decimal("0"),
        )

    # Threads + TestCase transactions don't mix — force sequential processing.
    @override_settings(SCRAPE_MAX_WORKERS=1)
    @patch.object(DataForSEOProvider, "search")
    @patch.object(BrightDataProvider, "extract")
    def test_full_pipeline_creates_lead(self, mock_extract, mock_search):
        mock_search.return_value = [
            {"url": "https://www.instagram.com/testbrand/", "title": "t"},
        ]
        mock_extract.return_value = ig_extracted()

        job = ScrapeJob.objects.create(
            query="site:instagram.com fashion designer",
            provider_name="dataforseo",
            status="queued",
        )
        run_scrape_engine(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.result_summary["leads_created"], 1)
        self.assertTrue(DesignerLead.objects.filter(instagram_handle="testbrand").exists())
        calls = ScrapeCall.objects.filter(job=job)
        self.assertEqual(calls.filter(call_type="search").count(), 1)
        self.assertEqual(calls.filter(call_type="extract").count(), 1)

    @override_settings(SCRAPE_MAX_WORKERS=1)
    def test_engine_fails_fast_without_extract_provider(self):
        ScrapeProviderConfig.objects.filter(name="brightdata").update(enabled=False)
        job = ScrapeJob.objects.create(
            query="x", provider_name="dataforseo", status="queued",
        )
        run_scrape_engine(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("cannot extract", job.error_message)

    @override_settings(SCRAPE_MAX_WORKERS=1)
    @patch.object(DataForSEOProvider, "search")
    def test_search_error_fails_job_and_refunds(self, mock_search):
        mock_search.side_effect = RuntimeError("401 Unauthorized")
        job = ScrapeJob.objects.create(
            query="x", provider_name="dataforseo", status="queued",
        )
        run_scrape_engine(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("401", job.error_message)
        cfg = ScrapeProviderConfig.objects.get(name="dataforseo")
        self.assertEqual(cfg.monthly_spend, Decimal("0"))
        call = ScrapeCall.objects.get(job=job, call_type="search")
        self.assertEqual(call.status, "error")
