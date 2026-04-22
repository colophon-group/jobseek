"""Phenom People career-site monitor.

Phenom is an enterprise ATS (Fortune-500 customers) whose career sites are
hosted on vanity domains (``careers.<company>.<tld>``) and backed by a
React SPA.  The SSR HTML embeds a ``window.__PRELOAD_STATE__`` JSON blob
with the first page of results, and the SPA paginates via a POST endpoint:

    POST {origin}/api/get-jobs?radius=15&page_number=N&enable_kilometers=false
    Content-Type: application/json
    Body:    {"disable_switch_search_mode": false,
              "site_available_languages": ["en", "en-us"]}
    Returns: {"jobs": [...], "facets": [...], "totalJob": N}

The API is cookie/CSRF-protected and fingerprints the TLS handshake —
direct ``httpx`` calls return HTTP 403 (WAF).  The monitor navigates the
site with a real-Chrome Playwright context, then issues each paginated
request via ``page.evaluate(fetch(...))`` so requests originate from the
page's own JS origin and inherit the session cookies / referer.

Page size is fixed at 10; iteration stops once the API returns an empty
``jobs`` array or we have accumulated ``totalJob`` items.  Registered as
a **rich** monitor — the API returns title, HTML description, and full
location blocks, so no scraper step is needed.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.shared.browser import open_page

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_JOBS = 50_000
PAGE_SIZE = 10  # Phenom's API is hard-wired to 10 per page
MAX_PAGES = MAX_JOBS // PAGE_SIZE  # safety ceiling

# Persistent-context launch mimics a real-Chrome profile, which is required
# to pass the Phenom WAF's TLS/JS fingerprint checks.
_BROWSER_CONFIG = {
    "persistent_context": True,
    "channel": "chrome",
    "headless": True,
}

# Phenom signature: every career page renders ``window.__PRELOAD_STATE__``
# with a ``jobSearch`` slice that exposes ``totalJob`` and the first batch
# of ``jobs``.  We require all three markers to avoid false positives on
# other React SSR sites that also expose a ``__PRELOAD_STATE__`` global.
_SIGNATURE_PATTERNS = (
    re.compile(r"window\.__PRELOAD_STATE__\s*="),
    re.compile(r'"jobSearch"\s*:\s*\{'),
    re.compile(r'"totalJob"\s*:'),
)

_PRELOAD_RE = re.compile(
    r"window\.__PRELOAD_STATE__\s*=\s*(\{.*?\})\s*;\s*window\.__",
    re.DOTALL,
)

_IN_PAGE_FETCH = """async (pageNumber) => {
    const resp = await fetch(
        `/api/get-jobs?radius=15&page_number=${pageNumber}&enable_kilometers=false`,
        {
            method: 'POST',
            headers: {
                'content-type': 'application/json',
                'accept': 'application/json, text/plain, */*'
            },
            body: JSON.stringify({
                disable_switch_search_mode: false,
                site_available_languages: ['en', 'en-us']
            })
        }
    );
    return { status: resp.status, data: resp.ok ? await resp.json() : null };
}"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _origin_from_url(url: str) -> str:
    """Return ``scheme://host[:port]`` for building absolute job URLs."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Cannot derive origin from URL: {url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _job_url(raw: dict, origin: str) -> str | None:
    """Build the canonical job URL from an API record.

    Phenom returns ``originalURL`` as a *relative* path like
    ``some-title/job/P8-123-0``.  We join it against the board origin.
    Falls back to the ``applyURL`` when no ``originalURL`` is present,
    though in practice every record we have observed has one.
    """
    original = (raw.get("originalURL") or "").strip()
    if original:
        if original.startswith("http://") or original.startswith("https://"):
            return original
        return f"{origin}/{original.lstrip('/')}"
    apply_url = (raw.get("applyURL") or "").strip()
    return apply_url or None


def _parse_locations(raw: dict) -> list[str] | None:
    """Prefer human-friendly ``locationText``, fall back to composite fields."""
    locations: list[str] = []
    seen: set[str] = set()
    for loc in raw.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        text = (
            (loc.get("locationText") or loc.get("locationParsedText") or "").strip()
            or (loc.get("cityState") or loc.get("cityStateAbbr") or "").strip()
            or (loc.get("city") or "").strip()
        )
        if text and text not in seen:
            locations.append(text)
            seen.add(text)
    return locations or None


def _parse_employment(raw: dict) -> str | None:
    """Phenom's ``employmentType`` is a list; surface the first entry."""
    value = raw.get("employmentType")
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
        if isinstance(first, dict):
            name = first.get("name") or first.get("value")
            if isinstance(name, str) and name.strip():
                return name.strip()
    elif isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_job(raw: dict, origin: str) -> DiscoveredJob | None:
    """Parse a single Phenom API item into a :class:`DiscoveredJob`."""
    url = _job_url(raw, origin)
    if not url:
        return None

    metadata: dict = {}
    for key in ("sourceID", "uniqueID", "reference", "requisitionID", "companyID"):
        value = raw.get(key)
        if value not in (None, ""):
            metadata[key] = value

    lang = raw.get("lang")
    language = lang if isinstance(lang, str) and lang.strip() else None

    remote_flag = raw.get("isRemote")
    # ``isRemote`` arrives as either bool or string; normalise to a Schema.org
    # compatible value so downstream enrichment can map it.
    job_location_type = None
    if remote_flag is True or (isinstance(remote_flag, str) and remote_flag.lower() == "true"):
        job_location_type = "TELECOMMUTE"

    return DiscoveredJob(
        url=url,
        title=(raw.get("title") or "").strip() or None,
        description=raw.get("description") or None,
        locations=_parse_locations(raw),
        employment_type=_parse_employment(raw),
        job_location_type=job_location_type,
        language=language,
        metadata=metadata or None,
    )


