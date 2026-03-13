"""Workable detail API scraper.

Fetches structured job data from the Workable detail endpoint:
  GET https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}

The monitor (``src/core/monitors/workable``) discovers URLs; this scraper
fetches details on the daily scrape schedule.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()

# Matches Workable job URLs — extracts slug and shortcode
# e.g. https://apply.workable.com/acme-corp/j/ABC123/
_JOB_URL_RE = re.compile(r"apply\.workable\.com/([\w-]+)/j/([\w]+)")

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


def _parse_job_url(url: str) -> tuple[str, str] | None:
    """Extract (slug, shortcode) from a Workable job URL.

    Example: https://apply.workable.com/acme-corp/j/ABC123/
      -> ("acme-corp", "ABC123")
    """
    match = _JOB_URL_RE.search(url)
    if not match:
        return None
    return match.group(1), match.group(2)


def _detail_url(slug: str, shortcode: str) -> str:
    """Build the Workable detail API URL."""
    return f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}"


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


def _parse_detail(detail: dict) -> JobContent:
    """Parse the Workable detail API response into JobContent."""
    title = detail.get("title")
    description = _build_description(detail)

    # Employment type
    raw_type = detail.get("type")
    employment_type = None
    if isinstance(raw_type, str):
        employment_type = _EMPLOYMENT_TYPE_MAP.get(raw_type.lower(), raw_type)

    # Metadata
    metadata: dict | None = None
    dept = detail.get("department")
    if isinstance(dept, str) and dept:
        metadata = {"department": dept}
    elif isinstance(dept, list) and dept:
        metadata = {"department": ", ".join(dept)}

    return JobContent(
        title=title,
        description=description,
        locations=_build_locations(detail),
        employment_type=employment_type,
        job_location_type=_parse_job_location_type(detail),
        date_posted=detail.get("published"),
        metadata=metadata,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch job details from the Workable detail API."""
    parsed = _parse_job_url(url)
    if not parsed:
        log.warning("workable_scraper.unparseable_url", url=url)
        return JobContent()

    slug, shortcode = parsed
    # Allow config override for slug
    slug = config.get("token") or slug
    api_url = _detail_url(slug, shortcode)

    resp = await http.get(api_url)
    if resp.status_code != 200:
        log.warning(
            "workable_scraper.detail_failed",
            url=url,
            status=resp.status_code,
        )
        return JobContent()

    return _parse_detail(resp.json())


register("workable", scrape)
