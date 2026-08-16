"""HeadHunter vacancy detail API scraper."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors.headhunter import REQUEST_HEADERS, _parse_job, _site_host_from_url
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_VACANCY_PATH_RE = re.compile(r"^/vacancy/(\d+)/?$", re.IGNORECASE)


def _vacancy_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() == "api.hh.ru" or _site_host_from_url(url) is None:
        return None
    match = _VACANCY_PATH_RE.match(parsed.path)
    return match.group(1) if match else None


def _detail_url(vacancy_id: str) -> str:
    return f"https://api.hh.ru/vacancies/{vacancy_id}"


def parse_payload(payload: dict, *, site_host: str = "hh.ru") -> JobContent:
    employer = payload.get("employer")
    employer_id = str(employer.get("id") or "") if isinstance(employer, dict) else ""
    parsed = _parse_job(payload, employer_id=employer_id, site_host=site_host)
    if parsed is None:
        return JobContent()
    return JobContent(
        title=parsed.title,
        description=parsed.description,
        locations=parsed.locations,
        employment_type=parsed.employment_type,
        job_location_type=parsed.job_location_type,
        date_posted=parsed.date_posted,
        base_salary=parsed.base_salary,
        language=parsed.language,
        extras=parsed.extras,
        metadata=parsed.metadata,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Hydrate one public HeadHunter vacancy through its JSON detail API."""
    _ = config, kwargs
    vacancy_id = _vacancy_id_from_url(url)
    site_host = _site_host_from_url(url)
    if not vacancy_id or not site_host:
        log.warning("headhunter_scraper.invalid_url", url=url)
        return JobContent()

    response = await http.get(
        _detail_url(vacancy_id), params={"host": site_host}, headers=REQUEST_HEADERS
    )
    if response.status_code == 404:
        return JobContent()
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"HeadHunter vacancy {vacancy_id} returned non-object JSON")
    if str(payload.get("id") or "") != vacancy_id:
        raise ValueError(f"HeadHunter vacancy response ID does not match {vacancy_id}")
    return parse_payload(payload, site_host=site_host)


register("headhunter", scrape)
