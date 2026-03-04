"""Recruitee Careers Site API monitor.

Public API: GET https://{slug}.recruitee.com/api/offers
Returns full job data in a single request — no pagination needed.
Also works on custom domains: GET https://{custom-domain}/api/offers
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000

_DOMAIN_RE = re.compile(r"^([\w-]+)\.recruitee\.com$")

_PAGE_PATTERNS = [
    re.compile(r"([\w-]+)\.recruitee\.com"),
    re.compile(r"recruiteecdn\.com"),
    re.compile(r"window\.recruitee"),
]

_IGNORE_SLUGS = frozenset({"api", "www", "app", "docs", "help", "support", "status"})

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "fulltime": "Full-time",
    "fulltime_permanent": "Full-time",
    "fulltime_fixed_term": "Full-time",
    "parttime": "Part-time",
    "parttime_permanent": "Part-time",
    "parttime_fixed_term": "Part-time",
    "freelance": "Contract",
    "internship": "Intern",
    "traineeship": "Intern",
    "volunteer": "Volunteer",
}


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Recruitee company slug from a *.recruitee.com URL."""
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_base_from_url(board_url: str) -> str | None:
    """Derive the API base URL. Returns https://{host} for any Recruitee URL."""
    parsed = urlparse(board_url)
    host = parsed.hostname
    if host:
        scheme = parsed.scheme or "https"
        return f"{scheme}://{host}"
    return None


def _api_url(api_base: str) -> str:
    return f"{api_base}/api/offers"


def _parse_locations(offer: dict) -> list[str] | None:
    """Extract locations from a Recruitee offer."""
    locations: list[str] = []
    seen: set[str] = set()

    # Structured locations array (preferred)
    for loc in offer.get("locations", []):
        city = loc.get("city", "")
        country = loc.get("country", "")
        parts = [p for p in (city, country) if p]
        name = ", ".join(parts)
        if name and name not in seen:
            locations.append(name)
            seen.add(name)

    # Fallback to flat location string
    if not locations:
        flat_loc = offer.get("location")
        if flat_loc and isinstance(flat_loc, str):
            locations.append(flat_loc)

    return locations or None


def _parse_job_location_type(offer: dict) -> str | None:
    """Derive job_location_type from boolean flags."""
    if offer.get("remote"):
        return "remote"
    if offer.get("hybrid"):
        return "hybrid"
    if offer.get("on_site"):
        return "onsite"
    return None


def _parse_salary(offer: dict) -> dict | None:
    """Extract salary from the salary object."""
    salary = offer.get("salary")
    if not salary or not isinstance(salary, dict):
        return None
    sal_min = salary.get("min")
    sal_max = salary.get("max")
    if sal_min is None and sal_max is None:
        return None
    currency = salary.get("currency")
    period = (salary.get("period") or "").lower()
    unit = "year"
    if "hour" in period:
        unit = "hour"
    elif "month" in period:
        unit = "month"
    elif "week" in period:
        unit = "week"
    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_job(offer: dict) -> DiscoveredJob | None:
    """Map a Recruitee offer to a DiscoveredJob."""
    url = offer.get("careers_url")
    if not url:
        return None

    # Combine description + requirements into a single HTML body
    parts: list[str] = []
    desc = offer.get("description")
    if desc:
        parts.append(desc)
    reqs = offer.get("requirements")
    if reqs:
        parts.append(reqs)
    description = "\n".join(parts) if parts else None

    # Employment type
    emp_code = offer.get("employment_type_code") or ""
    employment_type = _EMPLOYMENT_TYPE_MAP.get(emp_code, emp_code or None)

    # Metadata
    metadata: dict = {}
    department = offer.get("department")
    if department:
        metadata["department"] = department
    tags = offer.get("tags")
    if tags and isinstance(tags, list):
        metadata["tags"] = tags
    category = offer.get("category_code")
    if category:
        metadata["category"] = category
    offer_id = offer.get("id")
    if offer_id:
        metadata["id"] = offer_id

    return DiscoveredJob(
        url=url,
        title=offer.get("title"),
        description=description,
        locations=_parse_locations(offer),
        employment_type=employment_type,
        job_location_type=_parse_job_location_type(offer),
        date_posted=offer.get("published_at"),
        base_salary=_parse_salary(offer),
        metadata=metadata or None,
    )


async def _probe_api(api_base: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Recruitee API. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(api_base), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        offers = data.get("offers")
        if isinstance(offers, list):
            return True, len(offers)
        return False, None
    except Exception:
        return False, None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Recruitee public API."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    # Determine API base: explicit api_base in metadata, or from slug, or from board URL
    api_base = metadata.get("api_base")
    if not api_base:
        slug = metadata.get("slug") or _slug_from_url(board_url)
        if slug:
            api_base = f"https://{slug}.recruitee.com"
        else:
            # Custom domain — use the board URL's host directly
            api_base = _api_base_from_url(board_url)

    if not api_base:
        raise ValueError(
            f"Cannot derive Recruitee API base from board URL {board_url!r} "
            "and no slug or api_base in metadata"
        )

    url = _api_url(api_base)
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()

    data = response.json()
    raw_offers = data.get("offers", [])

    jobs: list[DiscoveredJob] = []
    for raw in raw_offers:
        if raw.get("status") != "published":
            continue
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("recruitee.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Recruitee: domain check -> page HTML scan -> slug-based API probe."""
    # 1. Direct *.recruitee.com URL
    slug = _slug_from_url(url)
    if slug:
        api_base = f"https://{slug}.recruitee.com"
        if client is not None:
            found, count = await _probe_api(api_base, client)
            if found:
                result: dict = {"slug": slug, "api_base": api_base}
                if count is not None:
                    result["jobs"] = count
                return result
        return {"slug": slug, "api_base": api_base}

    if client is None:
        return None

    # 2. HTML scan for Recruitee markers (custom domains)
    html = await fetch_page_text(url, client)
    if html:
        # Look for {slug}.recruitee.com references in page source
        slug_match = re.search(r"([\w-]+)\.recruitee\.com", html)
        if slug_match:
            found_slug = slug_match.group(1)
            if found_slug not in _IGNORE_SLUGS:
                log.info("recruitee.detected_in_page", url=url, slug=found_slug)
                # Custom domain: API is on the custom domain itself
                api_base = _api_base_from_url(url)
                if api_base:
                    found, count = await _probe_api(api_base, client)
                    if found:
                        result = {"slug": found_slug, "api_base": api_base}
                        if count is not None:
                            result["jobs"] = count
                        return result

        # Also check for recruiteecdn.com or window.recruitee
        if "recruiteecdn.com" in html or "window.recruitee" in html:
            log.info("recruitee.detected_marker", url=url)
            api_base = _api_base_from_url(url)
            if api_base:
                found, count = await _probe_api(api_base, client)
                if found:
                    result = {"api_base": api_base}
                    if count is not None:
                        result["jobs"] = count
                    return result

    # 3. Slug-based probe as fallback
    for slug in slugs_from_url(url):
        api_base = f"https://{slug}.recruitee.com"
        found, count = await _probe_api(api_base, client)
        if found:
            log.info("recruitee.detected_by_probe", url=url, slug=slug)
            result = {"slug": slug, "api_base": api_base}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("recruitee", discover, cost=10, can_handle=can_handle)
