"""Softgarden ATS monitor.

Classic boards ({slug}.softgarden.io) embed all job IDs in inline JavaScript
and serve JSON-LD JobPosting on detail pages.  No API credentials needed.

Listing page JS (confirmed on hapaglloyd, ctseventim):
  var complete_job_id_list = jobs_selected = [48677018, 53688446, ...];

Detail pages contain <script type="application/ld+json"> with full JobPosting
schema (title, description, datePosted, validThrough, employmentType,
baseSalary, jobLocation).

N+1 monitor: 1 listing fetch + N detail page fetches (concurrency=10).
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 10_000
CONCURRENCY = 10

_IGNORE_SLUGS = frozenset({"www", "api", "app", "static", "cdn"})

_JOB_IDS_RE = re.compile(r"var\s+complete_job_id_list\s*=\s*(?:jobs_selected\s*=\s*)?\[([^\]]*)\]")

_PAGE_MARKERS = [
    re.compile(r"softgarden\.io/assets/"),
    re.compile(r"tracker\.softgarden\.de"),
    re.compile(r"matomo\.softgarden\.io"),
    re.compile(r"certificate\.softgarden\.io"),
    re.compile(r"powered by softgarden", re.IGNORECASE),
    re.compile(r"mediaassets\.softgarden\.de"),
]

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "FULL_TIME": "full-time",
    "PART_TIME": "part-time",
    "TEMPORARY": "temporary",
    "CONTRACTOR": "contract",
    "INTERN": "internship",
}


# ── URL helpers ──────────────────────────────────────────────────────────


def _slug_from_url(url: str) -> str | None:
    """Extract customer slug from a *.softgarden.io URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith(".softgarden.io"):
        slug = host.removesuffix(".softgarden.io")
        if slug and slug not in _IGNORE_SLUGS:
            return slug
    return None


def _board_url(slug: str) -> str:
    return f"https://{slug}.softgarden.io"


def _job_url(base: str, job_id: int | str, pattern: str = "{base}/job/{id}?l=en") -> str:
    return pattern.replace("{base}", base).replace("{id}", str(job_id))


# ── Listing page parsing ────────────────────────────────────────────────


def _extract_job_ids(html: str) -> list[int]:
    """Extract job IDs from the listing page's inline JavaScript."""
    match = _JOB_IDS_RE.search(html)
    if not match:
        return []
    raw = match.group(1).strip()
    if not raw:
        return []
    ids: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if token:
            try:
                ids.append(int(token))
            except ValueError:
                continue
    return ids


# ── JSON-LD extraction ───────────────────────────────────────────────────


class _JsonLdExtractor(HTMLParser):
    """Extract JSON-LD blocks from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._in_jsonld = False
        self._data: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            attr_dict = {k: (v or "") for k, v in attrs}
            if attr_dict.get("type", "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._data = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._data.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.blocks.append("".join(self._data))


def _find_job_posting(blocks: list[str]) -> dict | None:
    """Find a JobPosting object in JSON-LD blocks (handles @graph)."""
    import json

    for raw in blocks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        if isinstance(data, dict):
            if data.get("@type") == "JobPosting":
                return data
            # Check @graph
            graph = data.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        return item
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    return item

    return None


def _extract_locations(posting: dict) -> list[str] | None:
    """Extract locations from jobLocation.address."""
    job_location = posting.get("jobLocation")
    if not job_location:
        return None

    locations_raw = job_location if isinstance(job_location, list) else [job_location]
    locations: list[str] = []

    for loc in locations_raw:
        if not isinstance(loc, dict):
            continue
        address = loc.get("address")
        if isinstance(address, dict):
            parts: list[str] = []
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                val = address.get(key)
                if val and isinstance(val, str):
                    parts.append(val)
            if parts:
                locations.append(", ".join(parts))
                continue
        # Fallback: use name field
        name = loc.get("name")
        if name and isinstance(name, str):
            locations.append(name)

    return locations if locations else None


def _extract_salary(posting: dict) -> dict | None:
    """Extract salary from baseSalary. Skip zero-value entries."""
    base_salary = posting.get("baseSalary")
    if not isinstance(base_salary, dict):
        return None

    value = base_salary.get("value")
    if not isinstance(value, dict):
        return None

    min_val = value.get("minValue")
    max_val = value.get("maxValue")

    # Skip zero-value entries (common in softgarden)
    if min_val is not None and max_val is not None:
        try:
            if float(min_val) == 0.0 and float(max_val) == 0.0:
                return None
        except (ValueError, TypeError):
            pass

    if min_val is None and max_val is None:
        return None

    currency = base_salary.get("currency")
    unit_raw = (value.get("unitText") or "").upper()
    unit = "year"
    if "HOUR" in unit_raw:
        unit = "hour"
    elif "MONTH" in unit_raw:
        unit = "month"
    elif "WEEK" in unit_raw:
        unit = "week"

    return {"currency": currency, "min": min_val, "max": max_val, "unit": unit}


def _normalize_employment_type(raw: str | list | None) -> str | None:
    """Normalize employmentType from JSON-LD to standard form."""
    if raw is None:
        return None
    if isinstance(raw, list):
        # Use the first recognized type
        for item in raw:
            mapped = _EMPLOYMENT_TYPE_MAP.get(item)
            if mapped:
                return mapped
        return None
    return _EMPLOYMENT_TYPE_MAP.get(raw)


def _parse_detail(html: str, url: str) -> DiscoveredJob | None:
    """Parse a detail page's JSON-LD into a DiscoveredJob."""
    extractor = _JsonLdExtractor()
    extractor.feed(html)

    posting = _find_job_posting(extractor.blocks)
    if not posting:
        return None

    return DiscoveredJob(
        url=url,
        title=posting.get("title") or posting.get("name"),
        description=posting.get("description"),
        locations=_extract_locations(posting),
        employment_type=_normalize_employment_type(posting.get("employmentType")),
        date_posted=posting.get("datePosted"),
        base_salary=_extract_salary(posting),
    )


