"""Hireology Careers API monitor.

Public API: GET https://api.hireology.com/v2/public/careers/{slug}?page_size=500
Returns full job data in a single request — no detail calls needed.
No authentication required.

Career page domains:
  - careers.hireology.com/{slug}
  - {slug}.hireology.careers
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000
PAGE_SIZE = 500

_CAREERS_DOMAIN_RE = re.compile(r"^careers\.hireology\.com$")
_NEW_DOMAIN_RE = re.compile(r"^([\w-]+)\.hireology\.careers$")

_PAGE_PATTERNS = [
    re.compile(r"careers\.hireology\.com/([\w-]+)"),
    re.compile(r"([\w-]+)\.hireology\.careers"),
    re.compile(r"api\.hireology\.com/v[12]/(?:public/)?careers/([\w-]+)"),
]

_IGNORE_SLUGS = frozenset({"api", "www", "app", "static", "assets", "js", "css"})


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Hireology careers slug from a known URL pattern."""
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()

    # {slug}.hireology.careers
    match = _NEW_DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug

    # careers.hireology.com/{slug}
    if _CAREERS_DOMAIN_RE.match(host):
        path = parsed.path.strip("/")
        # Extract first path segment (ignore /123/description etc.)
        parts = path.split("/")
        if parts and parts[0] and parts[0] not in _IGNORE_SLUGS:
            return parts[0]

    return None


def _api_url(slug: str) -> str:
    return f"https://api.hireology.com/v2/public/careers/{slug}"


def _parse_locations(job: dict) -> list[str] | None:
    """Extract locations from a Hireology job."""
    raw = job.get("locations")
    if not raw:
        return None

    locations: list[str] = []
    for loc in raw:
        if isinstance(loc, dict):
            city = loc.get("city", "")
            state = loc.get("state", "")
            parts = [p for p in (city, state) if p]
            name = ", ".join(parts)
            if name:
                locations.append(name)
        elif isinstance(loc, str) and loc:
            locations.append(loc)

    return locations or None


def _parse_job(job: dict) -> DiscoveredJob | None:
    """Map a Hireology job to a DiscoveredJob."""
    url = job.get("career_site_url")
    if not url:
        return None

    # Metadata
    metadata: dict = {}
    org = job.get("organization")
    if isinstance(org, dict) and org.get("name"):
        metadata["organization"] = org["name"]
    job_family = job.get("job_family")
    if isinstance(job_family, dict) and job_family.get("name"):
        metadata["job_family"] = job_family["name"]
    job_id = job.get("id")
    if job_id:
        metadata["id"] = job_id

    # job_location_type
    job_location_type = "remote" if job.get("remote") else None

    return DiscoveredJob(
        url=url,
        title=job.get("name"),
        description=job.get("job_description"),
        locations=_parse_locations(job),
        employment_type=job.get("employment_status"),
        job_location_type=job_location_type,
        date_posted=job.get("created_at"),
        metadata=metadata or None,
    )


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Hireology API for a slug. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(slug), params={"page_size": 1})
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        count = data.get("count")
        if isinstance(count, int):
            return True, count
        # Check if data list exists
        items = data.get("data")
        if isinstance(items, list):
            return True, len(items)
        return False, None
    except Exception:
        return False, None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Hireology public API."""
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Hireology slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    # Paginate through all jobs
    jobs: list[DiscoveredJob] = []
    page = 1

    while True:
        resp = await client.get(
            _api_url(slug),
            params={"page_size": PAGE_SIZE, "page": page},
        )
        resp.raise_for_status()

        data = resp.json()
        raw_jobs = data.get("data", [])

        for raw in raw_jobs:
            if raw.get("status") != "Open":
                continue
            parsed = _parse_job(raw)
            if parsed:
                jobs.append(parsed)

        total = data.get("count", 0)
        if page * PAGE_SIZE >= total or len(raw_jobs) < PAGE_SIZE:
            break

        page += 1

        if len(jobs) >= MAX_JOBS:
            log.warning("hireology.truncated", slug=slug, total=len(jobs), cap=MAX_JOBS)
            jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]
            break

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Hireology: URL pattern -> page HTML scan -> slug-based API probe."""
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_slug(slug, client)
            if found:
                result: dict = {"slug": slug}
                if count is not None:
                    result["jobs"] = count
                return result
        return {"slug": slug}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found_slug = match.group(1)
                if found_slug not in _IGNORE_SLUGS:
                    log.info("hireology.detected_in_page", url=url, slug=found_slug)
                    found, count = await _probe_slug(found_slug, client)
                    if found:
                        result = {"slug": found_slug}
                        if count is not None:
                            result["jobs"] = count
                        return result

    for slug_candidate in slugs_from_url(url):
        found, count = await _probe_slug(slug_candidate, client)
        if found:
            log.info("hireology.detected_by_probe", url=url, slug=slug_candidate)
            result = {"slug": slug_candidate}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("hireology", discover, cost=10, can_handle=can_handle)
