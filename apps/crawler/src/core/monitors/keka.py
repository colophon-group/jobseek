"""Keka public career-portal monitor.

Keka listing pages expose a stable organization identifier in their static
bootstrap. The same-origin embed endpoint then returns every active job as a
rich JSON record, so no browser or per-job scraper is needed.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, fetch_page_text, register
from src.core.monitors.dom import _raise_if_bot_challenge
from src.core.monitors.raw import save_json_response
from src.shared.html_normalize import normalize_description_html
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.keka import (
    KekaBoard,
    extract_keka_identifier,
    is_keka_forbidden_redirect,
    keka_board_from_metadata,
    keka_board_from_url,
)
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

MAX_HTML_CHARS = 500_000
MAX_JSON_CHARS = 25_000_000
MAX_JOBS = 50_000
_GONE_STATUSES = frozenset({404, 410})
_TRANSIENT_STATUSES = frozenset({202, 401, 403})
_PAGE_PATTERNS = [
    re.compile(
        r"(https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.keka\.com/careers"
        r"(?:/[a-z0-9](?:[a-z0-9-]{0,62})?)?"
        r"(?:/jobdetails/[1-9]\d{0,18})?/?(?:\?source=[^#\"'<\s]+)?)"
        r"(?=[#\"'<\s]|$)",
        re.IGNORECASE,
    )
]


def _configured_board(metadata: object) -> KekaBoard | None:
    if not isinstance(metadata, dict):
        if metadata:
            raise ValueError("Keka monitor metadata must be an object")
        return None
    configured = keka_board_from_metadata(metadata)
    if configured is None and any(key in metadata for key in ("tenant", "portal", "identifier")):
        raise ValueError("Keka monitor metadata contains an invalid portal identity")
    return configured


def _board_key(board: dict) -> KekaBoard:
    configured = _configured_board(board.get("metadata") or {})
    direct = keka_board_from_url(board["board_url"])
    if (
        configured is not None
        and direct is not None
        and (configured.tenant != direct.tenant or configured.portal != direct.portal)
    ):
        raise ValueError("Configured Keka portal does not match the board URL")
    resolved = configured or direct
    if resolved is None:
        raise ValueError(
            f"Cannot derive Keka tenant/portal from board URL {board['board_url']!r} or metadata"
        )
    return resolved


async def _bootstrap(board: KekaBoard, client: httpx.AsyncClient) -> tuple[str, KekaBoard]:
    url = board.listing_url()
    try:
        page = await fetch_text_page_with_retry(
            client,
            url,
            require_nonempty=True,
            max_chars=MAX_HTML_CHARS + 1,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            retryable_statuses=_TRANSIENT_STATUSES,
            log_event="keka.bootstrap_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES or (
            exc.last_status in {301, 302, 307, 308}
            and is_keka_forbidden_redirect(board, exc.last_location)
        ):
            raise BoardGoneError(
                "Keka career portal is unavailable",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    if page is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"Keka listing fetch returned no page for {url!r}")
    _raise_if_bot_challenge(url, page)
    if len(page) > MAX_HTML_CHARS:
        raise ValueError(f"Keka tenant {board.tenant!r} bootstrap exceeded the HTML safety cap")
    identifier = extract_keka_identifier(page)
    if identifier is None:
        raise ValueError(f"Keka tenant {board.tenant!r} omitted its career-portal identity")
    if board.identifier is not None and board.identifier != identifier:
        raise ValueError(f"Keka tenant {board.tenant!r} live portal identity changed")
    return page, board.with_identifier(identifier)


async def _active_jobs(board: KekaBoard, client: httpx.AsyncClient) -> list[object]:
    url = board.jobs_url()
    page = await fetch_text_page_with_retry(
        client,
        url,
        headers={"accept": "application/json"},
        require_nonempty=True,
        max_chars=MAX_JSON_CHARS + 1,
        follow_redirects=False,
        end_of_pagination_statuses=(),
        retryable_statuses=_TRANSIENT_STATUSES,
        log_event="keka.jobs_backoff",
    )
    if page is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"Keka jobs fetch returned no page for {url!r}")
    if len(page) > MAX_JSON_CHARS:
        raise ValueError(f"Keka tenant {board.tenant!r} jobs payload exceeded the safety cap")
    try:
        payload = json.loads(page)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Keka tenant {board.tenant!r} returned invalid jobs JSON") from exc
    if not isinstance(payload, list):
        raise ValueError(f"Keka tenant {board.tenant!r} returned a non-list jobs payload")
    if len(payload) > MAX_JOBS:
        raise ValueError(f"Keka tenant {board.tenant!r} exceeded the {MAX_JOBS:,}-job safety cap")
    return payload


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _date(value: object) -> str | None:
    cleaned = _clean_string(value)
    if cleaned is None:
        return None
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _locations(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    locations: list[str] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        components: list[str] = []
        seen_components: set[str] = set()
        for key in ("city", "state", "countryName"):
            component = _clean_string(raw.get(key))
            identity = component.casefold() if component is not None else None
            if component is not None and identity not in seen_components:
                components.append(component)
                seen_components.add(identity)
        # ``name`` is usually an internal office label ("Head Office",
        # "Bengaluru Center"). Prefer resolvable geography and retain the
        # label only for records that do not expose structured components.
        label = ", ".join(components) if components else _clean_string(raw.get("name"))
        if label and label not in locations:
            locations.append(label)
    return locations or None


def _salary(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    raw_min = value.get("minimum")
    raw_max = value.get("maximum")
    minimum = (
        raw_min if isinstance(raw_min, (int, float)) and not isinstance(raw_min, bool) else None
    )
    maximum = (
        raw_max if isinstance(raw_max, (int, float)) and not isinstance(raw_max, bool) else None
    )
    minimum = minimum if minimum and minimum > 0 else None
    maximum = maximum if maximum and maximum > 0 else None
    if minimum is None and maximum is None:
        return None
    raw_period = value.get("salaryPeriod")
    unit = (
        {1: "hour", 3: "month", 4: "year"}.get(raw_period)
        if isinstance(raw_period, int) and not isinstance(raw_period, bool)
        else None
    )
    return {
        "currency": _clean_string(value.get("currency")),
        "min": minimum,
        "max": maximum,
        "unit": unit,
    }


def _parse_job(raw: object, board: KekaBoard) -> DiscoveredJob:
    if not isinstance(raw, dict):
        raise ValueError(f"Keka tenant {board.tenant!r} returned a non-object job record")
    job_id = raw.get("id")
    title = _clean_string(raw.get("title"))
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1 or title is None:
        raise ValueError(f"Keka tenant {board.tenant!r} returned a job without valid identity")

    metadata: dict[str, object] = {"id": job_id}
    for source, target in (
        ("departmentIdentifier", "department_id"),
        ("departmentName", "department"),
        ("experience", "experience"),
        ("jobNumber", "job_number"),
    ):
        value = _clean_string(raw.get(source))
        if value is not None:
            metadata[target] = value
    extras: dict[str, object] | None = None
    skills = raw.get("skillNames")
    if isinstance(skills, list):
        cleaned_skills = [value for item in skills if (value := _clean_string(item)) is not None]
        if cleaned_skills:
            extras = {"skills": list(dict.fromkeys(cleaned_skills))}

    job_type = raw.get("jobType")
    employment_type = "Full Time" if job_type == 2 else "Part Time" if job_type == 1 else None
    if isinstance(job_type, int) and not isinstance(job_type, bool):
        metadata["job_type"] = job_type

    return DiscoveredJob(
        url=board.job_url(job_id),
        title=title,
        description=normalize_description_html(
            raw.get("description") if isinstance(raw.get("description"), str) else None
        ),
        locations=_locations(raw.get("jobLocations")),
        employment_type=employment_type,
        date_posted=_date(raw.get("publishedOn")),
        base_salary=_salary(raw.get("salaryRange")),
        extras=extras,
        metadata=metadata,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Return every active Keka job as an authoritative rich record."""
    _ = pw
    configured = _board_key(board)
    _page, resolved = await _bootstrap(configured, client)
    payload = await _active_jobs(resolved, client)
    jobs: list[DiscoveredJob] = []
    seen_urls: set[str] = set()
    for raw in payload:
        job = _parse_job(raw, resolved)
        if job.url in seen_urls:
            raise ValueError(f"Keka tenant {resolved.tenant!r} returned duplicate job IDs")
        seen_urls.add(job.url)
        jobs.append(job)
    log.info(
        "keka.discovered",
        tenant=resolved.tenant,
        portal=resolved.portal,
        jobs=len(jobs),
    )
    return jobs