# ── Detail fetching ─────────────────────────────────────────────────────


async def _fetch_detail(
    url: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str | None]:
    """Fetch a single detail page, respecting the concurrency semaphore."""
    async with semaphore:
        try:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                log.warning("softgarden.detail_failed", url=url, status=resp.status_code)
                return url, None
            return url, resp.text
        except Exception as exc:
            log.warning("softgarden.detail_error", url=url, error=str(exc))
            return url, None


# ── Discovery ────────────────────────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Discover jobs from a Softgarden board.

    1. Fetch listing page → extract job IDs from inline JS
    2. Build detail URLs via configurable pattern
    3. Fetch detail pages concurrently (semaphore=10)
    4. Parse JSON-LD JobPosting from each detail page
    """
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Softgarden slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    base = _board_url(slug)
    pattern = metadata.get("job_url_pattern", "{base}/job/{id}?l=en")

    # Step 1: Fetch listing page
    resp = await client.get(base, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # Step 2: Extract job IDs
    job_ids = _extract_job_ids(html)
    if not job_ids:
        log.info("softgarden.no_jobs", slug=slug)
        return []

    if len(job_ids) > MAX_JOBS:
        log.warning("softgarden.truncated", slug=slug, total=len(job_ids), cap=MAX_JOBS)
        job_ids = job_ids[:MAX_JOBS]

    log.info("softgarden.listed", slug=slug, jobs=len(job_ids))

    # Step 3: Fetch detail pages concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)
    detail_urls = [_job_url(base, jid, pattern) for jid in job_ids]
    tasks = [_fetch_detail(url, client, semaphore) for url in detail_urls]
    results = await asyncio.gather(*tasks)

    # Step 4: Parse JSON-LD from each detail page
    jobs: list[DiscoveredJob] = []
    for detail_url, detail_html in results:
        if detail_html is None:
            continue
        parsed = _parse_detail(detail_html, detail_url)
        if parsed:
            jobs.append(parsed)

    return jobs


# ── Probing ──────────────────────────────────────────────────────────────


async def _probe_listing(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe a Softgarden listing page. Returns (found, job_count)."""
    try:
        resp = await client.get(_board_url(slug), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        job_ids = _extract_job_ids(resp.text)
        if job_ids:
            return True, len(job_ids)
        return False, None
    except Exception:
        return False, None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Softgarden: URL domain match -> page HTML markers scan."""
    # 1. Direct *.softgarden.io URL
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_listing(slug, client)
            if found:
                result: dict = {"slug": slug}
                if count is not None:
                    result["jobs"] = count
                return result
            # URL matched but no jobs found — still a Softgarden portal
            return {"slug": slug}
        return {"slug": slug}

    if client is None:
        return None

    # 2. HTML scan for Softgarden markers
    html = await fetch_page_text(url, client)
    if html:
        for marker in _PAGE_MARKERS:
            if marker.search(html):
                # Try to extract a softgarden.io slug from the HTML
                for slug_match in re.finditer(r"([\w-]+)\.softgarden\.io", html):
                    found_slug = slug_match.group(1)
                    if found_slug in _IGNORE_SLUGS:
                        continue
                    log.info("softgarden.detected_in_page", url=url, slug=found_slug)
                    found, count = await _probe_listing(found_slug, client)
                    if found:
                        result = {"slug": found_slug}
                        if count is not None:
                            result["jobs"] = count
                        return result
                break  # Marker found but no usable slug

    return None


register("softgarden", discover, cost=10, can_handle=can_handle, rich=True)
