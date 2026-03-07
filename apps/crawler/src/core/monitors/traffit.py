"""TRAFFIT ATS monitor.

Public API (no auth): GET https://{slug}.traffit.com/public/job_posts/published
Returns full job data — title, HTML description, locations, salary, employment type.

Pagination via request headers: X-Request-Page-Size, X-Request-Current-Page.
Response headers: x-result-total-count, x-result-total-pages.

Detection: URL domain match (*.traffit.com) or page HTML markers
(cdn3.traffit.com, traffit-an-list, data-name="traffit",
traffit.com/public/an/generateJs).
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 10_000

# Page HTML patterns for detecting TRAFFIT career portals
_PAGE_PATTERNS = [
    re.compile(r"cdn3\.traffit\.com"),
    re.compile(r"traffit-an-list"),
    re.compile(r'data-name="traffit"'),
    re.compile(r"traffit\.com/public/an/generateJs"),
]

_IGNORE_SLUGS = frozenset({"www", "api", "cdn", "cdn3", "app", "help", "knowledge"})

_EMPLOYMENT_TYPE_MAP = {
    "Full time": "full-time",
    "Part time": "part-time",
    "Contract": "contract",
    "Internship": "internship",
}

_RATE_MAP = {
    "Monthly": "month",
    "Yearly": "year",
    "Hourly": "hour",
}


def _slug_from_url(url: str) -> str | None:
    """Extract customer slug from a *.traffit.com URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith(".traffit.com"):
        slug = host.removesuffix(".traffit.com")
        if slug and slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_url(slug: str) -> str:
    return f"https://{slug}.traffit.com/public/job_posts/published"


def _board_url(slug: str) -> str:
    return f"https://{slug}.traffit.com/career/"


def _get_value(values: list[dict], field_id: str) -> str | None:
    """Find a value by field_id in the advert.values array."""
    for item in values:
        if item.get("field_id") == field_id:
            return item.get("value")
    return None


def _parse_location(values: list[dict]) -> list[str] | None:
    """Extract locality from geolocation JSON string."""
    raw = _get_value(values, "geolocation")
    if not raw:
        return None
    try:
        geo = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    locality = geo.get("locality")
    if not locality:
        return None
    country = geo.get("country")
    if country:
        return [f"{locality}, {country}"]
    return [locality]


def _parse_salary(options: dict) -> dict | None:
    """Assemble base_salary dict from _Salary_* options."""
    min_val = options.get("_Salary_MIN")
    max_val = options.get("_Salary_MAX")
    currency = options.get("_Salary_Currency")

    if not currency or (min_val is None and max_val is None):
        return None

    rate_raw = options.get("_Salary_Rate", "")
    unit = _RATE_MAP.get(rate_raw, "month")

    def _to_num(v):
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    return {
        "currency": currency,
        "min": _to_num(min_val),
        "max": _to_num(max_val),
        "unit": unit,
    }


def _parse_job(job: dict) -> DiscoveredJob | None:
    """Parse a TRAFFIT job object into a DiscoveredJob."""
    url = job.get("url")
    if not url:
        return None

    advert = job.get("advert") or {}
    values = advert.get("values") or []
    options = job.get("options") or {}

    title = advert.get("name")
    description = _get_value(values, "description")
    locations = _parse_location(values)

    # Employment type
    job_types = options.get("job_type")
    employment_type = None
    if isinstance(job_types, list) and job_types:
        employment_type = _EMPLOYMENT_TYPE_MAP.get(job_types[0])

    # Job location type
    job_location_type = None
    remote = options.get("remote")
    if remote == "1":
        job_location_type = "remote"
    else:
        work_model = options.get("_work_model")
        if work_model == "Hybrid":
            job_location_type = "hybrid"
        elif work_model == "Remote":
            job_location_type = "remote"

    # Date posted
    date_posted = None
    valid_start = job.get("valid_start")
    if valid_start and isinstance(valid_start, str):
        date_posted = valid_start.split(" ")[0]

    # Salary
    base_salary = _parse_salary(options)

    # Language
    language = advert.get("language")

    # Extras — requirements, responsibilities, benefits
    extras: dict = {}
    for field_id in ("requirements", "responsibilities", "benefits"):
        val = _get_value(values, field_id)
        if val:
            extras[field_id] = val

    # Metadata
    metadata: dict = {}
    recruitment = advert.get("recruitment") or {}
    nr_ref = recruitment.get("nr_ref")
    if nr_ref:
        metadata["reference"] = nr_ref
    branches = options.get("branches")
    if branches:
        metadata["department"] = branches

    return DiscoveredJob(
        url=url,
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=date_posted,
        base_salary=base_salary,
        language=language,
        extras=extras or None,
        metadata=metadata or None,
    )


async def _probe_api(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the TRAFFIT API. Returns (found, job_count)."""
    try:
        resp = await client.get(
            _api_url(slug),
            headers={"X-Request-Page-Size": "1", "X-Request-Current-Page": "1"},
        )
        if resp.status_code != 200:
            return False, None
        total = resp.headers.get("x-result-total-count")
        if total is not None:
            try:
                return True, int(total)
            except ValueError:
                return True, None
        # Fallback: check if response is a valid JSON array
        data = resp.json()
        if isinstance(data, list):
            return True, len(data)
        return False, None
    except Exception:
        return False, None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings with full content from the TRAFFIT public API."""
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive TRAFFIT slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    page_size = 100
    page = 1
    jobs: list[DiscoveredJob] = []

    while True:
        resp = await client.get(
            _api_url(slug),
            headers={
                "X-Request-Page-Size": str(page_size),
                "X-Request-Current-Page": str(page),
            },
        )
        resp.raise_for_status()

        raw_jobs = resp.json()
        if not isinstance(raw_jobs, list) or not raw_jobs:
            break

        for raw in raw_jobs:
            if raw.get("awarded"):
                continue
            parsed = _parse_job(raw)
            if parsed:
                jobs.append(parsed)

        # Check pagination
        total_pages_hdr = resp.headers.get("x-result-total-pages")
        if total_pages_hdr:
            try:
                total_pages = int(total_pages_hdr)
            except ValueError:
                break
            if page >= total_pages:
                break
        else:
            # No pagination headers — single page response
            break

        page += 1

    if len(jobs) > MAX_JOBS:
        log.warning("traffit.truncated", slug=slug, total=len(jobs), cap=MAX_JOBS)
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect TRAFFIT: URL domain match -> page HTML scan."""
    # 1. Direct *.traffit.com URL
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_api(slug, client)
            if found:
                result: dict = {"slug": slug}
                if count is not None:
                    result["jobs"] = count
                return result
            # URL matched but API failed — still a TRAFFIT portal
            return {"slug": slug}
        return {"slug": slug}

    if client is None:
        return None

    # 2. HTML scan for TRAFFIT markers
    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                # Try to extract the traffit.com slug from HTML (skip ignored slugs)
                for slug_match in re.finditer(r"([\w-]+)\.traffit\.com", html):
                    found_slug = slug_match.group(1)
                    if found_slug in _IGNORE_SLUGS:
                        continue
                    log.info("traffit.detected_in_page", url=url, slug=found_slug)
                    found, count = await _probe_api(found_slug, client)
                    if found:
                        result = {"slug": found_slug}
                        if count is not None:
                            result["jobs"] = count
                        return result
                break  # Marker found but no usable slug — can't probe

    return None


register("traffit", discover, cost=10, can_handle=can_handle, rich=True)