# ---------------------------------------------------------------------------
# Signature detection
# ---------------------------------------------------------------------------


def _matches_signature(html: str | None) -> bool:
    """Return True when *html* carries the full Phenom SSR signature."""
    if not html:
        return False
    return all(pat.search(html) for pat in _SIGNATURE_PATTERNS)


def _parse_preload_state(html: str) -> dict | None:
    """Extract ``window.__PRELOAD_STATE__`` JSON, or None on failure."""
    match = _PRELOAD_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Discover
# ---------------------------------------------------------------------------


async def _fetch_page(page, page_number: int) -> tuple[int, dict | None]:
    """Fetch one Phenom API page via in-page ``fetch``.

    Returns ``(status, data)`` where ``data`` is the parsed JSON body
    (``None`` on non-200 status).
    """
    result = await page.evaluate(_IN_PAGE_FETCH, page_number)
    status = int(result.get("status") or 0)
    data = result.get("data")
    return status, data


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Discover jobs from a Phenom People career site.

    The board URL is used verbatim as the landing page — this is where we
    navigate to establish WAF cookies before paginating the API.  Callers
    that wire up this monitor without providing a Playwright instance get
    a ``RuntimeError``: Phenom cannot be scraped without a real browser
    (direct ``httpx`` calls return HTTP 403).
    """
    if pw is None:
        raise RuntimeError(
            "phenom monitor requires Playwright (pw) — boards must be dispatched "
            "to the browser worker queue"
        )

    board_url = board["board_url"]
    origin = _origin_from_url(board_url)

    jobs: list[DiscoveredJob] = []
    seen_urls: set[str] = set()

    async with open_page(pw, _BROWSER_CONFIG) as page:
        # Warm-up navigation: establishes the Akamai/WAF session cookies
        # that ``/api/get-jobs`` requires.
        await page.goto(board_url, wait_until="domcontentloaded", timeout=45_000)
        # Let late-firing inline scripts finish registering cookies.
        await page.wait_for_timeout(2000)

        # Discover total count from the first API page (``__PRELOAD_STATE__``
        # includes an initial ``totalJob`` but the live API is authoritative
        # and respects the real-time index state).
        status, data = await _fetch_page(page, 1)
        if status != 200 or data is None:
            log.warning(
                "phenom.first_page_failed",
                url=board_url,
                status=status,
            )
            return []

        total_job = data.get("totalJob")
        if not isinstance(total_job, int):
            total_job = None
        last_page = (
            min(MAX_PAGES, (total_job + PAGE_SIZE - 1) // PAGE_SIZE) if total_job else MAX_PAGES
        )

        for page_number in range(1, last_page + 1):
            if page_number > 1:
                status, data = await _fetch_page(page, page_number)
                if status != 200 or data is None:
                    log.warning(
                        "phenom.page_failed",
                        url=board_url,
                        page=page_number,
                        status=status,
                    )
                    break
            batch = data.get("jobs") or []
            if not batch:
                # Empty response = pagination exhausted (some tenants
                # overshoot the declared ``totalJob`` by a page).
                break
            new_count = 0
            for raw in batch:
                parsed = _parse_job(raw, origin)
                if not parsed or parsed.url in seen_urls:
                    continue
                seen_urls.add(parsed.url)
                jobs.append(parsed)
                new_count += 1
            if new_count == 0:
                # Entire page duplicated — defensive stop, avoids infinite
                # loops if the API begins returning a cursor-stable page.
                break
            if len(jobs) >= MAX_JOBS:
                log.warning("phenom.truncated", url=board_url, total=len(jobs), cap=MAX_JOBS)
                break

    log.info("phenom.discovered", url=board_url, jobs=len(jobs), total_job=total_job)
    return jobs


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect Phenom by scanning the career page for the SSR signature."""
    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if not _matches_signature(html):
        return None

    state = _parse_preload_state(html or "")
    total_job: int | None = None
    if state:
        js = state.get("jobSearch") or {}
        tj = js.get("totalJob")
        if isinstance(tj, int):
            total_job = tj

    result: dict = {"api_path": "/api/get-jobs"}
    if total_job is not None:
        result["jobs"] = total_job
    log.info("phenom.detected", url=url, jobs=total_job)
    return result


register("phenom", discover, cost=15, can_handle=can_handle, rich=True)
