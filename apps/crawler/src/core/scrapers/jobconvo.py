"""JobConvo public detail API scraper."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_JOB_PATH_RE = re.compile(
    rf"^/job/(?P<slug>[A-Za-z0-9_-]+)/(?P<job_id>{_UUID})/?$",
    re.IGNORECASE,
)
_LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)
_LOCATION_TYPE_MAP = {
    0: "onsite",
    1: "hybrid",
    2: "remote",
    "0": "onsite",
    "1": "hybrid",
    "2": "remote",
}


def _parse_job_url(url: str) -> tuple[str, str] | None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
        "app.jobconvo.com",
        "jobs.jobconvo.com",
    }:
        return None
    match = _JOB_PATH_RE.fullmatch(parsed.path)
    if match is None:
        return None
    return match.group("slug"), match.group("job_id").lower()


def _detail_url(locale: str, slug: str, job_id: str) -> str:
    return f"https://app.jobconvo.com/{locale}/api/job/{job_id}/{slug}/"


def _description(detail: dict) -> tuple[str | None, dict | None]:
    description = detail.get("description")
    if not isinstance(description, str) or not description.strip():
        description = None

    extras: dict[str, str] = {}
    requirements = detail.get("requirements")
    if isinstance(requirements, str) and requirements.strip():
        extras["qualifications"] = requirements
    benefits = detail.get("benefits")
    if isinstance(benefits, str) and benefits.strip():
        benefit_html = f"<h3>Benefits</h3>\n{benefits}"
        description = f"{description}\n{benefit_html}" if description else benefit_html
    return description, extras or None


def _locations(detail: dict) -> list[str] | None:
    parts: list[str] = []
    for key in ("city", "state", "country"):
        value = detail.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return [", ".join(parts)] if parts else None


def _parse_detail(detail: dict) -> JobContent:
    description, extras = _description(detail)
    raw_location_type = detail.get("type_work_location")
    job_location_type = normalize_job_location_type(
        _LOCATION_TYPE_MAP.get(raw_location_type),
        default=None,
    )

    metadata: dict = {}
    for source, target in (
        ("id", "id"),
        ("company", "company"),
        ("deadline", "deadline"),
        ("level", "level"),
        ("status", "status"),
    ):
        value = detail.get(source)
        if value not in (None, ""):
            metadata[target] = value

    language = detail.get("company_language")
    if isinstance(language, str):
        language = language.split("-", maxsplit=1)[0].lower()
        if len(language) != 2:
            language = None
    else:
        language = None

    return JobContent(
        title=detail.get("title") if isinstance(detail.get("title"), str) else None,
        description=description,
        locations=_locations(detail),
        employment_type=(
            detail.get("employment") if isinstance(detail.get("employment"), str) else None
        ),
        job_location_type=job_location_type,
        date_posted=detail.get("pub_date") if isinstance(detail.get("pub_date"), str) else None,
        base_salary=detail.get("salary"),
        language=language,
        extras=extras,
        metadata=metadata or None,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    _ = kwargs
    parsed = _parse_job_url(url)
    if parsed is None:
        log.warning("jobconvo_scraper.unparseable_url", url=url)
        return JobContent()
    slug, job_id = parsed

    locale = config.get("locale", "pt-br")
    if not isinstance(locale, str) or _LOCALE_RE.fullmatch(locale) is None:
        raise ValueError("JobConvo scraper locale must be an ISO locale")
    locale = locale.lower()

    response = await http.get(
        _detail_url(locale, slug, job_id),
        headers={"Accept": "application/json"},
    )
    if response.status_code != 200:
        log.warning("jobconvo_scraper.detail_failed", url=url, status=response.status_code)
        return JobContent()
    detail = response.json()
    if not isinstance(detail, dict) or str(detail.get("id", "")).lower() != job_id:
        log.warning("jobconvo_scraper.invalid_detail", url=url)
        return JobContent()
    return _parse_detail(detail)


register("jobconvo", scrape)
