"""Rippling detail API scraper.

Fetches structured job data from the Rippling detail endpoint:
  GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}

The monitor (``src/core/monitors/rippling``) discovers URLs; this scraper
fetches details on the daily scrape schedule.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_API_BASE = "https://api.rippling.com/platform/api/ats/v1/board"

# Matches ats.rippling.com or ats.us1.rippling.com, with optional locale prefix
# Extracts both slug and uuid
_JOB_URL_RE = re.compile(
    r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs/([\w-]+)"
)

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "SALARIED_FT": "Full-time",
    "SALARIED_PT": "Part-time",
    "HOURLY_FT": "Full-time",
    "HOURLY_PT": "Part-time",
    "INTERN": "Intern",
    "CONTRACT": "Contract",
    "TEMPORARY": "Temporary",
}


def _extract_job_params(url: str) -> tuple[str, str] | None:
    """Extract (slug, uuid) from a Rippling job URL.

    Example: https://ats.rippling.com/acme-corp/jobs/abc-123
      -> ("acme-corp", "abc-123")
    """
    match = _JOB_URL_RE.search(url)
    if not match:
        return None
    return match.group(1), match.group(2)


def _detail_url(slug: str, uuid: str) -> str:
    """Build the Rippling detail API URL."""
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


def _parse_detail(detail: dict) -> JobContent:
    """Parse a Rippling detail API response into JobContent."""
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

    return JobContent(
        title=detail.get("name"),
        description=description,
        locations=locations,
        employment_type=_parse_employment_type(detail.get("employmentType")),
        job_location_type=_parse_job_location_type(locations),
        date_posted=detail.get("createdOn"),
        base_salary=_parse_salary(detail.get("payRangeDetails")),
        metadata=metadata or None,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch job details from the Rippling detail API."""
    parsed = _extract_job_params(url)
    if not parsed:
        log.warning("rippling_scraper.unparseable_url", url=url)
        return JobContent()

    _url_slug, uuid = parsed
    # Prefer config slug over URL-extracted slug
    slug = config.get("slug") or _url_slug
    api_url = _detail_url(slug, uuid)

    resp = await http.get(api_url)
    if resp.status_code != 200:
        log.warning(
            "rippling_scraper.detail_failed",
            url=url,
            status=resp.status_code,
        )
        return JobContent()

    return _parse_detail(resp.json())


register("rippling", scrape)
