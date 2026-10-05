import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from functools import partial
from urllib.parse import urlparse
from django.utils import timezone
from django.conf import settings
from django.db import close_old_connections, connection, transaction, IntegrityError
from django.db.models import F, Q

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

# Domains that identify the platform, not the brand — never dedupe on these.
PLATFORM_DOMAINS = {
    "instagram.com", "facebook.com", "fb.com", "tiktok.com", "twitter.com",
    "x.com", "youtube.com", "linkedin.com", "pinterest.com", "threads.net",
}

EMAIL_RE = re.compile(r"[\w.-]+@[\w.-]+\.[\w]{2,}")

# Search engines rank directory-style accounts highly for broad location queries.
# Qualification must happen on the extracted profile, before a lead is persisted.
AGGREGATOR_RE = re.compile(
    r"\b(?:directory|community|network|association|group|forum|marketplace|"
    r"discover designers|find designers|top designers|best designers|"
    r"fashion designers\b|bridal designers\b|"
    r"designers in|fashion designers in|list of designers|featuring designers|"
    r"promoting designers|connect(?:ing)? you (?:with|to) designers)\b", re.I
)
GENERIC_NAME_RE = re.compile(
    r"^(?:(?:best|top|leading|affordable)\s+)?(?:fashion|bridal|clothing)\s+"
    r"designers?\s+(?:in|at|from)\s+.+$|^(?:lagos|nigeria|abuja)\s+"
    r"(?:fashion|bridal)\s+designers?$", re.I
)
DIRECT_WORK_RE = re.compile(
    r"\b(?:bespoke|couture|atelier|tailor(?:ing)?|made.to.order|custom.made|"
    r"ready.to.wear|rtw|bridalwear|wedding dresses|fashion house|"
    r"clothing brand|fashion label|we (?:make|design|sew|create)|"
    r"shop (?:our|the) collection|order (?:your|a) dress)\b", re.I
)


def _is_direct_instagram_designer(item: dict) -> bool:
    """Require a distinct designer identity and evidence of its own work."""
    name = str(item.get('full_name') or '').strip()
    handle = str(item.get('account') or '').strip().lstrip('@')
    bio = str(item.get('biography') or '').strip()
    category = str(item.get('business_category_name') or item.get('category_name')
                   or item.get('category') or '')
    identity = f'{name} {handle.replace("_", " ").replace(".", " ")}'
    if AGGREGATOR_RE.search(f'{identity} {bio} {category}'):
        return False
    if GENERIC_NAME_RE.match(name) and not DIRECT_WORK_RE.search(bio):
        return False
    if not (DIRECT_WORK_RE.search(bio) or re.search(
        r'\b(?:fashion designer|clothing designer|bridal designer|fashion brand)\b',
        f'{bio} {category}', re.I
    )):
        return False
    return bool(name or handle)


def _is_platform_domain(domain: str) -> bool:
    """Suffix-aware match so subdomains (help.instagram.com etc.) count."""
    return any(domain == d or domain.endswith(f".{d}") for d in PLATFORM_DOMAINS)


class BudgetExceeded(Exception):
    pass


def _url_hash(provider_name: str, url: str) -> str:
    return hashlib.sha256(f"{provider_name}|{url}".encode("utf-8")).hexdigest()


def _unit_cost(provider_name: str) -> tuple:
    """Return (config, cost_per_call_usd) for a provider."""
    try:
        config = ScrapeProviderConfig.objects.get(name=provider_name)
    except ScrapeProviderConfig.DoesNotExist:
        return None, Decimal('0')
    cost = (config.cost_per_1k_credits or Decimal('0')) / Decimal('1000')
    return config, cost


