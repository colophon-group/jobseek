"""HeadHunter employer-board monitor.

HeadHunter exposes a public JSON API for employer-scoped vacancy searches.
The public web board and API are protected by ddos-guard for some datacenter
networks, so detected boards opt into the crawler's existing proxy transport.

API:
  List:   GET https://api.hh.ru/vacancies?employer_id={id}&page=N
  Detail: GET https://api.hh.ru/vacancies/{vacancy_id}
"""

from __future__ import annotations

import re
from math import ceil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.core.monitors.raw import save_json_response
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

API_BASE = "https://api.hh.ru"
PAGE_SIZE = 100
MAX_JOBS = 2_000  # HeadHunter's public API caps deep pagination at 2,000.
REQUEST_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Jobseek/1.0 (+https://jobseek.ch; contact@jobseek.ch)",
}

_EMPLOYER_PATH_RE = re.compile(r"^/employer/(\d+)/?$", re.IGNORECASE)
_SITE_HOSTS = frozenset(
    {"hh.ru", "rabota.by", "hh1.az", "hh.uz", "hh.kz", "headhunter.ge", "headhunter.kg"}
)


def _site_host_from_url(url: str) -> str | None:
    """Return the public HeadHunter site selected by an exact, safe URL."""
    parsed = urlparse(url)
    try:
        unsafe_authority = bool(parsed.username or parsed.password or parsed.port)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or unsafe_authority:
        return None

    hostname = (parsed.hostname or "").lower()
    if hostname == "api.hh.ru":
        requested = parse_qs(parsed.query).get("host", ["hh.ru"])[0].lower()
        return requested if requested in _SITE_HOSTS else None
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname if hostname in _SITE_HOSTS else None


def _employer_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if _site_host_from_url(url) is None:
        return None

    hostname = (parsed.hostname or "").lower()
    if hostname == "api.hh.ru" and parsed.path.rstrip("/") == "/vacancies":
        values = parse_qs(parsed.query).get("employer_id", [])
        for value in values:
            if value.isdigit():
                return value
        return None
    if hostname == "api.hh.ru":
        return None

    match = _EMPLOYER_PATH_RE.match(parsed.path)
    if match:
        return match.group(1)
    return None


