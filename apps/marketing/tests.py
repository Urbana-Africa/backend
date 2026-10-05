from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.marketing.campaigns import process_campaign_batch, process_due_campaigns
from apps.marketing.eligibility import (
    find_suppression,
    lead_contactable,
    lead_from_unsubscribe_token,
    record_suppression,
    unsubscribe_token_for,
)
from apps.marketing.email_services import compile_and_send_lead_email
from apps.marketing.models import (
    DesignerLead, EmailCampaign, EmailLog, LeadEnrichment,
    LeadQualificationDecision, LeadSuppression,
    ScrapeCall, ScrapeCache, ScrapeJob, ScrapeProviderConfig,
)
from apps.marketing.qualification import (
    classify_existing_lead,
    classify_instagram_profile,
)
from apps.marketing.scraping.engine import (
    BudgetExceeded,
    _consume_budget,
    _first_url,
    _get_cached_or_extract,
    _is_platform_domain,
    _is_direct_instagram_designer,
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

    def test_instagram_qualification(self):
        self.assertTrue(_is_direct_instagram_designer(IG_RECORD))
        self.assertFalse(_is_direct_instagram_designer({
            "account": "fashiondesignersinlagos",
            "full_name": "Fashion Designers in Lagos",
            "biography": "Discover the best designers in Lagos. DM to be featured.",
            "business_category_name": "Fashion Designer",
        }))
        self.assertFalse(_is_direct_instagram_designer({
            "account": "bestfashiondesignerlagos",
            "full_name": "BEST FASHION DESIGNER IN GBAGADA, LAGOS, NIGERIA",
            "biography": "Fashion designer in Lagos",
            "business_category_name": "Fashion Designer",
        }))
        self.assertFalse(_is_direct_instagram_designer({
            "account": "naijafashiondesigners",
            "full_name": "Naija Fashion Designers|Fashion Designer in Lagos",
            "biography": "Fashion designer in Lagos",
        }))
        self.assertTrue(_is_direct_instagram_designer({
            "account": "starrycouture",
            "full_name": "STARRY | LAGOS FASHION DESIGNER",
            "biography": "Bespoke bridalwear made in Lagos. Book a fitting.",
        }))


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
        self.assertEqual(lead.designer_name, "")
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

    def test_directory_profile_is_not_saved(self):
        extracted = ig_extracted("https://www.instagram.com/fashiondesignersinlagos/")
        extracted["json"].update(
            account="fashiondesignersinlagos",
            full_name="Fashion Designers in Lagos",
            biography="Discover designers in Lagos. DM to be featured.",
        )
        self.assertEqual(_lead_from_extracted(extracted, "brightdata", self.job), "skipped")
        self.assertFalse(DesignerLead.objects.exists())

    @patch("apps.marketing.scraping.engine._parse_extracted_text", return_value=None)
    def test_unqualified_web_page_is_not_saved(self, parse):
        extracted = {
            "url": "https://example.com/fashion-designers-lagos",
            "json": {"full_name": "First designer mentioned"},
            "text": "A list of Lagos designers",
        }
        self.assertEqual(_lead_from_extracted(extracted, "brightdata", self.job), "skipped")
        self.assertFalse(DesignerLead.objects.exists())


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


def make_marketer():
    user = get_user_model().objects.create_user(
        email="mkt@example.com", password="x"
    )
    user.user_type = "admin"
    user.admin_role = "marketer"
    user.is_active = True
    user.save()
    return user


def qualified_lead(**kw):
    kw.setdefault("brand_name", "Good Brand")
    kw.setdefault("status", "Qualified")
    kw.setdefault("email", "hello@goodbrand.com")
    kw.setdefault("qualification_type", "direct_designer")
    return DesignerLead.objects.create(**kw)


class QualificationClassifierTests(TestCase):
    def test_directory_account_is_directory(self):
        qtype, _ = classify_instagram_profile({
            "account": "fashiondesignersinlagos",
            "full_name": "Fashion Designers in Lagos",
            "biography": "Discover the best designers in Lagos. DM to be featured.",
        })
        self.assertEqual(qtype, "directory")

    def test_community_without_work_evidence(self):
        qtype, _ = classify_instagram_profile({
            "account": "lagostailorsnetwork",
            "full_name": "Lagos Tailors Network",
            "biography": "A community of tailors and fashion lovers in Lagos.",
        })
        self.assertEqual(qtype, "community")

    def test_direct_brand(self):
        qtype, _ = classify_instagram_profile({
            "account": "starrycouture",
            "full_name": "STARRY | Lagos Fashion Designer",
            "biography": "Bespoke bridalwear made in Lagos. Book a fitting.",
        })
        self.assertEqual(qtype, "direct_designer")

    def test_no_evidence_is_uncertain(self):
        qtype, _ = classify_instagram_profile({
            "account": "mysteryacct",
            "full_name": "Mystery",
            "biography": "Just vibes and photos",
        })
        self.assertEqual(qtype, "uncertain")


class IngestionDecisionTests(TestCase):
    def setUp(self):
        self.job = make_job()

    def test_rejected_candidate_records_decision(self):
        extracted = ig_extracted("https://www.instagram.com/fashiondesignersinlagos/")
        extracted["json"].update(
            account="fashiondesignersinlagos",
            full_name="Fashion Designers in Lagos",
            biography="Discover designers in Lagos.",
        )
        res = _lead_from_extracted(extracted, "brightdata", self.job)
        self.assertEqual(res, "skipped")
        decision = LeadQualificationDecision.objects.get(job=self.job)
        self.assertEqual(decision.decision, "directory")
        self.assertIsNone(decision.lead)
        self.assertEqual(decision.candidate_name, "Fashion Designers in Lagos")

    def test_created_lead_records_decision(self):
        lead = _lead_from_extracted(ig_extracted(), "brightdata", self.job)
        self.assertEqual(lead.qualification_type, "direct_designer")
        self.assertEqual(lead.status, "Discovered")
        self.assertTrue(
            LeadQualificationDecision.objects.filter(
                lead=lead, decision="direct_designer", decided_by="rules"
            ).exists()
        )

    def test_uncertain_profile_is_persisted_for_review(self):
        rec = dict(
            IG_RECORD,
            biography="Just vibes and photos",
            business_category_name="",
            email_address="",
        )
        extracted = ig_extracted()
        extracted["json"] = rec
        lead = _lead_from_extracted(extracted, "brightdata", self.job)
        self.assertIsNotNone(lead)
        self.assertEqual(lead.qualification_type, "uncertain")
        self.assertEqual(lead.status, "Needs Review")
        self.assertTrue(lead.needs_review)


class EligibilityTests(TestCase):
    def test_qualified_lead_is_contactable(self):
        ok, reasons = lead_contactable(qualified_lead())
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_discovered_lead_is_not_contactable(self):
        lead = DesignerLead.objects.create(
            brand_name="Raw Find", status="Discovered", email="x@raw.com"
        )
        ok, reasons = lead_contactable(lead)
        self.assertFalse(ok)
        self.assertTrue(any("not eligible" in r for r in reasons))

    def test_suppression_blocks_by_email_handle_domain(self):
        for field, value in (
            ("email", "b@brand.com"),
            ("handle", "thebrand"),
            ("domain", "brand.com"),
        ):
            record_suppression(brand_name="B", reason="manual", **{field: value})
            lead = qualified_lead(
                brand_name="B", email="b@brand.com",
                instagram_handle="thebrand", website="https://brand.com",
            )
            ok, reasons = lead_contactable(lead)
            self.assertFalse(ok, field)
            self.assertTrue(any("suppressed" in r for r in reasons))
            LeadSuppression.objects.all().delete()

    def test_frequency_cap_blocks(self):
        lead = qualified_lead()
        for _ in range(2):
            EmailLog.objects.create(lead=lead, subject="s", status="Sent")
        ok, reasons = lead_contactable(lead)
        self.assertFalse(ok)
        self.assertTrue(any("frequency cap" in r for r in reasons))

    @patch("apps.marketing.email_services.resend_sendmail", return_value=True)
    def test_send_to_suppressed_is_blocked_and_logged(self, mock_send):
        lead = qualified_lead()
        record_suppression(email="hello@goodbrand.com", reason="unsubscribe")
        result = compile_and_send_lead_email(lead, None, "<p>x</p>", "subj")
        self.assertFalse(result)
        mock_send.assert_not_called()
        log = EmailLog.objects.get(lead=lead)
        self.assertEqual(log.status, "Suppressed")

    @patch("apps.marketing.email_services.resend_sendmail", return_value=True)
    def test_send_to_qualified_writes_sent_log(self, mock_send):
        lead = qualified_lead()
        result = compile_and_send_lead_email(lead, None, "<p>x</p>", "subj")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertEqual(EmailLog.objects.get(lead=lead).status, "Sent")

    def test_record_suppression_is_idempotent(self):
        row1, created1 = record_suppression(email="a@b.com", reason="manual")
        row2, created2 = record_suppression(email="a@b.com", reason="manual")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(row1.id, row2.id)


class CampaignFlowTests(TestCase):
    def setUp(self):
        self.lead_ok = qualified_lead(brand_name="A", email="a@x.com")
        self.lead_supp = qualified_lead(brand_name="B", email="b@x.com")
        record_suppression(email="b@x.com", reason="unsubscribe")
        # Rejected lead is not in the filtered audience at all.
        DesignerLead.objects.create(
            brand_name="C", email="c@x.com", status="Rejected"
        )
        self.campaign = EmailCampaign.objects.create(
            name="Test", subject="Hi", html_body="<p>hello</p>",
            status="sending", audience_filter={},
        )

    @patch("apps.marketing.email_services.resend_sendmail", return_value=True)
    def test_sweep_sends_only_to_eligible(self, mock_send):
        res = process_campaign_batch(self.campaign)
        self.assertEqual(res["sent"], 1)
        self.assertEqual(res["skipped"], 1)
        mock_send.assert_called_once()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "completed")
        self.assertEqual(
            EmailLog.objects.get(lead=self.lead_supp).status, "Suppressed"
        )
        self.lead_ok.refresh_from_db()
        self.assertEqual(self.lead_ok.status, "Contacted")

    @patch("apps.marketing.email_services.resend_sendmail", return_value=True)
    def test_sweep_is_idempotent(self, mock_send):
        process_campaign_batch(self.campaign)
        process_campaign_batch(self.campaign)
        mock_send.assert_called_once()
        self.assertEqual(EmailLog.objects.filter(status="Sent").count(), 1)

    @patch("apps.marketing.email_services.resend_sendmail", return_value=True)
    def test_paused_campaign_does_not_send(self, mock_send):
        self.campaign.status = "paused"
        self.campaign.save()
        process_campaign_batch(self.campaign)
        mock_send.assert_not_called()

    @patch("apps.marketing.email_services.resend_sendmail", return_value=True)
    def test_send_cap_stops_sends(self, mock_send):
        qualified_lead(brand_name="D", email="d@x.com")
        self.campaign.send_cap = 1
        self.campaign.save()
        process_campaign_batch(self.campaign)
        self.assertEqual(mock_send.call_count, 1)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "completed")

    @patch("apps.marketing.email_services.resend_sendmail", return_value=True)
    def test_scheduled_campaign_starts_when_due(self, mock_send):
        self.campaign.status = "approved"
        self.campaign.scheduled_at = timezone.now() - timezone.timedelta(minutes=1)
        self.campaign.save()
        process_due_campaigns()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "completed")
        mock_send.assert_called_once()


class MarketingApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_marketer())

    def test_broadcast_creates_sending_campaign(self):
        qualified_lead()
        res = self.client.post(
            "/marketing/leads/send_broadcast/",
            {"subject": "Hi", "html_body": "<p>x</p>"},
            format="json",
        )
        self.assertEqual(res.status_code, 202, res.data)
        camp = EmailCampaign.objects.get(id=res.data["campaign_id"])
        self.assertEqual(camp.status, "sending")
        self.assertEqual(camp.created_by.email, "mkt@example.com")

    def test_broadcast_with_no_eligible_leads_fails(self):
        DesignerLead.objects.create(brand_name="D", status="Discovered")
        res = self.client.post(
            "/marketing/leads/send_broadcast/",
            {"subject": "Hi", "html_body": "<p>x</p>"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_send_email_blocked_for_discovered_lead(self):
        lead = DesignerLead.objects.create(
            brand_name="D", status="Discovered", email="d@x.com"
        )
        res = self.client.post(
            f"/marketing/leads/{lead.id}/send_email/",
            {"subject": "Hi", "html_body": "<p>x</p>"},
            format="json",
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("reasons", res.data)

    def test_qualify_then_send_allowed(self):
        lead = DesignerLead.objects.create(
            brand_name="D", status="Needs Review",
            email="d@x.com", needs_review=True,
            qualification_type="uncertain",
        )
        res = self.client.post(f"/marketing/leads/{lead.id}/qualify/")
        self.assertEqual(res.status_code, 200, res.data)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "Qualified")
        self.assertEqual(lead.qualification_type, "direct_designer")
        self.assertIsNotNone(lead.qualified_at)
        self.assertTrue(
            LeadQualificationDecision.objects.filter(
                lead=lead, decision="qualified", decided_by="human"
            ).exists()
        )

    def test_reject_with_suppression(self):
        lead = DesignerLead.objects.create(brand_name="Spam Dir", status="Discovered")
        res = self.client.post(
            f"/marketing/leads/{lead.id}/reject/",
            {"reason": "directory", "suppress": True},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "Rejected")
        self.assertIsNotNone(find_suppression(lead))

    def test_suppress_action(self):
        lead = qualified_lead()
        res = self.client.post(f"/marketing/leads/{lead.id}/suppress/")
        self.assertEqual(res.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "Suppressed")
        self.assertIsNotNone(find_suppression(lead))

    def test_requalify_backfill(self):
        DesignerLead.objects.create(
            brand_name="Fashion Designers in Lagos", status="Discovered",
        )
        DesignerLead.objects.create(
            brand_name="Thin Evidence", status="Discovered",
        )
        res = self.client.post("/marketing/leads/requalify/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["hard_reject"], 1)
        self.assertEqual(res.data["uncertain"], 1)
        self.assertEqual(
            DesignerLead.objects.get(brand_name="Fashion Designers in Lagos").status,
            "Rejected",
        )
        self.assertEqual(
            DesignerLead.objects.get(brand_name="Thin Evidence").status,
            "Needs Review",
        )

    def test_unsubscribe_endpoint(self):
        lead = qualified_lead()
        token = unsubscribe_token_for(lead)
        anon = APIClient()
        res = anon.get(f"/marketing/leads/unsubscribe/?token={token}")
        self.assertEqual(res.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "Suppressed")
        self.assertIsNotNone(find_suppression(lead))
        self.assertIs(lead_from_unsubscribe_token(token) is not None, True)

    def test_unsubscribe_rejects_bad_token(self):
        res = APIClient().get("/marketing/leads/unsubscribe/?token=bogus")
        self.assertEqual(res.status_code, 400)

    def test_campaign_approve_send_lifecycle(self):
        qualified_lead()
        camp = EmailCampaign.objects.create(
            name="C", subject="s", html_body="<p>x</p>", status="draft",
        )
        res = self.client.post(f"/marketing/campaigns/{camp.id}/approve/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "approved")
        res = self.client.post(f"/marketing/campaigns/{camp.id}/send/")
        self.assertEqual(res.status_code, 200, res.data)
        camp.refresh_from_db()
        self.assertEqual(camp.status, "sending")

    def test_approve_requires_eligible_audience(self):
        camp = EmailCampaign.objects.create(
            name="Empty", subject="s", html_body="<p>x</p>", status="draft",
        )
        res = self.client.post(f"/marketing/campaigns/{camp.id}/approve/")
        self.assertEqual(res.status_code, 400)


class MergeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_marketer()
        self.client.force_authenticate(self.user)

    def test_merge_consolidates_into_target(self):
        target = qualified_lead(brand_name="Main Brand", email="main@x.com")
        source = DesignerLead.objects.create(
            brand_name="Dup Brand", email="dup@x.com",
            instagram_handle="dup_ig", status="Needs Review",
        )
        EmailLog.objects.create(lead=source, subject="old", status="Sent")

        res = self.client.post(
            f"/marketing/leads/{source.id}/merge/",
            {"target_id": target.id}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)

        target.refresh_from_db()
        source.refresh_from_db()
        self.assertEqual(target.instagram_handle, "dup_ig")
        self.assertTrue(EmailLog.objects.filter(lead=target, subject="old").exists())
        self.assertEqual(source.status, "Rejected")
        self.assertEqual(source.merged_into_id, target.id)
        self.assertTrue(
            LeadQualificationDecision.objects.filter(
                lead=source, decision="merged", decided_by="human"
            ).exists()
        )

    def test_merge_rejects_suppressed_target(self):
        target = qualified_lead(brand_name="T", status="Suppressed")
        source = DesignerLead.objects.create(brand_name="S")
        res = self.client.post(
            f"/marketing/leads/{source.id}/merge/",
            {"target_id": target.id}, format="json",
        )
        self.assertEqual(res.status_code, 400)


class TestSendTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_marketer())
        self.lead = qualified_lead()
        self.camp = EmailCampaign.objects.create(
            name="C", subject="Hello {{ brand_name }}",
            html_body="<p>hi {{ brand_name }}</p>", status="draft",
        )

    @patch("apps.utils.email_sender.resend_sendmail", return_value=True)
    def test_test_send_to_custom_email(self, mock_send):
        res = self.client.post(
            f"/marketing/campaigns/{self.camp.id}/test_send/",
            {"email": "me@urbana.com", "lead_id": self.lead.id},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        mock_send.assert_called_once()
        self.assertIn("[TEST]", mock_send.call_args.kwargs["subject"])
        log = EmailLog.objects.get(lead=self.lead)
        self.assertTrue(log.reason.startswith("test_send"))

    def test_test_send_blocked_for_suppressed_destination(self):
        record_suppression(email="blocked@x.com", reason="unsubscribe")
        res = self.client.post(
            f"/marketing/campaigns/{self.camp.id}/test_send/",
            {"email": "blocked@x.com"},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_test_send_requires_destination(self):
        res = self.client.post(
            f"/marketing/campaigns/{self.camp.id}/test_send/", {}, format="json",
        )
        self.assertEqual(res.status_code, 400)


class MakerCheckerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_marketer()
        self.client.force_authenticate(self.user)

    @override_settings(CAMPAIGN_MAKER_CHECKER_THRESHOLD=1)
    def test_creator_cannot_approve_large_audience(self):
        qualified_lead(brand_name="A", email="a@x.com")
        qualified_lead(brand_name="B", email="b@x.com")
        camp = EmailCampaign.objects.create(
            name="Big", subject="s", html_body="<p>x</p>",
            status="draft", created_by=self.user,
        )
        res = self.client.post(f"/marketing/campaigns/{camp.id}/approve/")
        self.assertEqual(res.status_code, 403)
        camp.refresh_from_db()
        self.assertEqual(camp.status, "draft")

    @override_settings(CAMPAIGN_MAKER_CHECKER_THRESHOLD=1)
    def test_other_marketer_can_approve(self):
        qualified_lead(brand_name="A", email="a@x.com")
        qualified_lead(brand_name="B", email="b@x.com")
        creator = get_user_model().objects.create_user(
            email="creator@example.com", password="x"
        )
        camp = EmailCampaign.objects.create(
            name="Big", subject="s", html_body="<p>x</p>",
            status="draft", created_by=creator,
        )
        res = self.client.post(f"/marketing/campaigns/{camp.id}/approve/")
        self.assertEqual(res.status_code, 200, res.data)

    @override_settings(CAMPAIGN_MAKER_CHECKER_THRESHOLD=1)
    def test_broadcast_over_threshold_becomes_draft(self):
        qualified_lead(brand_name="A", email="a@x.com")
        qualified_lead(brand_name="B", email="b@x.com")
        res = self.client.post(
            "/marketing/leads/send_broadcast/",
            {"subject": "Hi", "html_body": "<p>x</p>"}, format="json",
        )
        self.assertEqual(res.status_code, 202, res.data)
        self.assertTrue(res.data.get("requires_approval"))
        camp = EmailCampaign.objects.get(id=res.data["campaign_id"])
        self.assertEqual(camp.status, "draft")


import base64
import hashlib
import hmac
import time
import json as _json


def _svix_headers(body: bytes, secret: str, msg_id="msg_1", ts=None):
    ts = ts or str(int(time.time()))
    key = base64.b64decode(secret[len("whsec_"):])
    signed = f"{msg_id}.{ts}.{body.decode('utf-8')}"
    sig = base64.b64encode(
        hmac.new(key, signed.encode("utf-8"), hashlib.sha256).digest()
    ).decode()
    return {
        "HTTP_SVIX_ID": msg_id,
        "HTTP_SVIX_TIMESTAMP": ts,
        "HTTP_SVIX_SIGNATURE": f"v1,{sig}",
    }


class ResendWebhookTests(TestCase):
    SECRET = "whsec_" + base64.b64encode(b"testsecret").decode()

    def _post(self, payload, headers=None):
        body = _json.dumps(payload).encode()
        if headers is None:
            headers = _svix_headers(body, self.SECRET)
        return APIClient().post(
            "/marketing/webhooks/resend/",
            body,
            content_type="application/json",
            **headers,
        )

    @override_settings(RESEND_WEBHOOK_SECRET=SECRET)
    def test_bounce_suppresses_address(self):
        lead = qualified_lead(email="bounce@x.com")
        res = self._post({
            "type": "email.bounced",
            "data": {"to": ["bounce@x.com"], "email_id": "e1"},
        })
        self.assertEqual(res.status_code, 200, res.content)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "Suppressed")
        self.assertIsNotNone(find_suppression(lead))
        # Suppressed email can never be mailed again, even direct-send.
        ok, _ = lead_contactable(lead)
        self.assertFalse(ok)

    @override_settings(RESEND_WEBHOOK_SECRET=SECRET)
    def test_complaint_suppresses_address(self):
        lead = qualified_lead(email="spam@x.com")
        res = self._post({
            "type": "email.complained", "data": {"to": ["spam@x.com"]}
        })
        self.assertEqual(res.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "Suppressed")

    @override_settings(RESEND_WEBHOOK_SECRET=SECRET)
    def test_invalid_signature_rejected(self):
        res = self._post(
            {"type": "email.bounced", "data": {"to": ["a@x.com"]}},
            {"HTTP_SVIX_ID": "m", "HTTP_SVIX_TIMESTAMP": str(int(time.time())),
             "HTTP_SVIX_SIGNATURE": "v1,bogus"},
        )
        self.assertEqual(res.status_code, 401)

    @override_settings(RESEND_WEBHOOK_SECRET="")
    def test_unconfigured_webhook_refuses_events(self):
        res = APIClient().post(
            "/marketing/webhooks/resend/", {}, format="json"
        )
        self.assertEqual(res.status_code, 503)