def _consume_budget(job, provider_name: str):
    """Check budget, increment spend atomically and return call cost. Raises BudgetExceeded if over cap."""
    config, cost = _unit_cost(provider_name)
    if not config:
        return cost

    if config.monthly_budget:
        # Atomic compare-and-increment so concurrent jobs can't both pass the check.
        updated = ScrapeProviderConfig.objects.filter(
            id=config.id,
            monthly_spend__lte=config.monthly_budget - cost,
        ).update(monthly_spend=F('monthly_spend') + cost)

        if not updated:
            job.status = 'budget_stopped'
            job.completed_at = timezone.now()
            job.error_message = f"Provider {provider_name} monthly budget exceeded"
            job.save(update_fields=['status', 'completed_at', 'error_message'])
            raise BudgetExceeded(job.error_message)
    else:
        # No cap — still count spend so cost-per-lead reporting works.
        ScrapeProviderConfig.objects.filter(id=config.id).update(
            monthly_spend=F('monthly_spend') + cost
        )

    return cost


def _refund_budget(provider_name: str, cost):
    """Return a consumed budget reservation — for calls that never billed
    (free direct fetches, failed requests)."""
    if not cost:
        return
    ScrapeProviderConfig.objects.filter(name=provider_name).update(
        monthly_spend=F('monthly_spend') - cost
    )


def _is_suppressed(brand_name: str, email: str, url: str) -> bool:
    if LeadSuppression.objects.filter(brand_name__iexact=brand_name).exists():
        return True
    if email and LeadSuppression.objects.filter(email__iexact=email).exists():
        return True
    if url:
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain and LeadSuppression.objects.filter(domain__iexact=domain).exists():
            return True
    return False


def _slim_payload(extracted: dict) -> dict:
    """Keep the ScrapeCall ledger slim — full page text/html is stored on
    LeadEnrichment, not duplicated into every call row. Bright Data's embedded
    record also carries heavy arrays (posts/highlights) we don't need to echo."""
    slim = dict(extracted or {})
    for bulky in ("html", "text", "markdown"):
        content = slim.pop(bulky, None)
        if content:
            slim["content_chars"] = len(content)
    j = slim.get("json")
    if isinstance(j, dict):
        j = dict(j)
        for k in ("posts", "highlights", "post_hashtags", "bio_hashtags"):
            j.pop(k, None)
        slim["json"] = j
    return slim