def _listing_url() -> str:
    return f"{API_BASE}/vacancies"


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _dict_name(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return _clean_text(value.get("name"))


def _locations(job: dict) -> list[str] | None:
    address = job.get("address")
    if isinstance(address, dict):
        raw = _clean_text(address.get("raw"))
        if raw:
            return [raw]

        parts: list[str] = []
        for key in ("city", "street", "building"):
            value = _clean_text(address.get(key))
            if value and value not in parts:
                parts.append(value)
        if parts:
            return [", ".join(parts)]

    area = _dict_name(job.get("area"))
    return [area] if area else None


def _salary_values(salary: object, *, unit: str) -> dict | None:
    if not isinstance(salary, dict):
        return None
    currency = _clean_text(salary.get("currency"))
    minimum = salary.get("from")
    maximum = salary.get("to")
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
        minimum = None
    if isinstance(maximum, bool) or not isinstance(maximum, (int, float)):
        maximum = None
    if not currency or (minimum is None and maximum is None):
        return None
    return {
        "currency": currency,
        "min": minimum,
        "max": maximum,
        "unit": unit,
    }


def _salary(job: dict) -> dict | None:
    """Map current salary_range data, with a legacy monthly fallback."""
    salary_range = job.get("salary_range")
    if isinstance(salary_range, dict):
        mode = salary_range.get("mode")
        mode_id = _clean_text(mode.get("id")) if isinstance(mode, dict) else None
        unit = {"MONTH": "month", "HOUR": "hour"}.get((mode_id or "").upper())
        # Shift/service/fly-in-fly-out amounts have no canonical salary unit in
        # Jobseek. Dropping them is safer than labelling them as monthly.
        return _salary_values(salary_range, unit=unit) if unit else None

    # The deprecated salary object predates explicit granularity and is
    # documented by HeadHunter as a monthly range.
    return _salary_values(job.get("salary"), unit="month")


def _job_location_type(job: dict) -> str | None:
    work_formats = job.get("work_format")
    if isinstance(work_formats, list):
        identifiers = {
            str(item.get("id", "")).strip().upper()
            for item in work_formats
            if isinstance(item, dict)
        }
        if "HYBRID" in identifiers:
            return "hybrid"
        if "REMOTE" in identifiers:
            return "remote"
        if "ON_SITE" in identifiers:
            return "onsite"

    schedule = job.get("schedule")
    if isinstance(schedule, dict) and str(schedule.get("id", "")).lower() == "remote":
        return "remote"
    return None


def _named_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        name = _dict_name(item)
        if name:
            result.append(name)
    return result


def _parse_job(job: dict, *, employer_id: str, site_host: str = "hh.ru") -> DiscoveredJob | None:
    vacancy_id = str(job.get("id") or "")
    employer = job.get("employer")
    actual_employer_id = str(employer.get("id") or "") if isinstance(employer, dict) else ""
    if not vacancy_id.isdigit() or actual_employer_id != employer_id:
        return None

    if site_host not in _SITE_HOSTS:
        return None
    url = f"https://{site_host}/vacancy/{vacancy_id}"

    extras: dict = {}
    skills = _named_list(job.get("key_skills"))
    if skills:
        extras["skills"] = skills
    roles = _named_list(job.get("professional_roles"))
    if roles:
        extras["professional_roles"] = roles
    languages = _named_list(job.get("languages"))
    if languages:
        extras["languages"] = languages

    metadata: dict = {
        "vacancy_id": vacancy_id,
        "headhunter_employer_id": employer_id,
    }
    if isinstance(employer, dict):
        employer_name = _clean_text(employer.get("name"))
        if employer_name:
            metadata["employer"] = employer_name
    for source, target in (
        ("department", "department"),
        ("experience", "experience"),
        ("schedule", "schedule"),
    ):
        name = _dict_name(job.get(source))
        if name:
            metadata[target] = name

    employment = job.get("employment_form")
    if not isinstance(employment, dict):
        employment = job.get("employment")
    employment_type = None
    if isinstance(employment, dict):
        employment_type = _clean_text(employment.get("id")) or _clean_text(employment.get("name"))
        if employment_type:
            employment_type = employment_type.lower()

    return DiscoveredJob(
        url=url,
        title=_clean_text(job.get("name")),
        description=job.get("description") if isinstance(job.get("description"), str) else None,
        locations=_locations(job),
        employment_type=employment_type,
        job_location_type=_job_location_type(job),
        date_posted=_clean_text(job.get("published_at")),
        base_salary=_salary(job),
        extras=extras or None,
        metadata=metadata,
    )


async def _fetch_summaries(
    client: httpx.AsyncClient,
    employer_id: str,
    *,
    site_host: str,
) -> tuple[list[dict], bool]:
    summaries: list[dict] = []
    page = 0
    expected_found: int | None = None
    expected_pages: int | None = None
    seen_ids: set[str] = set()

    while expected_pages is None or page < max(1, expected_pages):
        response = await client.get(
            _listing_url(),
            params={
                "employer_id": employer_id,
                "page": page,
                "per_page": PAGE_SIZE,
                "host": site_host,
            },
            headers=REQUEST_HEADERS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("HeadHunter vacancy response is not an object")
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("HeadHunter vacancy response has no items array")

        pagination: dict[str, int] = {}
        for field in ("found", "page", "pages", "per_page"):
            value = payload.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"HeadHunter vacancy response has invalid {field!r}")
            pagination[field] = value
        if pagination["page"] != page or pagination["per_page"] != PAGE_SIZE:
            raise ValueError("HeadHunter vacancy response pagination does not match the request")

        found = pagination["found"]
        pages = pagination["pages"]
        calculated_pages = min(ceil(found / PAGE_SIZE), ceil(MAX_JOBS / PAGE_SIZE))
        if pages != calculated_pages:
            raise ValueError(
                f"HeadHunter vacancy response reports {pages} pages for {found} results"
            )
        if expected_found is None:
            expected_found = found
            expected_pages = pages
        elif found != expected_found or pages != expected_pages:
            raise ValueError("HeadHunter vacancy totals changed during pagination")

        for item in items:
            if not isinstance(item, dict):
                raise ValueError("HeadHunter vacancy response contains a non-object item")
            vacancy_id = str(item.get("id") or "")
            if not vacancy_id.isdigit():
                raise ValueError("HeadHunter vacancy response contains an invalid vacancy ID")
            if vacancy_id in seen_ids:
                raise ValueError(f"HeadHunter vacancy {vacancy_id} repeated across pages")
            seen_ids.add(vacancy_id)
            summaries.append(item)
        page += 1

    assert expected_found is not None
    expected_count = min(expected_found, MAX_JOBS)
    if len(summaries) != expected_count:
        raise ValueError(
            f"HeadHunter returned {len(summaries)} unique vacancies, expected {expected_count}"
        )
    return summaries, expected_found > MAX_JOBS


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return rich vacancy summaries for one HeadHunter employer."""
    _ = pw
    metadata = board.get("metadata") or {}
    url_employer_id = _employer_id_from_url(board["board_url"])
    configured_employer_id = metadata.get("employer_id")
    employer_id = str(configured_employer_id or url_employer_id or "")
    site_host = str(metadata.get("host") or _site_host_from_url(board["board_url"]) or "")
    if not employer_id.isdigit():
        raise ValueError(
            "HeadHunter monitor requires numeric employer_id or an employer board URL; "
            f"got {board['board_url']!r}"
        )
    if (
        url_employer_id
        and configured_employer_id
        and str(configured_employer_id) != url_employer_id
    ):
        raise ValueError("HeadHunter employer_id does not match the configured board URL")

    if site_host not in _SITE_HOSTS:
        raise ValueError(
            f"HeadHunter monitor requires a supported HTTPS site host; got {site_host!r}"
        )

    summaries, truncated = await _fetch_summaries(client, employer_id, site_host=site_host)
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for summary in summaries:
        parsed = _parse_job(summary, employer_id=employer_id, site_host=site_host)
        if parsed is None:
            raise ValueError("HeadHunter returned a vacancy outside the configured employer")
        if parsed.url in seen:
            raise ValueError(f"HeadHunter returned duplicate vacancy URL {parsed.url}")
        seen.add(parsed.url)
        jobs.append(parsed)

    log.info(
        "headhunter.discovered",
        employer_id=employer_id,
        jobs=len(jobs),
        truncated=truncated,
    )
    if truncated:
        return truncated_rich_result(jobs)
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect a HeadHunter employer board or employer-filtered API URL."""
    _ = pw
    employer_id = _employer_id_from_url(url)
    site_host = _site_host_from_url(url)
    if not employer_id or not site_host:
        return None

    # HeadHunter blocks some datacenter ranges with ddos-guard. Preserve the
    # strong host/path detection and opt the board into the existing proxy
    # transport even when this direct probe cannot count jobs.
    result: dict[str, str | int | bool] = {
        "employer_id": employer_id,
        "proxy": True,
    }
    if client is None:
        return result

    try:
        response = await client.get(
            _listing_url(),
            params={"employer_id": employer_id, "page": 0, "per_page": 1, "host": site_host},
            headers=REQUEST_HEADERS,
        )
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict):
                result["jobs"] = int(payload.get("found") or 0)
        elif response.status_code != 403:
            return None
    except (httpx.HTTPError, ValueError, TypeError):
        log.debug("headhunter.probe_failed", url=url, exc_info=True)
    return result


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    employer_id = str(metadata.get("employer_id") or _employer_id_from_url(board_url) or "")
    site_host = str(metadata.get("host") or _site_host_from_url(board_url) or "")
    if not employer_id or site_host not in _SITE_HOSTS:
        return
    await save_json_response(
        artifact_dir,
        client,
        _listing_url(),
        params={
            "employer_id": employer_id,
            "page": 0,
            "per_page": PAGE_SIZE,
            "host": site_host,
        },
        headers=REQUEST_HEADERS,
        filename="headhunter-listing.json",
    )


register("headhunter", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
