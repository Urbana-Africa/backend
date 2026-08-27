import hashlib
import json
import logging
import re
import time
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.conf import settings
from django.db import transaction

from ..models import (
    DesignerLead,
    ScrapeProviderConfig,
    ScrapeJob,
    ScrapeCall,
    ScrapeCache,
    LeadEnrichment,
    LeadSuppression,
)
from .registry import get_provider

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    pass


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _unit_cost(provider_name: str) -> tuple:
    """Return (config, cost_per_call_usd) for a provider."""
    try:
        config = ScrapeProviderConfig.objects.get(name=provider_name)
    except ScrapeProviderConfig.DoesNotExist:
        return None, Decimal('0')
    cost = (config.cost_per_1k_credits or Decimal('0')) / Decimal('1000')
    return config, cost


def _consume_budget(job, provider_name: str):
    """Check budget, increment spend and return call cost. Raises BudgetExceeded if over cap."""
    config, cost = _unit_cost(provider_name)
    if not config or not config.monthly_budget:
        return cost

    if config.monthly_spend + cost > config.monthly_budget:
        job.status = 'budget_stopped'
        job.completed_at = timezone.now()
        job.error_message = f"Provider {provider_name} monthly budget exceeded"
        job.save(update_fields=['status', 'completed_at', 'error_message'])
        raise BudgetExceeded(job.error_message)

    config.monthly_spend += cost
    config.save(update_fields=['monthly_spend'])
    return cost


def _is_suppressed(brand_name: str, email: str, url: str) -> bool:
    if LeadSuppression.objects.filter(brand_name__iexact=brand_name).exists():
        return True
    if email and LeadSuppression.objects.filter(email__iexact=email).exists():
        return True
    if url:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        if domain and LeadSuppression.objects.filter(domain__iexact=domain).exists():
            return True
    return False


def _get_cached_or_extract(job, provider, url):
    h = _url_hash(url)
    cache = ScrapeCache.objects.filter(url_hash=h).first()
    if cache:
        # cache used — still record a call so we can track duplicates
        ScrapeCall.objects.create(
            job=job,
            provider_name=provider.name,
            call_type='extract',
            input_url=url,
            cost_usd=0,
            status='ok',
            raw_payload={'cached': True, 'url_hash': h},
            raw_response=cache.extracted,
            duration_ms=0,
        )
        return cache.extracted

    # Budget is consumed before the paid provider call
    cost = _consume_budget(job, provider.name)

    start = int(time.time() * 1000)
    try:
        extracted = provider.extract(url)
    except BudgetExceeded:
        raise
    except Exception as e:
        duration = int(time.time() * 1000) - start
        ScrapeCall.objects.create(
            job=job,
            provider_name=provider.name,
            call_type='extract',
            input_url=url,
            status='error',
            error_message=str(e),
            duration_ms=duration,
        )
        return None

    duration = int(time.time() * 1000) - start
    ScrapeCall.objects.create(
        job=job,
        provider_name=provider.name,
        call_type='extract',
        input_url=url,
        cost_usd=cost,
        status='ok',
        raw_payload={'url_hash': h},
        raw_response=extracted or {},
        duration_ms=duration,
    )

    if extracted:
        stale_after = timezone.now() + timedelta(hours=24)
        ScrapeCache.objects.update_or_create(
            url_hash=h,
            defaults={
                'url': url,
                'provider_name': provider.name,
                'extracted': extracted,
                'stale_after': stale_after,
            }
        )
    return extracted


def _pick_provider(name: str = ""):
    if name:
        config = ScrapeProviderConfig.objects.filter(name=name, enabled=True).first()
        if not config:
            raise RuntimeError(f"Provider '{name}' not found or not enabled")
        return get_provider(name, config.config)

    # fallback: first enabled by priority
    config = ScrapeProviderConfig.objects.filter(enabled=True).order_by('priority', 'name').first()
    if not config:
        raise RuntimeError("No scrape providers are enabled. Configure at least one in /admin.")
    return get_provider(config.name, config.config)