def _get_cached_or_extract(job, provider, url):
    h = _url_hash(provider.name, url)
    cache = ScrapeCache.objects.filter(url_hash=h).exclude(
        stale_after__lte=timezone.now()
    ).first()
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
            raw_response=_slim_payload(cache.extracted if isinstance(cache.extracted, dict) else {}),
            duration_ms=0,
        )
        return cache.extracted

    # Budget is reserved before the provider call, then settled: failed calls
    # and free direct fetches are refunded so monthly_spend tracks real spend.
    cost = _consume_budget(job, provider.name)

    start = int(time.time() * 1000)
    try:
        extracted = provider.extract(url)
    except Exception as e:
        duration = int(time.time() * 1000) - start
        _refund_budget(provider.name, cost)
        ScrapeCall.objects.create(
            job=job,
            provider_name=provider.name,
            call_type='extract',
            input_url=url,
            status='error',
            error_message=str(e),
            cost_usd=0,
            duration_ms=duration,
            raw_payload={'reserved_cost': str(cost)},
        )
        return None

    duration = int(time.time() * 1000) - start

    if extracted and extracted.pop("_free_call", False):
        _refund_budget(provider.name, cost)
        cost = Decimal('0')

    ScrapeCall.objects.create(
        job=job,
        provider_name=provider.name,
        call_type='extract',
        input_url=url,
        cost_usd=cost,
        status='ok',
        raw_payload={'url_hash': h},
        raw_response=_slim_payload(extracted or {}),
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


def _first_url(value) -> str:
    """Bright Data sometimes returns external_url as a list of links or
    external_urls as [{url, title}] — normalise to a single URL string."""
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("url", "")
    return str(value or "")


def _looks_like_instagram_profile(raw_json) -> bool:
    return isinstance(raw_json, dict) and (
        "account" in raw_json
        or ("biography" in raw_json and "followers" in raw_json)
    )


def _lead_data_from_instagram(item: dict, url: str) -> dict:
    """Map a Bright Data Instagram profile record directly — no LLM needed."""
    bio = item.get("biography") or ""
    account = (item.get("account") or "").lstrip("@")
    email_match = EMAIL_RE.search(bio)
    email = (
        item.get("email_address")     # confirmed present in the dataset payload
        or item.get("business_email")
        or item.get("email")
        or (email_match.group(0) if email_match else "")
    )
    category = (
        item.get("business_category_name")
        or item.get("category_name")
        or item.get("category")
        or ""
    )
    return {
        "brand_name": (item.get("full_name") or account or ""),
        # A profile display name is usually the brand, not the person's name.
        "designer_name": "",
        "email": email,
        "phone_number": item.get("business_phone_number") or item.get("contact_phone_number") or "",
        "social_media_links": {"instagram": url},
        "followers_count": item.get("followers") or 0,
        "category_tags": [category] if category else [],
        "country_code": "",
    }


def _parse_extracted_text(job: ScrapeJob, text: str, url: str):
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
    Only identify the owner of this page. Directories, rankings, communities, group pages, marketplaces, and articles about multiple designers are not leads. Do not select the first designer mentioned on those pages.

    Extract these fields:
    - is_direct_designer: Boolean. True only when this page belongs to a specific designer or fashion brand that creates or sells its own designs.
    - brand_name: Name of the fashion brand.
    - designer_name: Name of the designer (if available).
    - email: Any contact email address.
    - phone_number: Any contact phone number.
    - social_media_links: A JSON object mapping platform names (e.g., "instagram", "twitter") to their URLs.
    - followers_count: Integer (if mentioned).
    - country_code: ISO 3166-1 alpha-2 country code of the designer/brand (e.g., "NG", "GH", "ZA") if identifiable, otherwise empty.
    - category_tags: A list of strings describing the style (e.g., ["Streetwear", "Luxury"]).

    IMPORTANT: If the text does not contain their email, phone_number, or social_media_links, do not invent them. Leave them blank.

    Return ONLY a raw JSON object matching the fields exactly. No markdown formatting, no code blocks, just raw JSON.

    Text to analyze:
    {text}
    """

    start = int(time.time() * 1000)
    chat_model = getattr(settings, "CHAT_GEMINI_MODEL", "gemini-2.5-flash")
    try:
        client = genai.Client(api_key=gemini_key)
        gen_response = client.models.generate_content(
            model=chat_model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )

        duration = int(time.time() * 1000) - start
        usage = getattr(gen_response, "usage_metadata", None)
        ScrapeCall.objects.create(
            job=job,
            provider_name="gemini",
            call_type='llm',
            input_url=url,
            status='ok',
            duration_ms=duration,
            raw_payload={'model': chat_model, 'input_chars': len(text)},
            raw_response={
                'prompt_tokens': getattr(usage, "prompt_token_count", None),
                'completion_tokens': getattr(usage, "candidates_token_count", None),
            },
        )

        output_text = (gen_response.text or "").strip()
        if output_text.startswith("```json"):
            output_text = output_text[7:]
        if output_text.startswith("```"):
            output_text = output_text[3:]
        if output_text.endswith("```"):
            output_text = output_text[:-3]

        data = json.loads(output_text.strip())
        if data.get("is_direct_designer") is not True or not data.get("brand_name"):
            return None
        return data
    except Exception as e:
        duration = int(time.time() * 1000) - start
        try:
            ScrapeCall.objects.create(
                job=job,
                provider_name="gemini",
                call_type='llm',
                input_url=url,
                status='error',
                error_message=str(e),
                duration_ms=duration,
                raw_payload={'model': chat_model},
            )
        except Exception:
            pass
        logger.error(f"Gemini parse error for {url}: {e}")
        return None


def _lead_from_extracted(extracted, provider_name: str, job: ScrapeJob):
    if not extracted:
        return None
    url = extracted.get("url", "")
    raw_json = extracted.get("json") or {}
    text = extracted.get("text") or extracted.get("markdown") or extracted.get("html", "")

    if _looks_like_instagram_profile(raw_json) and not _is_direct_instagram_designer(raw_json):
        return 'skipped'

    # Structured Instagram records are mapped directly; Gemini is only used
    # to parse unstructured page text.
    used_gemini = not _looks_like_instagram_profile(raw_json)
    if used_gemini:
        data = _parse_extracted_text(job, text, url) or {}
        if not data:
            return 'skipped'
    else:
        data = _lead_data_from_instagram(raw_json, url)

    if not data.get("brand_name") and not raw_json:
        return None

    # Where the content actually came from (e.g. "direct" for free fetches).
    source_name = extracted.get("source") or provider_name

    brand = (data.get("brand_name") or "").strip()[:255]
    if not brand and raw_json:
        brand = (raw_json.get("account") or raw_json.get("full_name") or "").strip()[:255]
    if not brand:
        return None

    if _is_suppressed(brand, data.get("email", ""), url):
        logger.info(f"Suppressed lead: {brand}")
        return 'skipped'

    followers = data.get("followers_count")
    if not isinstance(followers, int):
        followers = raw_json.get("followers", 0)
    if not isinstance(followers, int):
        followers = 0

    # Normalise dedupe inputs
    socials = data.get("social_media_links", {}) or {}
    ig_url = socials.get("instagram") or ""
    instagram_handle = ""
    domain = (urlparse(url).netloc or "").lower()
    if domain.startswith("www."):
        domain = domain[4:]
    domain = domain[:100]
    if ig_url:
        # e.g. https://instagram.com/mybrand/ -> mybrand
        instagram_handle = ig_url.rstrip("/").split("/")[-1].lstrip("@").lower()[:100]
    elif "instagram.com" in domain:
        instagram_handle = (urlparse(url).path.strip("/").split("/")[0] or "").lstrip("@").lower()[:100]
    brand_norm = brand.lower()[:100]
    dedupe_parts = [p for p in [brand_norm, instagram_handle, domain] if p]
    dedupe_key = "|".join(dedupe_parts)[:255] if dedupe_parts else None

    # Dedupe on each identifier independently — a composite key misses the
    # same brand found via its website vs its Instagram profile.
    conditions = []
    if instagram_handle:
        conditions.append(Q(instagram_handle__iexact=instagram_handle))
    if brand_norm:
        conditions.append(Q(brand_name__iexact=brand))
    if domain and not _is_platform_domain(domain):
        conditions.append(Q(website__icontains=domain) | Q(dedupe_key__icontains=domain))
    if conditions:
        dup_q = conditions[0]
        for c in conditions[1:]:
            dup_q |= c
        if DesignerLead.objects.filter(dup_q).exists():
            logger.info(f"Lead already exists (dedupe): {brand}")
            return 'skipped'

    # Confidence: only store provenanced values — no invented contacts
    bio = (raw_json.get("biography") or "") if raw_json else ""
    json_email_match = EMAIL_RE.search(bio) if bio else None
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

    try:
        with transaction.atomic():
            lead = DesignerLead.objects.create(
                brand_name=brand,
                designer_name=(data.get("designer_name") or "")[:255],
                email=email,
                phone_number=phone,
                social_media_links=socials,
                website=(_first_url(raw_json.get("external_url"))
                         or _first_url(raw_json.get("external_urls"))
                         or url)[:200],
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
                source=f"{source_name} / Gemini" if used_gemini else source_name,
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
                extraction_source=source_name,
                confidence=confidence,
                enrichment_status='completed',
            )
    except IntegrityError:
        logger.info(f"Lead already exists (unique dedupe_key): {dedupe_key}")
        return 'skipped'
    return lead


def _process_url(job, provider, url: str) -> str:
    """Extract one URL and upsert the lead. Runs in a worker thread —
    returns 'created' | 'skipped' | 'failed' | 'budget_stopped'."""
    try:
        try:
            extracted = _get_cached_or_extract(job, provider, url)
        except BudgetExceeded:
            return 'budget_stopped'
        res = _lead_from_extracted(extracted, provider.name, job)
        if res == 'skipped':
            return 'skipped'
        return 'created' if res else 'failed'
    except Exception as e:
        # One malformed record shouldn't kill the whole job
        logger.warning(f"Lead processing failed for {url}: {e}")
        return 'failed'
    finally:
        # Worker threads must release their DB connection — but never close
        # one that sits inside an atomic block (TestCase txn / ATOMIC_REQUESTS).
        if not connection.in_atomic_block:
            close_old_connections()


def run_scrape_engine(job_id: str):
    try:
        job = ScrapeJob.objects.get(id=job_id)
    except ScrapeJob.DoesNotExist:
        logger.error(f"ScrapeJob {job_id} not found")
        return

    # Atomic claim — guards against duplicate schedulers in multi-process
    # deployments picking up the same queued job.
    claimed = ScrapeJob.objects.filter(id=job.id, status='queued').update(
        status='running', started_at=timezone.now()
    )
    if not claimed:
        logger.info(f"ScrapeJob {job_id} already claimed (status={job.status})")
        return
    job.status = 'running'
    job.started_at = timezone.now()

    try:
        provider = _pick_provider(job.provider_name)
        extract_provider = _select_extract_provider(provider.name) or provider

        if not getattr(extract_provider, 'can_extract', False):
            raise RuntimeError(
                f"Provider '{extract_provider.name}' cannot extract; "
                "enable an extract-capable provider (e.g. brightdata)."
            )

        # Fail fast on missing credentials instead of spending a paid SERP
        # call and recording every URL as a parse failure.
        for p in {provider.name: provider, extract_provider.name: extract_provider}.values():
            health = p.health_check()
            if not health.get("ok"):
                raise RuntimeError(f"Provider {p.name} not configured: {health.get('message')}")

        # Consume search budget before the call
        search_cost = _consume_budget(job, provider.name)

        start = int(time.time() * 1000)
        try:
            urls = provider.search(job.query, max_results=job.max_results)
        except Exception as e:
            _refund_budget(provider.name, search_cost)
            ScrapeCall.objects.create(
                job=job,
                provider_name=provider.name,
                call_type='search',
                raw_payload={'query': job.query, 'max_results': job.max_results, 'reserved_cost': str(search_cost)},
                status='error',
                error_message=str(e),
                cost_usd=0,
                duration_ms=int(time.time() * 1000) - start,
            )
            raise
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

        created = 0
        failed = 0
        skipped = 0
        stopped = False
        seen = set()
        work = []
        for item in urls:
            url = item.get('url', item) if isinstance(item, dict) else item
            if url and url not in seen:
                seen.add(url)
                work.append(url)

        # Extraction is I/O-bound (HTTP + optional LLM) — run a small pool.
        # Budget CAS stays safe across threads; a BudgetExceeded anywhere just
        # surfaces as a 'budget_stopped' result.
        max_workers = int(getattr(settings, 'SCRAPE_MAX_WORKERS', 4) or 1)
        if max_workers > 1 and len(work) > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                outcomes = list(pool.map(partial(_process_url, job, extract_provider), work))
        else:
            outcomes = [_process_url(job, extract_provider, u) for u in work]

        for res in outcomes:
            if res == 'created':
                created += 1
            elif res == 'skipped':
                skipped += 1
            elif res == 'budget_stopped':
                stopped = True
            else:
                failed += 1

        if stopped:
            job.result_summary = {
                'urls_found': len(urls),
                'leads_created': created,
                'skipped': skipped,
                'parse_failures': failed,
                'search_provider': provider.name,
                'extract_provider': extract_provider.name,
                'stopped_reason': 'budget_exceeded'
            }
            job.save(update_fields=['result_summary'])
            return

        job.status = 'completed'
        job.completed_at = timezone.now()
        job.result_summary = {
            'urls_found': len(urls),
            'leads_created': created,
            'skipped': skipped,
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
