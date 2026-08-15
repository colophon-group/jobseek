"""HeadHunter vacancy detail API scraper."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors.headhunter import REQUEST_HEADERS, _parse_job
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_VACANCY_PATH_RE = re.compile(r"^/vacancy/(\d+)/?$", re.IGNORECASE)


def _vacancy_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {
        "hh.ru",
        "www.hh.ru",
        "rabota.by",
        "www.rabota.by",
    }:
        return None
    match = _VACANCY_PATH_RE.match(parsed.path)
    return match.group(1) if match else None


def _detail_url(vacancy_id: str) -> str:
    return f"https://api.hh.ru/vacancies/{vacancy_id}"


def parse_payload(payload: dict) -> JobContent:
    employer = payload.get("employer")
    employer_id = str(employer.get("id") or "") if isinstance(employer, dict) else ""
    parsed = _parse_job(payload, employer_id=employer_id)
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
    if not vacancy_id:
        log.warning("headhunter_scraper.invalid_url", url=url)
        return JobContent()

    response = await http.get(_detail_url(vacancy_id), headers=REQUEST_HEADERS)
    if response.status_code == 404:
        return JobContent()
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"HeadHunter vacancy {vacancy_id} returned non-object JSON")
    return parse_payload(payload)


register("headhunter", scrape)
