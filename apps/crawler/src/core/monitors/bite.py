"""BITE GmbH ATS monitor (Job Search API).

Public JSON API at jobs.b-ite.com — requires a "Job Listing Key" (40-char hex)
that is embedded in widget listing JavaScript on career pages.

Search:  POST https://jobs.b-ite.com/api/v1/postings/search

The search endpoint returns job URLs. Detail fetching is handled by the
BITE scraper (``src/core/scrapers/bite``).

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

import re

import httpx
import structlog

from src.core.monitors import fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 10_000
PAGE_SIZE = 100

_SEARCH_URL = "https://jobs.b-ite.com/api/v1/postings/search"

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


# ── Discovery ────────────────────────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover job URLs from a BITE board.

    POST search with key + pagination → collect job URLs.
    """
    metadata = board.get("metadata") or {}
    key = metadata.get("key")

    if not key:
        raise ValueError(
            f"BITE monitor requires a 'key' in metadata for board {board['board_url']!r}"
        )

    locale = metadata.get("locale", "de")
    channel = metadata.get("channel", 0)

    urls: set[str] = set()
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
            if url:
                urls.add(url)

        page_info = data.get("page", {})
        total = page_info.get("total", 0)
        offset += len(postings)

        if offset >= total or len(urls) >= MAX_JOBS:
            break

    if not urls:
        log.info("bite.no_jobs", key=key[:8] + "...")
        return set()

    if len(urls) > MAX_JOBS:
        log.warning("bite.truncated", total=len(urls), cap=MAX_JOBS)

    log.info("bite.listed", key=key[:8] + "...", jobs=len(urls))

    return urls


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


register("bite", discover, cost=10, can_handle=can_handle, rich=False)
