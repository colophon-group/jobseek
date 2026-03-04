"""Workable Posting API monitor.

Public API:
  List:   POST https://apply.workable.com/api/v3/accounts/{slug}/jobs
  Detail: GET  https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}

The list endpoint returns metadata (title, location, department) but not the
full job description.  The detail endpoint adds ``description``, ``requirements``,
and ``benefits`` (all HTML).  So the monitor fetches each posting individually
for full data (N+1 calls, with concurrency).
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000
CONCURRENCY = 10

_PAGE_PATTERNS = [
    re.compile(r"apply\.workable\.com/([\w-]+)"),
]

_IGNORE_TOKENS = frozenset({"api", "v2", "v3", "js", "css", "assets", "accounts", "jobs"})

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "full": "Full-time",
    "part": "Part-time",
    "contract": "Contract",
    "temporary": "Temporary",
    "internship": "Intern",
    "volunteer": "Volunteer",
    "other": "Other",
}

_WORKPLACE_MAP: dict[str, str] = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
    "on_site": "onsite",
}


def _token_from_url(board_url: str) -> str | None:
    """Extract company slug from a Workable URL."""
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(board_url)
        if match:
            token = match.group(1)
            if token not in _IGNORE_TOKENS:
                return token
    return None


def _api_list_url(slug: str) -> str:
    return f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"


def _api_detail_url(slug: str, shortcode: str) -> str:
    return f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}"


def _job_url(slug: str, shortcode: str) -> str:
    return f"https://apply.workable.com/{slug}/j/{shortcode}/"


def _build_description(detail: dict) -> str | None:
    """Combine description + requirements + benefits into a single HTML body."""
    parts: list[str] = []
    for key in ("description", "requirements", "benefits"):
        text = detail.get(key)
        if text and isinstance(text, str):
            parts.append(text)
    return "\n".join(parts) if parts else None


def _build_locations(detail: dict) -> list[str] | None:
    """Build location strings from the locations array."""
    raw_locations = detail.get("locations")
    if not raw_locations or not isinstance(raw_locations, list):
        # Fallback to single location object
        loc = detail.get("location")
        if isinstance(loc, dict):
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            name = ", ".join(p for p in parts if p)
            return [name] if name else None
        if isinstance(loc, str) and loc:
            return [loc]
        return None

    locations: list[str] = []
    seen: set[str] = set()
    for loc in raw_locations:
        if isinstance(loc, dict):
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            name = ", ".join(p for p in parts if p)
        elif isinstance(loc, str):
            name = loc
        else:
            continue
        if name and name not in seen:
            locations.append(name)
            seen.add(name)

    return locations or None


def _parse_job_location_type(detail: dict) -> str | None:
    """Derive job_location_type from workplace or remote fields."""
    workplace = detail.get("workplace")
    if isinstance(workplace, str):
        mapped = _WORKPLACE_MAP.get(workplace.lower())
        if mapped:
            return mapped
    if detail.get("remote"):
        return "remote"
    return None


def _parse_job(detail: dict, slug: str) -> DiscoveredJob | None:
    """Map a detail API response to a DiscoveredJob."""
    shortcode = detail.get("shortcode")
    if not shortcode:
        return None

    url = _job_url(slug, shortcode)
    title = detail.get("title")
    description = _build_description(detail)

    # Employment type
    raw_type = detail.get("type")
    employment_type = None
    if isinstance(raw_type, str):
        employment_type = _EMPLOYMENT_TYPE_MAP.get(raw_type.lower(), raw_type)

    # Metadata
    metadata: dict = {}
    dept = detail.get("department")
    if isinstance(dept, str) and dept:
        metadata["department"] = dept
    elif isinstance(dept, list) and dept:
        metadata["department"] = ", ".join(dept)

    return DiscoveredJob(
        url=url,
        title=title,
        description=description,
        locations=_build_locations(detail),
        employment_type=employment_type,
        job_location_type=_parse_job_location_type(detail),
        date_posted=detail.get("published"),
        metadata=metadata or None,
    )


async def _fetch_detail(
    slug: str,
    shortcode: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Fetch a single posting's detail, respecting the concurrency semaphore."""
    async with semaphore:
        try:
            resp = await client.get(_api_detail_url(slug, shortcode))
            if resp.status_code != 200:
                log.warning(
                    "workable.detail_failed",
                    shortcode=shortcode,
                    status=resp.status_code,
                )
                return None
            return resp.json()
        except Exception as exc:
            log.warning("workable.detail_error", shortcode=shortcode, error=str(exc))
            return None


async def _api_list(slug: str, client: httpx.AsyncClient) -> list[str]:
    """Paginate the list endpoint to collect all shortcodes."""
    shortcodes: list[str] = []
    body: dict = {"query": "", "location": [], "department": [], "worktype": []}

    while True:
        resp = await client.post(_api_list_url(slug), json=body)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        for item in results:
            sc = item.get("shortcode")
            if sc:
                shortcodes.append(sc)

        next_page = data.get("nextPage")
        if not next_page or not results:
            break

        body["token"] = next_page

        if len(shortcodes) >= MAX_JOBS:
            log.warning("workable.truncated", slug=slug, total=len(shortcodes), cap=MAX_JOBS)
            shortcodes = shortcodes[:MAX_JOBS]
            break

    return shortcodes


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Workable public API.

    Paginates the list endpoint, then fetches each posting's detail
    concurrently to get full descriptions.
    """
    metadata = board.get("metadata") or {}
    slug = metadata.get("token") or _token_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Workable slug from board URL {board['board_url']!r} "
            "and no token in metadata"
        )

    # Step 1: Paginate list endpoint to collect all shortcodes
    shortcodes = await _api_list(slug, client)
    log.info("workable.listed", slug=slug, postings=len(shortcodes))

    # Step 2: Fetch details concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [_fetch_detail(slug, sc, client, semaphore) for sc in shortcodes]
    detail_results = await asyncio.gather(*tasks)

    # Step 3: Parse into DiscoveredJobs
    jobs: list[DiscoveredJob] = []
    for detail in detail_results:
        if detail is None:
            continue
        parsed = _parse_job(detail, slug)
        if parsed:
            jobs.append(parsed)

    return jobs


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Workable API for a slug. Returns (found, job_count)."""
    try:
        body = {"query": "", "location": [], "department": [], "worktype": []}
        resp = await client.post(_api_list_url(slug), json=body)
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        total = data.get("total")
        if isinstance(total, int):
            return True, total
        results = data.get("results")
        if isinstance(results, list):
            return True, len(results)
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(slug: str, client: httpx.AsyncClient) -> int | None:
    """Lightweight API call to get the job count for a slug."""
    try:
        body = {"query": "", "location": [], "department": [], "worktype": []}
        resp = await client.post(_api_list_url(slug), json=body)
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("total")
        return total if isinstance(total, int) else None
    except Exception:
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Workable: URL pattern -> page HTML scan -> slug-based API probe."""
    token = _token_from_url(url)
    if token:
        if client is not None:
            count = await _fetch_job_count(token, client)
            if count is not None:
                return {"token": token, "jobs": count}
        return {"token": token}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    log.info("workable.detected_in_page", url=url, board_token=found)
                    count = await _fetch_job_count(found, client)
                    result: dict = {"token": found}
                    if count is not None:
                        result["jobs"] = count
                    return result

    for slug in slugs_from_url(url):
        found, count = await _probe_slug(slug, client)
        if found:
            log.info("workable.detected_by_probe", url=url, board_token=slug)
            result = {"token": slug}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("workable", discover, cost=10, can_handle=can_handle)
