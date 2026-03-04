"""Ashby Job Board API monitor.

Public API: GET https://api.ashbyhq.com/posting-api/job-board/{BOARD_NAME}
Returns full job data — title, HTML description, locations, departments, etc.
No authentication required.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000

_PAGE_PATTERNS = [
    re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([\w-]+)"),
    re.compile(r"jobs\.ashbyhq\.com/([\w-]+)"),
]

_IGNORE_TOKENS = frozenset({"api", "js", "css", "assets", "posting-api"})

_WORKPLACE_TYPE_MAP: dict[str, str] = {
    "Remote": "remote",
    "Hybrid": "hybrid",
    "OnSite": "onsite",
}

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "FullTime": "Full-time",
    "PartTime": "Part-time",
    "Intern": "Intern",
    "Contract": "Contract",
    "Temporary": "Temporary",
}


def _parse_locations(job: dict) -> list[str] | None:
    """Extract locations from Ashby job data."""
    locations: list[str] = []
    seen: set[str] = set()

    # Primary location string
    location = job.get("location")
    if location and isinstance(location, str):
        locations.append(location)
        seen.add(location)

    # Secondary locations
    for loc in job.get("secondaryLocations", []):
        name = loc if isinstance(loc, str) else loc.get("location", "")
        if name and name not in seen:
            locations.append(name)
            seen.add(name)

    # Structured address as fallback
    if not locations:
        address = job.get("address")
        if isinstance(address, dict):
            city = address.get("city", "")
            region = address.get("region", "")
            country = address.get("country", "")
            parts = [p for p in (city, region, country) if p]
            if parts:
                name = ", ".join(parts)
                locations.append(name)

    return locations or None


def _parse_compensation(job: dict, compensation: dict | None) -> dict | None:
    """Extract salary from the compensation tiers if available."""
    if not compensation:
        return None
    comp_tiers = compensation.get("compensationTierSummary", [])
    if not comp_tiers:
        return None

    # Match by compensation tier ID if present on the job
    tier_id = job.get("compensationTierSummary")
    if not tier_id:
        return None

    for tier in comp_tiers:
        if tier.get("id") == tier_id:
            sal_min = tier.get("min")
            sal_max = tier.get("max")
            currency = tier.get("currency")
            interval = tier.get("interval", "")
            if sal_min is None and sal_max is None:
                return None
            unit = "year"
            if "hour" in interval.lower():
                unit = "hour"
            elif "month" in interval.lower():
                unit = "month"
            return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}

    return None


def _parse_job(job: dict, compensation: dict | None = None) -> DiscoveredJob | None:
    url = job.get("jobUrl")
    if not url:
        return None

    metadata: dict = {}
    department = job.get("department")
    if department:
        metadata["department"] = department
    team = job.get("team")
    if team:
        metadata["team"] = team
    job_id = job.get("id")
    if job_id:
        metadata["id"] = job_id

    workplace_type = job.get("workplaceType")
    job_location_type = _WORKPLACE_TYPE_MAP.get(workplace_type, "") if workplace_type else None

    employment_type_raw = job.get("employmentType")
    employment_type = (
        _EMPLOYMENT_TYPE_MAP.get(employment_type_raw, employment_type_raw)
        if employment_type_raw
        else None
    )

    return DiscoveredJob(
        url=url,
        title=job.get("title"),
        description=job.get("descriptionHtml") or job.get("descriptionPlain"),
        locations=_parse_locations(job),
        employment_type=employment_type,
        job_location_type=job_location_type or None,
        date_posted=job.get("publishedAt"),
        base_salary=_parse_compensation(job, compensation),
        metadata=metadata or None,
    )


def _token_from_url(board_url: str) -> str | None:
    match = re.search(r"jobs\.ashbyhq\.com/([\w-]+)", board_url)
    if match and match.group(1) not in _IGNORE_TOKENS:
        return match.group(1)
    return None


def _api_url(token: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{token}"


async def _probe_token(token: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Ashby API for a token. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(token))
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        jobs = data.get("jobs")
        if isinstance(jobs, list):
            return True, len(jobs)
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(token: str, client: httpx.AsyncClient) -> int | None:
    """Lightweight API call to get the job count for a token."""
    try:
        resp = await client.get(_api_url(token))
        if resp.status_code != 200:
            return None
        data = resp.json()
        jobs = data.get("jobs")
        return len(jobs) if isinstance(jobs, list) else None
    except Exception:
        return None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings with full content from the Ashby public API."""
    metadata = board.get("metadata") or {}
    token = metadata.get("token") or _token_from_url(board["board_url"])

    if not token:
        raise ValueError(
            f"Cannot derive Ashby token from board URL {board['board_url']!r} "
            "and no token in metadata"
        )

    url = _api_url(token)
    params = {"includeCompensation": "true"}
    response = await client.get(url, params=params)
    response.raise_for_status()

    data = response.json()
    raw_jobs = data.get("jobs", [])
    compensation = data.get("compensation")

    jobs: list[DiscoveredJob] = []
    for raw in raw_jobs:
        # Skip unlisted jobs
        if not raw.get("isListed", True):
            continue
        parsed = _parse_job(raw, compensation)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("ashby.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Ashby: domain check -> page HTML scan -> slug-based API probe."""
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
                    log.info("ashby.detected_in_page", url=url, board_token=found)
                    count = await _fetch_job_count(found, client)
                    result: dict = {"token": found}
                    if count is not None:
                        result["jobs"] = count
                    return result

    for slug in slugs_from_url(url):
        found, count = await _probe_token(slug, client)
        if found:
            log.info("ashby.detected_by_probe", url=url, board_token=slug)
            result = {"token": slug}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("ashby", discover, cost=10, can_handle=can_handle, rich=True)
