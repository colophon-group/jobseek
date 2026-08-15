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
_HH_HOSTS = frozenset(
    {
        "hh.ru",
        "www.hh.ru",
        "api.hh.ru",
        "rabota.by",
        "www.rabota.by",
        "api.rabota.by",
    }
)


def _employer_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in _HH_HOSTS:
        return None

    match = _EMPLOYER_PATH_RE.match(parsed.path)
    if match:
        return match.group(1)

    if parsed.path.rstrip("/") == "/vacancies":
        values = parse_qs(parsed.query).get("employer_id", [])
        for value in values:
            if value.isdigit():
                return value
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


def _salary(job: dict) -> dict | None:
    salary = job.get("salary")
    if not isinstance(salary, dict):
        return None
    currency = _clean_text(salary.get("currency"))
    minimum = salary.get("from")
    maximum = salary.get("to")
    if not currency or (minimum is None and maximum is None):
        return None
    return {
        "currency": currency,
        "min": minimum,
        "max": maximum,
        # HeadHunter's salary range is a monthly amount unless the API says
        # otherwise; the public schema has no period field.
        "unit": "month",
    }


def _job_location_type(job: dict) -> str | None:
    work_formats = job.get("work_format")
    if isinstance(work_formats, list):
        identifiers = {
            str(item.get("id", "")).lower() for item in work_formats if isinstance(item, dict)
        }
        if any("hybrid" in value for value in identifiers):
            return "hybrid"
        if any("remote" in value for value in identifiers):
            return "remote"
        if identifiers and any("on_site" in value or "onsite" in value for value in identifiers):
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


def _parse_job(job: dict, *, employer_id: str) -> DiscoveredJob | None:
    vacancy_id = str(job.get("id") or "")
    employer = job.get("employer")
    actual_employer_id = str(employer.get("id") or "") if isinstance(employer, dict) else ""
    if not vacancy_id or actual_employer_id != employer_id:
        return None

    url = _clean_text(job.get("alternate_url")) or f"https://hh.ru/vacancy/{vacancy_id}"

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

    employment = job.get("employment")
    employment_type = None
    if isinstance(employment, dict):
        employment_type = _clean_text(employment.get("id")) or _clean_text(employment.get("name"))

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
) -> tuple[list[dict], bool]:
    summaries: list[dict] = []
    page = 0
    pages = 1
    truncated = False

    while page < pages:
        response = await client.get(
            _listing_url(),
            params={
                "employer_id": employer_id,
                "page": page,
                "per_page": PAGE_SIZE,
            },
            headers=REQUEST_HEADERS,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("HeadHunter vacancy response has no items array")
        summaries.extend(item for item in items if isinstance(item, dict))

        try:
            pages = max(1, int(payload.get("pages") or 1))
        except (TypeError, ValueError):
            pages = 1
        page += 1
        if len(summaries) >= MAX_JOBS and page < pages:
            truncated = True
            break

    return summaries[:MAX_JOBS], truncated


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return rich vacancy summaries for one HeadHunter employer."""
    _ = pw
    metadata = board.get("metadata") or {}
    employer_id = str(
        metadata.get("employer_id") or _employer_id_from_url(board["board_url"]) or ""
    )
    if not employer_id.isdigit():
        raise ValueError(
            "HeadHunter monitor requires numeric employer_id or an employer board URL; "
            f"got {board['board_url']!r}"
        )

    summaries, truncated = await _fetch_summaries(client, employer_id)
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for summary in summaries:
        parsed = _parse_job(summary, employer_id=employer_id)
        if parsed and parsed.url not in seen:
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
    if not employer_id:
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
            params={"employer_id": employer_id, "page": 0, "per_page": 1},
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
    if not employer_id:
        return
    await save_json_response(
        artifact_dir,
        client,
        _listing_url(),
        params={"employer_id": employer_id, "page": 0, "per_page": PAGE_SIZE},
        headers=REQUEST_HEADERS,
        filename="headhunter-listing.json",
    )


register("headhunter", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
