"""BITE GmbH ATS monitor (Job Search API).

Public JSON API at jobs.b-ite.com — requires a "Job Listing Key" (40-char hex)
that is embedded in widget listing JavaScript on career pages.

Search:  POST https://jobs.b-ite.com/api/v1/postings/search
Detail:  GET  https://jobs.b-ite.com/jobposting/{hash}/json?locale={locale}&contentRendered=true

The search endpoint returns listings without descriptions.  The detail
endpoint returns rendered HTML content, employer info, employment types,
and salary.  N+1 monitor: 1 search (paginated) + N detail fetches.

Key extraction:
  Career pages embed ``data-bite-jobs-api-listing="customer:listing"``
  attributes.  The listing JS at
  ``cs-assets.b-ite.com/{customer}/jobs-api/{listing}.min.js``
  contains the 40-char hex key in a ``createClient({key: "..."})`` call.

Note: Pitchman-hosted portals (jobs.drk.de, jobs.brk.de) use a completely
different API path (/api/v1/jobmarket/) and are multi-employer aggregators.
They are NOT handled by this monitor.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 10_000
CONCURRENCY = 10
PAGE_SIZE = 100

_SEARCH_URL = "https://jobs.b-ite.com/api/v1/postings/search"
_DETAIL_URL = "https://jobs.b-ite.com/jobposting/{hash}/json"

_KEY_RE = re.compile(r'"([0-9a-f]{40})"')

# Widget attribute: data-bite-jobs-api-listing="customer:listing"
_WIDGET_ATTR_RE = re.compile(r"data-bite-jobs-api-listing=(?:&quot;|\")([\w-]+):([\w-]+)")

_PAGE_MARKERS = [
    re.compile(r"static\.b-ite\.com"),
    re.compile(r"cs-assets\.b-ite\.com"),
    re.compile(r"jobs\.b-ite\.com"),
    re.compile(r"cdn\.pitchman\.b-ite\.com"),
    re.compile(r"data-bite-jobs-api-listing"),
]

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "full_time": "full-time",
    "part_time": "part-time",
    "temporary": "temporary",
    "contract": "contract",
    "internship": "internship",
    "mini_job": "part-time",
    "volunteer": "volunteer",
}


# ── Key extraction ───────────────────────────────────────────────────────


def _listing_js_url(customer: str, listing: str) -> str:
    return f"https://cs-assets.b-ite.com/{customer}/jobs-api/{listing}.min.js"


def _extract_key_from_js(js_text: str) -> str | None:
    """Extract the 40-char hex API key from listing JS.

    Three known patterns in BITE listing JS:
      1. var X = "<key>", Y = Z.createClient({key: X})   — variable ref
      2. createClient({key: "<key>"})                     — inline key
      3. var t = "<key>", ... new e({key: t})             — newer format

    When called on a known BITE listing JS file (from cs-assets.b-ite.com),
    the first 40-char hex string is always the API key.  For safety we
    still verify that `createClient` or `{key:` appears somewhere in the file.
    """
    m = _KEY_RE.search(js_text)
    if not m:
        return None
    candidate = m.group(1)
    # Verify this is actually a BITE listing JS (not arbitrary JS)
    if "createClient" in js_text or "{key:" in js_text:
        return candidate
    return None


async def _extract_key_from_html(
    html: str, client: httpx.AsyncClient
) -> tuple[str | None, str | None]:
    """Extract API key from career page HTML.

    Returns (key, customer) or (None, None).
    """
    # Find data-bite-jobs-api-listing="customer:listing"
    m = _WIDGET_ATTR_RE.search(html)
    if not m:
        return None, None

    customer = m.group(1)
    listing = m.group(2)

    # Fetch listing JS
    js_url = _listing_js_url(customer, listing)
    try:
        resp = await client.get(js_url)
        if resp.status_code != 200:
            log.warning("bite.listing_js_failed", url=js_url, status=resp.status_code)
            return None, customer
        key = _extract_key_from_js(resp.text)
        if key:
            return key, customer
    except Exception as exc:
        log.warning("bite.listing_js_error", url=js_url, error=str(exc))

    return None, customer


# ── Parsing ──────────────────────────────────────────────────────────────


def _extract_hash_from_url(url: str) -> str | None:
    """Extract job hash from a BITE job posting URL."""
    m = re.search(r"/jobposting/([a-f0-9]{40,42})", url)
    return m.group(1) if m else None


def _build_location(address: dict | None) -> list[str] | None:
    """Build location string from address object."""
    if not address:
        return None
    city = address.get("city")
    country = address.get("country")
    if not city:
        return None
    if country:
        return [f"{city}, {country.upper()}"]
    return [city]


def _normalize_employment_type(emp_types: list | None) -> str | None:
    """Normalize employment type from detail endpoint."""
    if not emp_types:
        return None
    for raw in emp_types:
        mapped = _EMPLOYMENT_TYPE_MAP.get(raw)
        if mapped:
            return mapped
    return None


def _parse_salary(detail: dict) -> dict | None:
    """Extract salary from detail endpoint baseSalary."""
    base = detail.get("baseSalary")
    if not isinstance(base, dict):
        return None
    currency = base.get("currency")
    if not currency:
        return None
    unit_raw = (base.get("unitText") or "").upper()
    unit = "month"  # BITE default
    if "YEAR" in unit_raw:
        unit = "year"
    elif "HOUR" in unit_raw:
        unit = "hour"
    elif "WEEK" in unit_raw:
        unit = "week"

    min_val = base.get("minValue")
    max_val = base.get("maxValue")
    if min_val is None and max_val is None:
        return None
    return {"currency": currency, "min": min_val, "max": max_val, "unit": unit}


def _parse_detail(detail: dict, url: str) -> DiscoveredJob | None:
    """Parse a detail endpoint response into a DiscoveredJob."""
    title = detail.get("title")
    if not title:
        return None

    # Description from rendered HTML content
    description = None
    content = detail.get("content")
    if isinstance(content, dict):
        html_block = content.get("html")
        if isinstance(html_block, dict):
            description = html_block.get("rendered")
        elif isinstance(html_block, str):
            description = html_block

    # Location
    locations = _build_location(detail.get("address"))

    # Employment type
    employment_type = _normalize_employment_type(detail.get("employmentType"))

    # Date posted
    date_posted = None
    for date_field in ("activatedAt", "createdAt", "startAt"):
        raw = detail.get(date_field)
        if raw and isinstance(raw, str):
            date_posted = raw.split("T")[0] if "T" in raw else raw
            break

    # Salary
    base_salary = _parse_salary(detail)

    # Metadata
    metadata: dict = {}
    identification = detail.get("identification")
    if identification:
        metadata["reference"] = identification
    employer = detail.get("employer")
    if isinstance(employer, dict):
        emp_name = employer.get("name")
        if emp_name:
            metadata["employer"] = emp_name

    return DiscoveredJob(
        url=url,
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        date_posted=date_posted,
        base_salary=base_salary,
        language=detail.get("locale"),
        metadata=metadata or None,
    )


# ── Detail fetching ─────────────────────────────────────────────────────


async def _fetch_detail(
    job_hash: str,
    job_url: str,
    locale: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> DiscoveredJob | None:
    """Fetch a single job's detail, respecting the concurrency semaphore."""
    async with semaphore:
        try:
            detail_url = (
                _DETAIL_URL.format(hash=job_hash) + f"?locale={locale}&contentRendered=true"
            )
            resp = await client.get(detail_url)
            if resp.status_code != 200:
                log.warning("bite.detail_failed", hash=job_hash, status=resp.status_code)
                return None
            return _parse_detail(resp.json(), job_url)
        except Exception as exc:
            log.warning("bite.detail_error", hash=job_hash, error=str(exc))
            return None


