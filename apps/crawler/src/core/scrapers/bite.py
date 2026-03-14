"""BITE GmbH ATS detail API scraper.

Fetches structured job data from the BITE detail endpoint:
  GET https://jobs.b-ite.com/jobposting/{hash}/json?locale={locale}&contentRendered=true

The monitor (``src/core/monitors/bite``) discovers URLs; this scraper
fetches details on the daily scrape schedule.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_DETAIL_URL = "https://jobs.b-ite.com/jobposting/{hash}/json"

_HASH_RE = re.compile(r"/jobposting/([a-f0-9]{40,42})")

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "full_time": "full-time",
    "part_time": "part-time",
    "temporary": "temporary",
    "contract": "contract",
    "internship": "internship",
    "mini_job": "part-time",
    "volunteer": "volunteer",
}


def _extract_hash_from_url(url: str) -> str | None:
    """Extract job hash from a BITE job posting URL."""
    m = _HASH_RE.search(url)
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


def _parse_detail(detail: dict, locale: str) -> JobContent:
    """Parse a detail endpoint response into JobContent."""
    title = detail.get("title")

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

    return JobContent(
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        date_posted=date_posted,
        base_salary=base_salary,
        language=detail.get("locale") or locale,
        metadata=metadata or None,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch job details from the BITE detail API."""
    job_hash = _extract_hash_from_url(url)
    if not job_hash:
        log.warning("bite_scraper.unparseable_url", url=url)
        return JobContent()

    locale = config.get("locale", "de")
    detail_url = _DETAIL_URL.format(hash=job_hash) + f"?locale={locale}&contentRendered=true"

    resp = await http.get(detail_url)
    if resp.status_code != 200:
        log.warning("bite_scraper.detail_failed", url=url, status=resp.status_code)
        return JobContent()

    return _parse_detail(resp.json(), locale)


register("bite", scrape)
