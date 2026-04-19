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


# Workday location format: "{COUNTRY}-{STATE}-{CITY}-{BUILDING} ~ {ADDRESS} ~ ..."
# Strip building/address noise after ~ and normalize the code prefix.
_TILDE_RE = re.compile(r"\s*~.*")

# Code-format: "US-AR-SPRINGDALE", "AU-NSW-NOWRA-039", "GB-LND-LONDON"
# The state segment is 2-3 uppercase letters; city is all-caps letters/spaces.
_CODE_RE = re.compile(
    r"^([A-Z]{2})"  # country (ISO-2)
    r"-([A-Z]{2,3})"  # state/province
    r"-([A-Z][A-Z ]+)"  # city (all-caps, may contain spaces)
)

# Space-separated display format without commas: "New York New York United States"
# Collapse multiple spaces before further processing.
_MULTI_SPACE_RE = re.compile(r" {2,}")


def _normalize_workday_location(raw: str) -> str:
    """Normalize a Workday location string for the resolver.

    Handles two Workday formats:
    - Code: "US-AR-SPRINGDALE-BLDG 1 ~ 275 E Robinson Ave" -> "Springdale, AR, US"
    - Display: "New York New York United States" -> "New York, New York, United States"
    """
    # Strip building/address after ~
    cleaned = _TILDE_RE.sub("", raw).strip()
    if not cleaned:
        return raw

    # Try code format first
    m = _CODE_RE.match(cleaned)
    if m:
        country, state, city = m.group(1), m.group(2), m.group(3)
        # Strip trailing building codes (digits, alphanumeric IDs like "TB1", "BLDG", etc.)
        city = re.sub(r"[-\s]+([A-Z]{0,4}\d+|BLDG).*$", "", city, flags=re.IGNORECASE).strip()
        if city:
            return f"{city.title()}, {state}, {country}"
        return f"{state}, {country}"

    # Display format: Workday uses double spaces as segment separators
    # "Sg  Singapore", "Heredia  Costa Rica", "New York  New York  United States"
    if "  " in cleaned:
        return ", ".join(part.strip() for part in cleaned.split("  ") if part.strip())

    return cleaned


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

    # Locations: primary + additional, deduplicated and normalized
    locations: list[str] | None = None
    primary = info.get("location")
    additional = info.get("additionalLocations") or []
    if primary or additional:
        seen: set[str] = set()
        locs: list[str] = []
        for loc in [primary, *additional]:
            if loc:
                normalized = _normalize_workday_location(loc)
                if normalized not in seen:
                    seen.add(normalized)
                    locs.append(normalized)
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
    # Workday soft-fails (posting removed between list + detail fetches):
    #   - 404 is the documented "not found" case.
    #   - 403 with errorCode=S22 ("permission denied") is the *undocumented*
    #     case Workday actually uses for closed/unlisted requisitions.
    #     Verified 2026-04-19 against 15 consecutive 403 URLs from Loki:
    #     0/15 were in the current LIST output — all genuinely delisted.
    # Anything else (real WAF block, 5xx, other S-codes): raise so it
    # surfaces in batch.scrape.error and gets retried.
    if resp.status_code == 404:
        log.info("workday_scraper.detail_gone", url=url, status=404)
        return JobContent()
    if resp.status_code == 403 and _is_gone_response(resp):
        log.info("workday_scraper.detail_gone", url=url, status=403, error_code="S22")
        return JobContent()
    resp.raise_for_status()

    return _parse_detail(resp.json())


def _is_gone_response(resp: httpx.Response) -> bool:
    """Detect Workday's 'gone' response shape.

    Workday's CXS detail endpoint returns 403 with a JSON body of
    ``{"errorCode": "S22", "message": "permission denied", ...}`` for
    requisitions that have been closed or unlisted. Any other 403 shape
    (raw text, HTML, different errorCode) is treated as a real error so
    actual WAF blocks or auth failures still surface.
    """
    try:
        return resp.json().get("errorCode") == "S22"
    except Exception:
        return False


register("workday", scrape)