async def _probe_listing(url: str, client: httpx.AsyncClient) -> dict | None:
    board = keka_board_from_url(url)
    if board is None:
        return None
    try:
        _page, resolved = await _bootstrap(board, client)
        jobs = await _active_jobs(resolved, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("keka.probe_failed", url=url, exc_info=True)
        return None
    return {
        "tenant": resolved.tenant,
        "portal": resolved.portal,
        "identifier": resolved.identifier,
        "jobs": len(jobs),
    }


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked Keka career portals."""
    _ = pw
    direct = keka_board_from_url(url)
    if direct is not None:
        if client is not None:
            return await _probe_listing(direct.listing_url(), client)
        return {"tenant": direct.tenant, "portal": direct.portal}
    if client is None:
        return None
    page = await fetch_page_text(url, client)
    if not page:
        return None
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(page)
        if match is None:
            continue
        result = await _probe_listing(match.group(1), client)
        if result is not None:
            log.info("keka.detected_in_page", url=url, tenant=result["tenant"])
            return result
    return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    configured = _board_key({"board_url": board_url, "metadata": metadata})
    page, resolved = await _bootstrap(configured, client)
    (artifact_dir / "keka-listing.html").write_text(page, encoding="utf-8")
    await save_json_response(
        artifact_dir,
        client,
        resolved.jobs_url(),
        filename="keka-jobs.json",
        headers={"accept": "application/json"},
        follow_redirects=False,
    )


register(
    "keka",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    save_raw=save_raw,
)
