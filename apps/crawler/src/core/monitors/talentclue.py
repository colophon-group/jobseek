"""TalentClue ATS monitor (Spanish-market recruiting platform).

TalentClue customers embed a JavaScript widget on their careers pages:

    <div id="tc-jswidget"
         data-client-id="3277d5dd7c62b36c4e13b1f9b8a7f3e4"
         data-lang="es"
         data-job-listing="1"></div>
    <script src="https://careers.talentclue.com/sites/static/widget/jswidget.min.js"></script>

The widget POSTs to the TalentClue Drupal 7 Services API:

    POST https://api.talentclue.com/jswidget-ajax/jswidget/jobs/{client_id}/{base64_filter}
    Headers: Content-Type: application/json, Accept: application/json

``{base64_filter}`` is a base64-encoded JSON object describing the list query;
the default filter (no search terms, primary widget pane, all job types)
returns every active posting.

Without an ``Accept: application/json`` header, the same endpoint returns
XML — hence the PR history note that "the API returns XML".  We request
JSON explicitly so the response is a plain ``{"jobs": {"<id>": {...}, ...}}``.

Job detail pages live at ``https://{customer}.talentclue.com/{lang}/node/
{job_id}/{variant_id}`` and each job payload embeds the canonical URL in its
``url`` field, so the monitor does not need to reconstruct it.
"""

from __future__ import annotations

import base64
import datetime
import json
import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 50_000

_API_BASE = "https://api.talentclue.com"

# Matches ``data-client-id="<32-hex>"`` (plus escaped variants) on the widget div.
_CLIENT_ID_RE = re.compile(
    r'data-client-id=(?:&quot;|&#34;|")([a-f0-9]{32})(?:&quot;|&#34;|")',
    re.IGNORECASE,
)
# Matches ``data-lang="xx"``.
_LANG_RE = re.compile(
    r'data-lang=(?:&quot;|&#34;|")([a-z]{2})(?:&quot;|&#34;|")',
    re.IGNORECASE,
)

# Widget script marker — indicates a page embeds the TalentClue widget.
_WIDGET_SCRIPT_RE = re.compile(
    r"careers\.talentclue\.com/sites/static/widget/jswidget\.min\.js",
    re.IGNORECASE,
)

_IGNORE_SUBDOMAINS = frozenset(
    {"www", "api", "careers", "welcome", "storage", "cdn", "static", "assets"}
)

# Widget tuning knobs.
_DEFAULT_LANG = "es"
_CLOSED_JOBS_MONTHS = "12"


def _default_filter(lang: str) -> dict:
    """Build the default list filter payload.

    Mirrors the request the widget sends when a visitor arrives on the
    jobs page with no search terms active.  ``subs`` selects the primary
    widget pane; everything else is an empty selection / default.
    """
    return {
        "op": 1,
        "position": [],
        "cdef": False,
        "subs": "1",
        "lang": lang,
        "industry": [],
        "department": [],
        "contract": False,
        "countries": None,
        "provinces": [],
        "includeOnlyClosedJobs": False,
        "closedJobsOfTheLastMonths": _CLOSED_JOBS_MONTHS,
        "showArchivedJobs": False,
    }


