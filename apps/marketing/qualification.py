"""Lead qualification heuristics (MKT-02).

A scraped profile only counts as a lead when it is a *distinct* designer or
label that creates/sells its own fashion work. Directory, community, ranking,
agency and generic-location accounts are hard-rejected at ingestion; genuinely
ambiguous profiles are queued for human review instead of being emailed.

Every decision (auto or human) is written to ``LeadQualificationDecision`` so
the qualified-leads-per-100-candidates yield can be measured per source.
"""
import re

# Search engines rank directory-style accounts highly for broad location
# queries. Qualification must happen on the extracted profile, before a lead
# is persisted.
DIRECTORY_RE = re.compile(
    r"\b(?:directory|discover designers|find designers|top designers|"
    r"best designers|fashion designers\b|bridal designers\b|"
    r"designers in|list of designers|featuring designers|"
    r"promoting designers|connect(?:ing)? you (?:with|to) designers)\b", re.I
)
COMMUNITY_RE = re.compile(
    r"\b(?:community|network|association|collective|guild|forum|group)\b", re.I
)
AGGREGATOR_RE = re.compile(
    r"\b(?:aggregator|marketplace|platform for designers|"
    r"shop (?:from|multiple) designers|multi.designer)\b", re.I
)
AGENCY_RE = re.compile(
    r"\b(?:talent agency|model(?:ing)? agency|pr agency|"
    r"management agency|we represent)\b", re.I
)
GENERIC_NAME_RE = re.compile(
    r"^(?:(?:best|top|leading|affordable)\s+)?(?:fashion|bridal|clothing)\s+"
    r"designers?\s+(?:in|at|from)\s+.+$|^(?:lagos|nigeria|abuja|accra|ghana|"
    r"nairobi|kenya|dakar|senegal|johannesburg|cape town|south africa)\s+"
    r"(?:fashion|bridal)\s+designers?$", re.I
)
DIRECT_WORK_RE = re.compile(
    r"\b(?:bespoke|couture|atelier|tailor(?:ing)?|made.to.order|custom.made|"
    r"ready.to.wear|rtw|bridalwear|wedding dresses|fashion house|"
    r"clothing brand|fashion label|we (?:make|design|sew|create)|"
    r"shop (?:our|the) collection|order (?:your|a) dress)\b", re.I
)
DESIGNER_ROLE_RE = re.compile(
    r"\b(?:fashion designer|clothing designer|bridal designer|"
    r"fashion brand|creative director|founder)\b", re.I
)

# Qualification types that must never be persisted as outreach leads.
HARD_REJECT_TYPES = {
    'directory', 'community', 'aggregator', 'agency', 'generic_account',
    'not_a_lead',
}


def classify_instagram_profile(item: dict) -> tuple:
    """Classify a structured Instagram profile record.

    Returns ``(qualification_type, reasons)`` where reasons are human-readable
    strings for the reviewer/audit trail.
    """
    name = str(item.get('full_name') or '').strip()
    handle = str(item.get('account') or '').strip().lstrip('@')
    bio = str(item.get('biography') or '').strip()
    category = str(item.get('business_category_name') or item.get('category_name')
                   or item.get('category') or '')
    identity = f'{name} {handle.replace("_", " ").replace(".", " ")}'
    haystack = f'{identity} {bio} {category}'

    if DIRECTORY_RE.search(haystack):
        return 'directory', ['directory/ranking signals in name, bio or category']
    if COMMUNITY_RE.search(haystack) and not DIRECT_WORK_RE.search(bio):
        return 'community', ['community/group account without own-work evidence']
    if AGGREGATOR_RE.search(haystack):
        return 'aggregator', ['marketplace/aggregator account']
    if AGENCY_RE.search(haystack):
        return 'agency', ['agency/management account, not a designer']
    if GENERIC_NAME_RE.match(name) and not DIRECT_WORK_RE.search(bio):
        return 'generic_account', ['generic location account name without own-work evidence']

    direct = bool(
        DIRECT_WORK_RE.search(bio)
        or DESIGNER_ROLE_RE.search(f'{bio} {category}')
    )
    if not direct:
        return 'uncertain', ['no direct-work evidence found — needs human review']
    if not (name or handle):
        return 'uncertain', ['no identifiable name or handle']
    return 'direct_designer', ['evidence of a distinct designer/label with own work']


def classify_existing_lead(lead) -> tuple:
    """Re-classify a persisted DesignerLead from its stored fields and, when
    available, the raw enrichment text captured at scrape time."""
    raw_text = ''
    try:
        raw_text = (lead.enrichment.raw_text or '')[:4000]
    except Exception:
        pass
    item = {
        'full_name': lead.brand_name or '',
        'account': lead.instagram_handle or '',
        'biography': raw_text,
        'category': ' '.join(lead.category_tags or []),
    }
    qtype, reasons = classify_instagram_profile(item)
    # Requalification never sees the live profile, so downgrade to uncertain
    # when the only "evidence" is thin stored metadata.
    if qtype == 'direct_designer' and not raw_text and not lead.email:
        return 'uncertain', ['insufficient stored evidence — needs human review']
    return qtype, reasons


def llm_result_qualification(data: dict) -> tuple:
    """Map a Gemini extraction result to a qualification type."""
    if not data:
        return 'not_a_lead', ['no extractable page data']
    if data.get('is_direct_designer') is not True:
        return 'not_a_lead', ['LLM did not confirm a direct designer/brand']
    if not (data.get('brand_name') or '').strip():
        return 'uncertain', ['LLM found no brand name']
    return 'direct_designer', ['LLM confirmed direct designer/brand']