def _select_extract_provider(search_provider_name: str):
    # If search provider can extract, prefer it. Otherwise choose first extract-capable enabled provider.
    config = ScrapeProviderConfig.objects.filter(enabled=True, name=search_provider_name).first()
    if config:
        from .registry import PROVIDERS
        klass = PROVIDERS[search_provider_name]
        if getattr(klass, 'can_extract', False):
            return get_provider(search_provider_name, config.config)

    for cfg in ScrapeProviderConfig.objects.filter(enabled=True).order_by('priority', 'name'):
        from .registry import PROVIDERS
        klass = PROVIDERS[cfg.name]
        if getattr(klass, 'can_extract', False):
            return get_provider(cfg.name, cfg.config)
    return None


def _parse_extracted_text(text: str, url: str):
    """Use Gemini to parse extracted page text into DesignerLead fields."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("google-genai is not installed.")
        return None

    gemini_key = getattr(settings, "GEMINI_SECRET_KEY", "")
    if not gemini_key:
        logger.error("GEMINI_SECRET_KEY is not configured.")
        return None

    if not text:
        return None

    text = text[:20000]

    prompt = f"""
    You are an expert data extractor. Review the following text extracted from a webpage ({url}) and extract the details of an African fashion designer or fashion brand.
    If there are multiple designers mentioned, just extract the main one or the first one. If no designer is found, return empty strings.

    Extract these fields:
    - brand_name: Name of the fashion brand.
    - designer_name: Name of the designer (if available).
    - email: Any contact email address.
    - phone_number: Any contact phone number.
    - social_media_links: A JSON object mapping platform names (e.g., "instagram", "twitter") to their URLs.
    - followers_count: Integer (if mentioned).
    - category_tags: A list of strings describing the style (e.g., ["Streetwear", "Luxury"]).

    IMPORTANT: If the text does not contain their email, phone_number, or social_media_links, do not invent them. Leave them blank.

    Return ONLY a raw JSON object matching the fields exactly. No markdown formatting, no code blocks, just raw JSON.

    Text to analyze:
    {text}
    """

    try:
        client = genai.Client(api_key=gemini_key)
        chat_model = getattr(settings, "CHAT_GEMINI_MODEL", "gemini-2.5-flash")

        gen_response = client.models.generate_content(
            model=chat_model,
            contents=[prompt],
            config=types.GenerateContentConfig(temperature=0.1),
        )

        output_text = gen_response.text.strip()
        if output_text.startswith("```json"):
            output_text = output_text[7:]
        if output_text.startswith("```"):
            output_text = output_text[3:]
        if output_text.endswith("```"):
            output_text = output_text[:-3]

        data = json.loads(output_text.strip())
        if not data.get("brand_name"):
            return None
        return data
    except Exception as e:
        logger.error(f"Gemini parse error for {url}: {e}")
        return None


def _lead_from_extracted(extracted, provider_name: str, job: ScrapeJob):
    if not extracted:
        return None
    text = extracted.get("text") or extracted.get("markdown") or extracted.get("html", "")
    url = extracted.get("url", "")
    data = _parse_extracted_text(text, url) or {}
    raw_json = extracted.get("json") or {}
    if not data.get("brand_name") and not raw_json:
        return None

    brand = data.get("brand_name", "").strip()[:255]
    if _is_suppressed(brand, data.get("email", ""), url):
        logger.info(f"Suppressed lead: {brand}")
        return None

    followers = data.get("followers_count")
    if not isinstance(followers, int):
        followers = raw_json.get("followers", 0)
    if not isinstance(followers, int):
        followers = 0

    # Normalise dedupe inputs
    from urllib.parse import urlparse
    socials = data.get("social_media_links", {}) or {}
    ig_url = socials.get("instagram") or ""
    instagram_handle = ""
    if ig_url:
        # e.g. https://instagram.com/mybrand/ -> mybrand
        instagram_handle = ig_url.rstrip("/").split("/")[-1].lstrip("@").lower()[:100]
    elif "instagram.com" in domain:
        instagram_handle = (urlparse(url).path.strip("/").split("/")[0] or "").lstrip("@").lower()[:100]
    domain = (urlparse(url).netloc or "").lower()[:100]
    brand_norm = brand.lower()[:100]
    dedupe_parts = [p for p in [brand_norm, instagram_handle, domain] if p]
    dedupe_key = "|".join(dedupe_parts)[:255] if dedupe_parts else None

    if dedupe_key and DesignerLead.objects.filter(dedupe_key=dedupe_key).exists():
        logger.info(f"Lead already exists (dedupe): {dedupe_key}")
        return None

    # Confidence: only store provenanced values — no invented contacts
    bio = (raw_json.get("biography") or "") if raw_json else ""
    json_email_match = re.search(r"[\w.-]+@[\w.-]+\.[\w]{2,}", bio) if bio else None
    json_email = json_email_match.group(0) if json_email_match else ""
    email = (data.get("email") or json_email or "")[:254]
    phone = (data.get("phone_number") or "")[:50]
    confidence = 0.0
    if email:
        confidence += 0.4
    if phone:
        confidence += 0.3
    if instagram_handle or socials:
        confidence += 0.2
    if url:
        confidence += 0.1

    needs_review = not email and not phone

    with transaction.atomic():
        lead = DesignerLead.objects.create(
            brand_name=brand,
            designer_name=(data.get("designer_name") or "")[:255],
            email=email,
            phone_number=phone,
            social_media_links=socials,
            website=(raw_json.get("external_url") or url)[:200],
            instagram_handle=instagram_handle,
            country_code=(data.get("country_code") or "")[:10],
            followers_count=followers,
            category_tags=data.get("category_tags", []),
            confidence_score=confidence,
            provenance={
                'source_url': url,
                'provider': provider_name,
                'fetched_at': timezone.now().isoformat(),
            },
            source=f"{provider_name} / Gemini",
            status="Discovered",
            needs_review=needs_review,
            dedupe_key=dedupe_key,
            last_enriched_at=timezone.now(),
        )
        LeadEnrichment.objects.create(
            lead=lead,
            job=job,
            raw_text=text,
            raw_html=extracted.get("html", ""),
            extraction_source=provider_name,
            enrichment_status='completed',
        )
    return lead


def run_scrape_engine(job_id: str):
    try:
        job = ScrapeJob.objects.get(id=job_id)
    except ScrapeJob.DoesNotExist:
        logger.error(f"ScrapeJob {job_id} not found")
        return

    job.status = 'running'
    job.started_at = timezone.now()
    job.save(update_fields=['status', 'started_at'])

    try:
        provider = _pick_provider(job.provider_name)

        # Consume search budget before the call
        search_cost = _consume_budget(job, provider.name)

        start = int(time.time() * 1000)
        urls = provider.search(job.query, max_results=job.max_results)
        duration = int(time.time() * 1000) - start
        ScrapeCall.objects.create(
            job=job,
            provider_name=provider.name,
            call_type='search',
            raw_payload={'query': job.query, 'max_results': job.max_results},
            raw_response={'count': len(urls), 'urls': [u.get('url', u) for u in urls]},
            status='ok',
            cost_usd=search_cost,
            duration_ms=duration,
        )

        extract_provider = _select_extract_provider(provider.name) or provider
        created = 0
        failed = 0
        seen = set()

        for item in urls:
            url = item.get('url', item) if isinstance(item, dict) else item
            if not url or url in seen:
                continue
            seen.add(url)

            try:
                extracted = _get_cached_or_extract(job, extract_provider, url)
            except BudgetExceeded:
                return

            if _lead_from_extracted(extracted, extract_provider.name, job):
                created += 1
            else:
                failed += 1

        job.status = 'completed'
        job.completed_at = timezone.now()
        job.result_summary = {
            'urls_found': len(urls),
            'leads_created': created,
            'parse_failures': failed,
            'search_provider': provider.name,
            'extract_provider': extract_provider.name,
        }
        job.save(update_fields=['status', 'completed_at', 'result_summary'])

    except BudgetExceeded:
        logger.warning(f"ScrapeJob {job_id} stopped for budget")
        return

    except Exception as e:
        logger.exception(f"ScrapeJob {job_id} failed")
        job.status = 'failed'
        job.error_message = str(e)
        job.completed_at = timezone.now()
        job.save(update_fields=['status', 'error_message', 'completed_at'])