# ── Discovery ────────────────────────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Discover jobs from a BITE board.

    1. POST search with key + pagination → collect job URLs and hashes
    2. Fetch detail for each job concurrently (semaphore=10)
    3. Parse into DiscoveredJob
    """
    metadata = board.get("metadata") or {}
    key = metadata.get("key")

    if not key:
        raise ValueError(
            f"BITE monitor requires a 'key' in metadata for board {board['board_url']!r}"
        )

    locale = metadata.get("locale", "de")
    channel = metadata.get("channel", 0)

    # Step 1: Paginated search to collect all job URLs
    job_entries: list[tuple[str, str]] = []  # (hash, url)
    offset = 0

    while True:
        resp = await client.post(
            _SEARCH_URL,
            json={
                "key": key,
                "channel": channel,
                "locale": locale,
                "page": {"num": PAGE_SIZE, "offset": offset},
            },
        )
        resp.raise_for_status()
        data = resp.json()

        postings = data.get("jobPostings", [])
        if not postings:
            break

        for p in postings:
            url = p.get("url")
            if not url:
                continue
            job_hash = _extract_hash_from_url(url)
            if job_hash:
                job_entries.append((job_hash, url))

        page_info = data.get("page", {})
        total = page_info.get("total", 0)
        offset += len(postings)

        if offset >= total or len(job_entries) >= MAX_JOBS:
            break

    if not job_entries:
        log.info("bite.no_jobs", key=key[:8] + "...")
        return []

    if len(job_entries) > MAX_JOBS:
        log.warning("bite.truncated", total=len(job_entries), cap=MAX_JOBS)
        job_entries = job_entries[:MAX_JOBS]

    log.info("bite.listed", key=key[:8] + "...", jobs=len(job_entries))

    # Step 2: Fetch details concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        _fetch_detail(job_hash, job_url, locale, client, semaphore)
        for job_hash, job_url in job_entries
    ]
    results = await asyncio.gather(*tasks)

    # Step 3: Collect parsed jobs
    jobs: list[DiscoveredJob] = []
    for result in results:
        if result is not None:
            jobs.append(result)

    return jobs


# ── Probing ──────────────────────────────────────────────────────────────


async def _probe_key(key: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the BITE search API with a key. Returns (found, job_count)."""
    try:
        resp = await client.post(
            _SEARCH_URL,
            json={
                "key": key,
                "channel": 0,
                "locale": "de",
                "page": {"num": 1, "offset": 0},
            },
        )
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        total = data.get("page", {}).get("total")
        if total is not None:
            return True, total
        return True, None
    except Exception:
        return False, None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect BITE: page HTML scan for widget markers → extract key from listing JS."""
    if client is None:
        return None

    # Scan page HTML for BITE markers
    html = await fetch_page_text(url, client)
    if not html:
        return None

    # Check for any BITE marker
    has_marker = any(marker.search(html) for marker in _PAGE_MARKERS)
    if not has_marker:
        return None

    # Try to extract the API key
    key, customer = await _extract_key_from_html(html, client)
    if key:
        found, count = await _probe_key(key, client)
        if found:
            result: dict = {"key": key}
            if customer:
                result["customer"] = customer
            if count is not None:
                result["jobs"] = count
            return result
        # Key found but API probe failed
        result = {"key": key}
        if customer:
            result["customer"] = customer
        return result

    # Marker found but no key extracted
    if customer:
        return {"customer": customer}

    return None


register("bite", discover, cost=10, can_handle=can_handle, rich=True)
