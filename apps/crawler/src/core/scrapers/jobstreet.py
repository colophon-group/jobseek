"""JobStreet job-detail GraphQL scraper."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors.jobstreet import _HOST_CONFIG, _clean_text, _graphql, _parse_salary_label
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_JOB_PATH_RE = re.compile(r"^/job/(\d{1,18})/?$", re.IGNORECASE)
_DETAIL_QUERY = """
query Job($id: ID!, $locale: Locale!) {
  jobDetails(id: $id) {
    job {
      id
      title
      content
      isExpired
      status
      location { label(locale: $locale, type: LONG) }
      workTypes { label(locale: $locale) }
      salary { label }
      advertiser { name(locale: $locale) }
      createdAt { dateTimeUtc }
      expiresAt { dateTimeUtc }
    }
  }
}
"""


def _job_identity_from_url(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in _HOST_CONFIG
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None
    match = _JOB_PATH_RE.fullmatch(parsed.path)
    return (host, match.group(1)) if match else None


def _date_time(job: dict, field: str) -> str | None:
    value = job.get(field)
    return _clean_text(value.get("dateTimeUtc")) if isinstance(value, dict) else None


def parse_payload(payload: dict, *, host: str, job_id: str) -> JobContent:
    details = payload.get("jobDetails")
    if details is None:
        return JobContent()
    job = details.get("job") if isinstance(details, dict) else None
    if not isinstance(job, dict):
        raise ValueError("JobStreet detail response omitted its job object")
    if str(job.get("id") or "") != job_id:
        raise ValueError(f"JobStreet detail response ID does not match {job_id}")
    if job.get("isExpired") is True or str(job.get("status") or "").lower() != "active":
        return JobContent()

    title = _clean_text(job.get("title"))
    description = job.get("content")
    if not title or not isinstance(description, str) or not description.strip():
        raise ValueError(f"JobStreet job {job_id} omitted title or description")

    location = job.get("location")
    location_label = _clean_text(location.get("label")) if isinstance(location, dict) else None
    work_types = job.get("workTypes")
    employment_type = _clean_text(work_types.get("label")) if isinstance(work_types, dict) else None
    salary = job.get("salary")
    salary_label = _clean_text(salary.get("label")) if isinstance(salary, dict) else None
    advertiser = job.get("advertiser")
    employer = _clean_text(advertiser.get("name")) if isinstance(advertiser, dict) else None

    metadata: dict[str, object] = {"jobstreet_job_id": job_id}
    if employer:
        metadata["employer"] = employer
    if expiration := _date_time(job, "expiresAt"):
        metadata["expiration_date"] = expiration

    return JobContent(
        title=title,
        description=description,
        locations=[location_label] if location_label else None,
        employment_type=employment_type,
        date_posted=_date_time(job, "createdAt"),
        base_salary=_parse_salary_label(salary_label, host=host),
        language=_HOST_CONFIG[host]["language"],
        metadata=metadata,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Hydrate one canonical JobStreet job through its anonymous GraphQL API."""
    _ = config, kwargs
    identity = _job_identity_from_url(url)
    if identity is None:
        log.warning("jobstreet_scraper.invalid_url", url=url)
        return JobContent()
    host, job_id = identity
    data = await _graphql(
        http,
        host,
        _DETAIL_QUERY,
        {"id": job_id, "locale": _HOST_CONFIG[host]["locale"]},
    )
    return parse_payload(data, host=host, job_id=job_id)


register("jobstreet", scrape)