def _encode_filter(filter_obj: dict) -> str:
    """Encode a filter dict as the base64 path segment expected by the API."""
    # The widget uses the standard browser ``btoa`` with compact (no-space)
    # JSON separators — match that exactly so cached responses line up.
    serialized = json.dumps(filter_obj, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(serialized.encode("utf-8")).decode("ascii")


def _api_url(client_id: str, filter_obj: dict) -> str:
    return f"{_API_BASE}/jswidget-ajax/jswidget/jobs/{client_id}/{_encode_filter(filter_obj)}"


# ── Response parsing ─────────────────────────────────────────────────────


def _parse_locations(raw: dict) -> list[str] | None:
    """Collect location strings from the job payload.

    TalentClue exposes city / province / country separately.  We prefer the
    most specific value (city) and fall back up the chain.  When both city
    and province are present and different we keep them joined — this is
    how the widget renders them to users.
    """
    city = (raw.get("city") or "").strip()
    province = (raw.get("province_label") or "").strip()
    country = (raw.get("country_label") or "").strip()

    if city and province and city.lower() != province.lower():
        return [f"{city}, {province}"]
    if city:
        return [city]
    if province:
        return [province]
    if country:
        return [country]
    return None


_WORK_MODALITY_MAP: dict[str, str] = {
    "presencial": "On-site",
    "remoto": "Remote",
    "teletrabajo": "Remote",
    "híbrido": "Hybrid",
    "hibrido": "Hybrid",
    "mixto": "Hybrid",
}


def _parse_job_location_type(raw: dict) -> str | None:
    """Normalise the Spanish ``work_modality`` label to standard values."""
    modality = (raw.get("work_modality") or "").strip().lower()
    return _WORK_MODALITY_MAP.get(modality)


_SHIFT_MAP: dict[str, str] = {
    "jornada completa": "Full-time",
    "jornada parcial": "Part-time",
    "media jornada": "Part-time",
    "completa": "Full-time",
    "parcial": "Part-time",
    "full-time": "Full-time",
    "part-time": "Part-time",
    "full time": "Full-time",
    "part time": "Part-time",
}

_CONTRACT_MAP: dict[str, str] = {
    # Internships override any shift-based full/part-time value.
    "prácticas": "Intern",
    "practicas": "Intern",
    "becario": "Intern",
    "beca": "Intern",
}


def _parse_employment_type(raw: dict) -> str | None:
    """Derive the standardised employment_type from contract/shift labels."""
    contract = (raw.get("contract_label") or "").strip().lower()
    for needle, label in _CONTRACT_MAP.items():
        if needle in contract:
            return label
    shift = (raw.get("shift_label") or "").strip().lower()
    return _SHIFT_MAP.get(shift)


def _parse_date_posted(raw: dict) -> str | None:
    """Convert ``post_date`` (DD/MM/YYYY) to ISO ``YYYY-MM-DD``."""
    post_date = raw.get("post_date")
    if isinstance(post_date, str):
        match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", post_date.strip())
        if match:
            day, month, year = match.groups()
            return f"{year}-{month}-{day}"
    ts = raw.get("post_date_timestamp")
    if ts:
        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            return None
        # Fall back to timestamp → date string.
        return datetime.datetime.fromtimestamp(ts_int, tz=datetime.UTC).date().isoformat()
    return None


def _parse_job(job_id: str, raw: dict) -> DiscoveredJob | None:
    """Parse a single job dict from the API response."""
    url = raw.get("url")
    if not url or not isinstance(url, str):
        return None

    title = raw.get("title")
    if title and isinstance(title, str):
        title = title.strip() or None

    # Metadata — keep IDs and facets useful for later scraping/enrichment.
    metadata: dict = {"id": str(job_id)}
    for src_key, meta_key in (
        ("company_id", "company_id"),
        ("parent_company_id", "parent_company_id"),
        ("parent_company_name", "parent_company_name"),
        ("subgroup", "subgroup"),
        ("department", "department"),
        ("discipline_label", "discipline"),
        ("industry_label", "industry"),
        ("position", "position"),
        ("vacancy", "vacancies"),
    ):
        val = raw.get(src_key)
        if val not in (None, "", False):
            metadata[meta_key] = val
    geo = raw.get("geolocation")
    if isinstance(geo, dict) and geo.get("lat") is not None and geo.get("lng") is not None:
        metadata["geolocation"] = {"lat": geo["lat"], "lng": geo["lng"]}

    language = raw.get("language") or None
    if language and isinstance(language, str):
        language = language.strip().lower() or None

    return DiscoveredJob(
        url=url,
        title=title,
        description=None,  # Description lives on the detail page; the scraper fills it in.
        locations=_parse_locations(raw),
        employment_type=_parse_employment_type(raw),
        job_location_type=_parse_job_location_type(raw),
        date_posted=_parse_date_posted(raw),
        language=language,
        metadata=metadata or None,
    )


def _parse_jobs_payload(payload: dict) -> list[DiscoveredJob]:
    """Extract jobs from the ``{"jobs": {id: {...}}}`` response shape."""
    jobs_map = payload.get("jobs")
    if not isinstance(jobs_map, dict):
        return []
    out: list[DiscoveredJob] = []
    for jid, raw in jobs_map.items():
        if isinstance(raw, dict):
            parsed = _parse_job(str(jid), raw)
            if parsed is not None:
                out.append(parsed)
    return out


# ── API fetch ─────────────────────────────────────────────────────────────


async def _fetch_jobs(client_id: str, lang: str, client: httpx.AsyncClient) -> list[DiscoveredJob]:
    """POST the default filter and return parsed DiscoveredJob entries."""
    filter_obj = _default_filter(lang)
    url = _api_url(client_id, filter_obj)
    # The API is content-negotiated: plain POST returns XML; we want JSON.
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    response = await client.post(url, headers=headers)
    response.raise_for_status()
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return []
    return _parse_jobs_payload(payload)


# ── Detection helpers ────────────────────────────────────────────────────


def _extract_widget_metadata(html: str) -> dict | None:
    """Scan rendered HTML for the TalentClue widget and return its config."""
    # Accept the widget if either the script URL or the tc-jswidget div is
    # present — some pages lazy-load the widget JS.
    if not _WIDGET_SCRIPT_RE.search(html) and not re.search(
        r'id=(?:&quot;|")tc-jswidget(?:&quot;|")', html
    ):
        return None
    client_match = _CLIENT_ID_RE.search(html)
    if not client_match:
        return None
    result: dict = {"client_id": client_match.group(1).lower()}
    lang_match = _LANG_RE.search(html)
    if lang_match:
        result["lang"] = lang_match.group(1).lower()
    return result


def _extract_subdomain_from_url(url: str) -> str | None:
    """Return the customer subdomain for ``*.talentclue.com`` detail URLs."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host.endswith(".talentclue.com"):
        return None
    sub = host.removesuffix(".talentclue.com")
    if not sub or sub in _IGNORE_SUBDOMAINS:
        return None
    return sub


async def _probe_client_id(client_id: str, lang: str, client: httpx.AsyncClient) -> int | None:
    """Probe the jobs API for a client_id. Returns the job count or None."""
    try:
        jobs = await _fetch_jobs(client_id, lang, client)
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    return len(jobs)


# ── Monitor entrypoints ──────────────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch TalentClue job listings for the configured client_id.

    Config keys (in ``board["metadata"]``):

    ``client_id`` (required)
        32-character hex client ID from the ``tc-jswidget`` widget div.
    ``lang`` (optional, default ``"es"``)
        Widget language code (also used as the ``lang`` field in the API
        filter payload).
    """
    metadata = board.get("metadata") or {}
    client_id = metadata.get("client_id")
    if not client_id:
        raise ValueError(
            f"Cannot derive TalentClue client_id from board URL "
            f"{board.get('board_url')!r} and no client_id in metadata"
        )
    lang = metadata.get("lang") or _DEFAULT_LANG

    jobs = await _fetch_jobs(client_id, lang, client)

    if len(jobs) > MAX_JOBS:
        log.warning(
            "talentclue.truncated",
            client_id=client_id,
            total=len(jobs),
            cap=MAX_JOBS,
        )
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    log.info("talentclue.listed", client_id=client_id, lang=lang, jobs=len(jobs))
    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect a TalentClue widget on the given URL.

    Detection strategy:

    1. Scan the page HTML for the ``tc-jswidget`` div (or the widget script
       URL) plus a ``data-client-id`` attribute.
    2. If the URL itself points at ``*.talentclue.com`` (direct detail /
       listing page), treat it as a TalentClue board and report the
       subdomain (discover() still needs a ``client_id`` to run).
    """
    if client is None:
        # Offline mode — only the direct subdomain signal is usable.
        subdomain = _extract_subdomain_from_url(url)
        if subdomain:
            return {"subdomain": subdomain}
        return None

    html = await fetch_page_text(url, client)
    if html:
        widget = _extract_widget_metadata(html)
        if widget:
            lang = widget.get("lang") or _DEFAULT_LANG
            count = await _probe_client_id(widget["client_id"], lang, client)
            result: dict = {"client_id": widget["client_id"], "lang": lang}
            if count is not None:
                result["jobs"] = count
            log.info(
                "talentclue.detected_in_page",
                url=url,
                client_id=widget["client_id"],
                jobs=count,
            )
            return result

    subdomain = _extract_subdomain_from_url(url)
    if subdomain:
        # Hosted landing page — flag as TalentClue but no client_id yet;
        # operator can supply one manually.
        return {"subdomain": subdomain}

    return None


register("talentclue", discover, cost=12, can_handle=can_handle, rich=False)
