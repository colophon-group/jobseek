"""Rippling ATS Job Board API monitor.

Public API:
  List:   GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs
  Detail: GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}

The list endpoint returns all jobs (no pagination) but only metadata
(name, department, workLocation).  The detail endpoint adds description,
employmentType, payRangeDetails, etc.  So the monitor fetches each job
individually for full data (N+1 calls, with concurrency).
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

_API_BASE = "https://api.rippling.com/platform/api/ats/v1/board"

# Matches ats.rippling.com or ats.us1.rippling.com, with optional locale prefix
_URL_RE = re.compile(
    r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs"
)

_PAGE_PATTERNS = [
    re.compile(r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs"),
    re.compile(r"api\.rippling\.com/platform/api/ats/\w+/board/([\w-]+)"),
]

_IGNORE_SLUGS = frozenset({"api", "platform", "static", "assets", "js", "css"})

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "SALARIED_FT": "Full-time",
    "SALARIED_PT": "Part-time",
    "HOURLY_FT": "Full-time",
    "HOURLY_PT": "Part-time",
    "INTERN": "Intern",
    "CONTRACT": "Contract",
    "TEMPORARY": "Temporary",
}


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Rippling board slug from an ats.rippling.com URL."""
    match = _URL_RE.search(board_url)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_list_url(slug: str) -> str:
    return f"{_API_BASE}/{slug}/jobs"


def _api_detail_url(slug: str, uuid: str) -> str:
    return f"{_API_BASE}/{slug}/jobs/{uuid}"


def _parse_salary(pay_ranges: list[dict] | None) -> dict | None:
    """Extract salary from payRangeDetails."""
    if not pay_ranges:
        return None
    # Use the first pay range
    pr = pay_ranges[0]
    sal_min = pr.get("rangeStart")
    sal_max = pr.get("rangeEnd")
    if sal_min is None and sal_max is None:
        return None
    currency = pr.get("currency")
    freq = (pr.get("frequency") or "").upper()
    unit = "year"
    if "HOUR" in freq:
        unit = "hour"
    elif "MONTH" in freq:
        unit = "month"
    elif "WEEK" in freq:
        unit = "week"
    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_job_location_type(locations: list[str] | None) -> str | None:
    """Infer job_location_type from workLocations strings."""
    if not locations:
        return None
    # If any location contains "Remote", mark as remote
    for loc in locations:
        if "remote" in loc.lower():
            return "remote"
    return None


def _parse_employment_type(emp: dict | None) -> str | None:
    """Map employmentType label to human-readable form."""
    if not emp:
        return None
    label = emp.get("label", "")
    mapped = _EMPLOYMENT_TYPE_MAP.get(label)
    if mapped:
        return mapped
    # Fallback to the id field which is human-readable
    return emp.get("id") or label or None


def _parse_job(detail: dict) -> DiscoveredJob | None:
    """Map a detail API response to a DiscoveredJob."""
    url = detail.get("url")
    if not url:
        return None

    # Combine description.company + description.role into a single HTML body
    desc_obj = detail.get("description") or {}
    parts: list[str] = []
    company_desc = desc_obj.get("company")
    if company_desc:
        parts.append(company_desc)
    role_desc = desc_obj.get("role")
    if role_desc:
        parts.append(role_desc)
    description = "\n".join(parts) if parts else None

    # Locations
    work_locations = detail.get("workLocations") or []
    locations = [loc for loc in work_locations if loc] or None

    # Metadata
    metadata: dict = {}
    dept = detail.get("department")
    if isinstance(dept, dict):
        dept_name = dept.get("name")
        if dept_name:
            metadata["department"] = dept_name
        base_dept = dept.get("base_department")
        if base_dept and base_dept != dept_name:
            metadata["base_department"] = base_dept
    company_name = detail.get("companyName")
    if company_name:
        metadata["company"] = company_name

    return DiscoveredJob(
        url=url,
        title=detail.get("name"),
        description=description,
        locations=locations,
        employment_type=_parse_employment_type(detail.get("employmentType")),
        job_location_type=_parse_job_location_type(locations),
        date_posted=detail.get("createdOn"),
        base_salary=_parse_salary(detail.get("payRangeDetails")),
        metadata=metadata or None,
    )


async def _fetch_detail(
    slug: str,
    uuid: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Fetch a single job's detail, respecting the concurrency semaphore."""
    async with semaphore:
        try:
            resp = await client.get(_api_detail_url(slug, uuid))
            if resp.status_code != 200:
                log.warning(
                    "rippling.detail_failed",
                    uuid=uuid,
                    status=resp.status_code,
                )
                return None
            return resp.json()
        except Exception as exc:
            log.warning("rippling.detail_error", uuid=uuid, error=str(exc))
            return None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Rippling public API.

    Lists all jobs via the V1 endpoint (single request, no pagination),
    then fetches each job's detail concurrently for full data.
    """
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Rippling board slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    # Step 1: List all jobs
    resp = await client.get(_api_list_url(slug))
    resp.raise_for_status()

    job_list: list[dict] = resp.json()
    if not isinstance(job_list, list):
        return []

    uuids = [j["uuid"] for j in job_list if j.get("uuid")]

    if len(uuids) > MAX_JOBS:
        log.warning("rippling.truncated", slug=slug, total=len(uuids), cap=MAX_JOBS)
        uuids = uuids[:MAX_JOBS]

    log.info("rippling.listed", slug=slug, jobs=len(uuids))

    # Step 2: Fetch details concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [_fetch_detail(slug, uuid, client, semaphore) for uuid in uuids]
    detail_results = await asyncio.gather(*tasks)

    # Step 3: Parse into DiscoveredJobs
    jobs: list[DiscoveredJob] = []
    for detail in detail_results:
        if detail is None:
            continue
        parsed = _parse_job(detail)
        if parsed:
            jobs.append(parsed)

    return jobs


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Rippling API for a slug. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_list_url(slug))
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        if isinstance(data, list):
            return True, len(data)
        return False, None
    except Exception:
        return False, None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Rippling: URL pattern -> page HTML scan -> slug-based API probe."""
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
                    log.info("rippling.detected_in_page", url=url, slug=found_slug)
                    found, count = await _probe_slug(found_slug, client)
                    if found:
                        result = {"slug": found_slug}
                        if count is not None:
                            result["jobs"] = count
                        return result

    for slug_candidate in slugs_from_url(url):
        found, count = await _probe_slug(slug_candidate, client)
        if found:
            log.info("rippling.detected_by_probe", url=url, slug=slug_candidate)
            result = {"slug": slug_candidate}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("rippling", discover, cost=10, can_handle=can_handle)
