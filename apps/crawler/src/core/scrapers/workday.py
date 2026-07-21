"""Workday detail API scraper.

Fetches structured job data from the Workday detail endpoint:
  GET https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/job/{path}

The monitor (``src/core/monitors/workday``) discovers URLs; this scraper
fetches details on the daily scrape schedule.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_DETAIL_HEADERS = {"Accept": "application/json"}
_DETAIL_RETRY_ATTEMPTS = 3
_DETAIL_RETRY_BASE_DELAY = 0.5


class WorkdayDetailPayloadError(Exception):
    """A Workday detail endpoint repeatedly returned an invalid success body."""

    def __init__(
        self,
        *,
        attempts: int,
        reason: str,
        status: int,
        content_type: str,
        body_length: int,
        body_sha256: str,
    ) -> None:
        self.attempts = attempts
        self.reason = reason
        self.status = status
        self.content_type = content_type
        self.body_length = body_length
        self.body_sha256 = body_sha256
        super().__init__(
            "Workday detail payload remained invalid "
            f"after {attempts} attempts "
            f"(reason={reason}, status={status}, content_type={content_type!r}, "
            f"body_length={body_length}, body_sha256={body_sha256})"
        )


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
    """Normalize Workday remoteType to our enum.

    Workday emits ``remote`` / ``flexible`` / ``hybrid`` — the central
    :func:`src.core.enum_normalize.normalize_job_location_type` already
    knows ``flexible`` -> ``hybrid``.  Pass ``default=None`` so unknown
    values surface as ``None`` (preserves pre-#2992 behaviour).
    """
    return normalize_job_location_type(value, default=None)


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
    sleep = kwargs.get("sleep", asyncio.sleep)

    from src.metrics import http_retry_attempts_total, http_retry_host

    host = http_retry_host(api_url)
    retried = False
    last_payload: dict[str, object] | None = None

    for attempt in range(_DETAIL_RETRY_ATTEMPTS):
        # ``Accept`` describes the representation we want back. The previous
        # request sent ``Content-Type`` on a bodyless GET and inherited the
        # shared browser-oriented Accept header, leaving an API response open
        # to HTML content negotiation during degraded edge behavior (#5230).
        resp = await http.get(api_url, headers=_DETAIL_HEADERS)

        # Workday soft-fails (posting removed between list + detail fetches):
        #   - 404 is the documented "not found" case.
        #   - 403 with errorCode=S22 ("permission denied") is the *undocumented*
        #     case Workday actually uses for closed/unlisted requisitions.
        #     Verified 2026-04-19 against 15 consecutive 403 URLs from Loki:
        #     0/15 were in the current LIST output — all genuinely delisted.
        # Anything else (real WAF block, 5xx, other S-codes): raise so it
        # surfaces in batch.scrape.error and gets retried by the queue.
        if resp.status_code == 404:
            log.info("workday_scraper.detail_gone", url=url, status=404)
            return JobContent()
        if resp.status_code == 403 and _is_gone_response(resp):
            log.info("workday_scraper.detail_gone", url=url, status=403, error_code="S22")
            return JobContent()
        resp.raise_for_status()

        reason: str | None = None
        try:
            data = resp.json()
        except ValueError:
            data = None
            reason = "json_decode"
        if reason is None and not isinstance(data, dict):
            reason = f"json_{type(data).__name__}"
        if reason is None and not isinstance(data.get("jobPostingInfo"), dict):
            reason = "missing_job_posting_info"
        if reason is None:
            if retried:
                http_retry_attempts_total.labels(host=host, outcome="recovered").inc()
            return _parse_detail(data)

        body = resp.content
        last_payload = {
            "reason": reason,
            "status": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "body_length": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest()[:16],
        }
        retried = True
        http_retry_attempts_total.labels(host=host, outcome="retry").inc()
        log.info(
            "workday_scraper.detail_payload_retry",
            url=url,
            attempt=attempt + 1,
            **last_payload,
        )
        if attempt < _DETAIL_RETRY_ATTEMPTS - 1:
            delay = _DETAIL_RETRY_BASE_DELAY * (2**attempt) * (0.5 + random.random())
            await sleep(delay)

    assert last_payload is not None
    http_retry_attempts_total.labels(host=host, outcome="exhausted").inc()
    raise WorkdayDetailPayloadError(
        attempts=_DETAIL_RETRY_ATTEMPTS,
        reason=str(last_payload["reason"]),
        status=int(last_payload["status"]),
        content_type=str(last_payload["content_type"]),
        body_length=int(last_payload["body_length"]),
        body_sha256=str(last_payload["body_sha256"]),
    )


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
