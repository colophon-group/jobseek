"""SmartRecruiters detail API scraper.

Fetches structured job data from the SmartRecruiters detail endpoint:
  GET https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}

The monitor (``src/core/monitors/smartrecruiters``) discovers URLs; this scraper
fetches details on the daily scrape schedule.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()

# Matches SmartRecruiters job URLs — extracts posting_id from path
# e.g. https://jobs.smartrecruiters.com/Nexthink/743999106810286
#      https://jobs.smartrecruiters.com/Nexthink/743999106810286-senior-software-engineer
_JOB_URL_RE = re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/[\w-]+/([\w-]+)")


def _extract_posting_id(url: str) -> str | None:
    """Extract posting_id from a SmartRecruiters job URL.

    After URL normalization, URLs are /{token}/{posting_id} (bare numeric ID).
    Pre-migration URLs may have /{token}/{posting_id}-seo-slug — the full
    second path segment is used as posting_id either way, since the API
    accepts both forms.
    """
    match = _JOB_URL_RE.search(url)
    if not match:
        return None
    return match.group(1)


def _detail_url(token: str, posting_id: str) -> str:
    """Build the SmartRecruiters detail API URL."""
    return f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}"


def _build_description(job_ad: dict) -> str | None:
    """Combine jobAd sections into a single HTML description."""
    if not job_ad:
        return None
    sections = job_ad.get("sections", {})
    parts: list[str] = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key)
        if isinstance(section, dict):
            title = section.get("title", "")
            text = section.get("text", "")
            if text:
                if title:
                    parts.append(f"<h3>{title}</h3>\n{text}")
                else:
                    parts.append(text)
    return "\n".join(parts) if parts else None


def _build_location(loc: dict) -> str | None:
    """Build a human-readable location string."""
    if not loc:
        return None
    full = loc.get("fullLocation")
    if full:
        return full
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    filtered = [p for p in parts if p]
    return ", ".join(filtered) if filtered else None


def _parse_salary(posting: dict) -> dict | None:
    """Extract salary from the compensation field if available."""
    comp = posting.get("compensation")
    if not comp:
        return None
    salary = comp.get("salary")
    if not salary:
        return None
    sal_min = salary.get("min")
    sal_max = salary.get("max")
    if sal_min is None and sal_max is None:
        return None
    currency = salary.get("currency")
    period = salary.get("period", "")
    unit = "year"
    if "hour" in period.lower():
        unit = "hour"
    elif "month" in period.lower():
        unit = "month"
    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_detail(posting: dict) -> JobContent:
    """Parse a SmartRecruiters detail API response into JobContent."""
    title = posting.get("name")
    description = _build_description(posting.get("jobAd", {}))

    # Location
    loc = posting.get("location", {})
    location_str = _build_location(loc)
    locations = [location_str] if location_str else None

    # Remote detection
    job_location_type = None
    if loc.get("remote"):
        job_location_type = "remote"
    elif loc.get("hybrid"):
        job_location_type = "hybrid"

    # Employment type
    employment = posting.get("typeOfEmployment")
    employment_type = employment.get("label") if isinstance(employment, dict) else None

    # Metadata
    metadata: dict = {}
    dept = posting.get("department")
    if isinstance(dept, dict) and dept.get("label"):
        metadata["department"] = dept["label"]
    func = posting.get("function")
    if isinstance(func, dict) and func.get("label"):
        metadata["function"] = func["label"]
    exp = posting.get("experienceLevel")
    if isinstance(exp, dict) and exp.get("label"):
        metadata["experienceLevel"] = exp["label"]

    return JobContent(
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=posting.get("releasedDate"),
        base_salary=_parse_salary(posting),
        metadata=metadata or None,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch job details from the SmartRecruiters detail API."""
    posting_id = _extract_posting_id(url)
    if not posting_id:
        log.warning("smartrecruiters_scraper.unparseable_url", url=url)
        return JobContent()

    token = config.get("token")
    if not token:
        log.warning("smartrecruiters_scraper.no_token", url=url)
        return JobContent()

    api_url = _detail_url(token, posting_id)
    resp = await http.get(api_url)
    if resp.status_code != 200:
        log.warning(
            "smartrecruiters_scraper.detail_failed",
            url=url,
            status=resp.status_code,
        )
        return JobContent()

    return _parse_detail(resp.json())


register("smartrecruiters", scrape)
