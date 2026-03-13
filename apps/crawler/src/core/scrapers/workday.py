"""Workday detail API scraper.

Fetches structured job data from the Workday detail endpoint:
  GET https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/job/{path}

The monitor (``src/core/monitors/workday``) discovers URLs; this scraper
fetches details on the daily scrape schedule.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()

# Matches Workday job URLs — extracts company, wd instance number, site, and job path
_JOB_URL_RE = re.compile(
    r"([\w-]+)\.wd(\d+)\.myworkdayjobs\.com/"
    r"(?:[a-z]{2}-[A-Z]{2}/)?"  # optional locale prefix
    r"([^/]+)"  # site
    r"(/job/.+)"  # /job/... path
)


def _parse_job_url(url: str) -> tuple[str, str, str, str] | None:
    """Extract (company, wd_instance, site, path) from a Workday job URL.

    Example: https://nvidia.wd5.myworkdayjobs.com/ExtSite/job/Senior-Engineer/JR001
      -> ("nvidia", "wd5", "ExtSite", "/job/Senior-Engineer/JR001")
    """
    match = _JOB_URL_RE.search(url)
    if not match:
        return None
    company = match.group(1)
    wd_instance = f"wd{match.group(2)}"
    site = match.group(3)
    path = match.group(4)
    return company, wd_instance, site, path


def _detail_url(company: str, wd_instance: str, site: str, path: str) -> str:
    """Build the Workday detail API URL."""
    return f"https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}{path}"


def _parse_location_type(value: str | None) -> str | None:
    """Normalize Workday remoteType to our enum."""
    if not value:
        return None
    lower = value.lower()
    if lower == "remote":
        return "remote"
    if lower in ("flexible", "hybrid"):
        return "hybrid"
    return None


def _parse_detail(data: dict) -> JobContent:
    """Parse the Workday detail API response into JobContent."""
    info = data.get("jobPostingInfo", {})

    title = info.get("title")
    description = info.get("jobDescription")

    # Locations: primary + additional, deduplicated
    locations: list[str] | None = None
    primary = info.get("location")
    additional = info.get("additionalLocations") or []
    if primary or additional:
        seen: set[str] = set()
        locs: list[str] = []
        for loc in [primary, *additional]:
            if loc and loc not in seen:
                seen.add(loc)
                locs.append(loc)
        if locs:
            locations = locs

    employment_type = info.get("timeType")
    job_location_type = _parse_location_type(info.get("remoteType"))
    date_posted = info.get("startDate")

    # Metadata: jobReqId
    metadata: dict | None = None
    req_id = info.get("jobReqId")
    if req_id:
        metadata = {"jobReqId": req_id}

    return JobContent(
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=date_posted,
        metadata=metadata,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch job details from the Workday detail API."""
    parsed = _parse_job_url(url)
    if not parsed:
        log.warning("workday_scraper.unparseable_url", url=url)
        return JobContent()

    company, wd_instance, site, path = parsed
    api_url = _detail_url(company, wd_instance, site, path)

    resp = await http.get(api_url, headers={"Content-Type": "application/json"})
    if resp.status_code != 200:
        log.warning(
            "workday_scraper.detail_failed",
            url=url,
            status=resp.status_code,
        )
        return JobContent()

    return _parse_detail(resp.json())


register("workday", scrape)
