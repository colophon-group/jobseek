"""Paycom job-detail scraper using the portal's public regional API."""

from __future__ import annotations

import json
import re

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type
from src.core.monitors.paycom import (
    _bootstrap,
    _clean_string,
    _token_from_url,
)
from src.core.salary_extract import parse_salary_text
from src.core.scrapers import JobContent, register
from src.shared.http_retry import PaginationFetchError, fetch_json_page_with_retry

log = structlog.get_logger()

_JOB_PATH_RE = re.compile(
    r"^/v4/ats/web\.php/portal/[0-9a-f]{32}/jobs/(?P<job_id>[1-9]\d*)/?$",
    re.IGNORECASE,
)


def _job_id_from_url(url: str) -> str | None:
    try:
        path = httpx.URL(url).path
    except (TypeError, ValueError):
        return None
    match = _JOB_PATH_RE.fullmatch(path)
    return match.group("job_id") if match else None


def _locations(detail: dict) -> list[str] | None:
    values: list[str] = []
    primary = _clean_string(detail.get("location"))
    if primary:
        values.append(primary)
    secondary = detail.get("secondaryLocations")
    if isinstance(secondary, list):
        for raw in secondary:
            value = _clean_string(raw)
            if value and value.casefold() not in {item.casefold() for item in values}:
                values.append(value)
    return values or None


def _google_job(detail: dict) -> dict:
    raw = detail.get("googleJobJson")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("paycom_scraper.invalid_google_job_json")
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_detail(payload: dict) -> JobContent:
    detail = payload.get("jobPosting")
    if not isinstance(detail, dict):
        raise ValueError("Paycom detail response omitted jobPosting")

    google_job = _google_job(detail)
    description = _clean_string(detail.get("description"))
    qualifications = _clean_string(detail.get("qualifications"))
    salary_text = _clean_string(detail.get("salaryRange"))
    metadata = {
        key: value
        for key, value in {
            "ats_job_id": detail.get("jobId"),
            "client_code": detail.get("clientCode"),
            "department": detail.get("jobCategory"),
            "shift": detail.get("jobShift"),
            "education_level": detail.get("educationLevel"),
            "travel_percentage": detail.get("travelPercentage"),
            "is_hot_job": detail.get("isHotJob"),
        }.items()
        if value not in (None, "")
    }
    return JobContent(
        title=_clean_string(detail.get("jobTitle")) or _clean_string(google_job.get("title")),
        description=description,
        locations=_locations(detail),
        employment_type=_clean_string(detail.get("positionType"))
        or _clean_string(google_job.get("employmentType")),
        job_location_type=normalize_job_location_type(_clean_string(detail.get("remoteType"))),
        date_posted=_clean_string(google_job.get("datePosted")),
        base_salary=parse_salary_text(salary_text) if salary_text else None,
        extras={"qualifications": qualifications} if qualifications else None,
        metadata=metadata or None,
    )


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict | None:
    _ = client
    return {} if _token_from_url(url) and _job_id_from_url(url) else None


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    **kwargs,
) -> JobContent:
    """Bootstrap the portal and fetch one authoritative job detail object."""
    _ = config, kwargs
    token = _token_from_url(url)
    job_id = _job_id_from_url(url)
    if token is None or job_id is None:
        log.error("paycom_scraper.invalid_job_url", url=url)
        return JobContent()

    bootstrap = await _bootstrap(token, http)
    detail_url = f"{bootstrap.service_url}/api/ats/job-postings/{job_id}"
    try:
        payload = await fetch_json_page_with_retry(
            http,
            detail_url,
            expect_shape=dict,
            headers=bootstrap.headers,
            retries=3,
            base_delay=0.5,
            log_event="paycom.detail_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in {404, 410}:
            log.info("paycom_scraper.job_gone", url=url)
            return JobContent()
        raise
    return _parse_detail(payload)


register("paycom", scrape, can_handle=can_handle)
