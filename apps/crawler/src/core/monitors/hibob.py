"""HiBob public career-site monitor.

HiBob-hosted boards expose their complete listing payload at ``/api/job-ad``.
The endpoint is public but expects the career-site origin as its referrer.  A
single response contains every open job, including the detail sections used by
the Angular board, so this monitor returns rich jobs without a scraper.
"""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type, normalize_salary_unit
from src.core.monitors import DiscoveredJob, register
from src.core.monitors.raw import save_json_response
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

MAX_JOBS = 50_000
_HOST_SUFFIX = ".careers.hibob.com"


def _origin_from_url(url: str) -> str | None:
    """Return the normalized origin for a HiBob-hosted career-site URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host.endswith(_HOST_SUFFIX):
        return None
    return f"{parsed.scheme}://{host}"


def _api_url(origin: str) -> str:
    return f"{origin}/api/job-ad"


def _headers(origin: str) -> dict[str, str]:
    # HiBob returns 401 for context-free requests, while its public career
    # application sends the board origin as the referrer.
    return {
        "Accept": "application/json",
        "Referer": f"{origin}/",
    }


def _build_description(raw: dict) -> tuple[str | None, dict | None]:
    """Combine HiBob's job-detail sections and retain structured extras."""
    parts: list[str] = []
    extras: dict[str, str] = {}

    description = raw.get("description")
    if isinstance(description, str) and description.strip():
        parts.append(description.strip())

    for key, label, extras_key in (
        ("responsibilities", "Responsibilities", "responsibilities"),
        ("requirements", "Requirements", "qualifications"),
        ("benefits", "Benefits", "benefits"),
    ):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        parts.append(f"<h3>{html.escape(label)}</h3>\n{value}")
        extras[extras_key] = value

    return "\n".join(parts) or None, extras or None


def _location(raw: dict) -> list[str] | None:
    """Use the location label displayed by the HiBob career site."""
    site = raw.get("site")
    if isinstance(site, str) and site.strip():
        return [site.strip()]

    country = raw.get("country")
    if isinstance(country, str) and country.strip():
        return [country.strip()]
    return None


def _salary(raw: dict) -> dict | None:
    minimum = raw.get("payTransparencyMinSalary")
    maximum = raw.get("payTransparencyMaxSalary")
    if minimum is None and maximum is None:
        return None

    unit = normalize_salary_unit(raw.get("payTransparencySalaryPayPeriod")) or "year"
    return {
        "currency": raw.get("payTransparencySalaryCurrency"),
        "min": minimum,
        "max": maximum,
        "unit": unit,
    }


def _parse_job(raw: dict, origin: str) -> DiscoveredJob | None:
    job_id = raw.get("id")
    if not isinstance(job_id, str) or not job_id.strip():
        return None

    description, extras = _build_description(raw)
    metadata = {
        key: value
        for key, value in {
            "id": job_id,
            "department": raw.get("department"),
            "department_id": raw.get("departmentId"),
            "site_id": raw.get("siteId"),
            "country": raw.get("country"),
            "employment_type_id": raw.get("employmentTypeId"),
            "workspace_type_id": raw.get("workspaceTypeId"),
        }.items()
        if value is not None and value != ""
    }

    return DiscoveredJob(
        url=f"{origin}/jobs/{job_id.strip()}",
        title=raw.get("title"),
        description=description,
        locations=_location(raw),
        employment_type=raw.get("employmentType") or raw.get("employmentTypeId"),
        job_location_type=normalize_job_location_type(
            raw.get("workspaceType") or raw.get("workspaceTypeId"),
            default=None,
        ),
        date_posted=raw.get("publishedAt"),
        base_salary=_salary(raw),
        language=raw.get("language"),
        extras=extras,
        metadata=metadata or None,
    )


async def _fetch_payload(origin: str, client: httpx.AsyncClient) -> dict:
    response = await client.get(
        _api_url(origin),
        headers=_headers(origin),
        follow_redirects=True,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("jobAdDetails"), list):
        raise ValueError("HiBob /api/job-ad response has no jobAdDetails list")
    return payload


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob]:
    """Fetch every open posting from a HiBob public career site."""
    _ = pw
    metadata = board.get("metadata") or {}
    origin = metadata.get("origin") or _origin_from_url(board["board_url"])
    if not isinstance(origin, str) or _origin_from_url(origin) is None:
        raise ValueError(
            f"Cannot derive HiBob career-site origin from {board['board_url']!r} "
            "and no valid origin in metadata"
        )
    origin = _origin_from_url(origin)
    assert origin is not None

    payload = await _fetch_payload(origin, client)
    jobs = [
        job
        for raw in payload["jobAdDetails"]
        if isinstance(raw, dict) and (job := _parse_job(raw, origin))
    ]

    if len(jobs) > MAX_JOBS:
        log.warning("hibob.truncated", origin=origin, total=len(jobs), cap=MAX_JOBS)
        return truncated_rich_result(jobs)

    log.info("hibob.discovered", origin=origin, jobs=len(jobs))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect a HiBob hosted board and verify its public listing feed."""
    _ = pw
    origin = _origin_from_url(url)
    if not origin:
        return None
    if client is None:
        return {"origin": origin}

    try:
        payload = await _fetch_payload(origin, client)
    except Exception:
        return None
    return {"origin": origin, "jobs": len(payload["jobAdDetails"])}


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    origin = metadata.get("origin") or _origin_from_url(board_url)
    if not isinstance(origin, str):
        return
    origin = _origin_from_url(origin)
    if not origin:
        return
    await save_json_response(
        artifact_dir,
        client,
        _api_url(origin),
        headers=_headers(origin),
    )


register("hibob", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
