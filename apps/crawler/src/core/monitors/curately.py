"""Curately public career-portal monitor.

Curately career portals resolve a public client id from the tenant short name,
then POST an unfiltered search body to ``sovrenjobsearch``.  Contrary to the
generic API probe's conservative field mapping, every list row already carries
the complete HTML description, structured location, work arrangement, posting
date, and optional pay range.  A provider monitor can therefore avoid both a
browser replay and per-job detail requests.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import structlog

from src.core.enum_normalize import normalize_salary_unit
from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.shared.http_retry import PaginationFetchError, fetch_json_page_with_retry
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

API_BASE = "https://api.curately.ai/QADemoCurately"
SEARCH_URL = f"{API_BASE}/sovrenjobsearch"
MAX_JOBS = 50_000
DEFAULT_DAYS_BACK = 180
_SNAPSHOT_ATTEMPTS = 2
_SNAPSHOT_RETRY_DELAY = 1.0

_SHORT_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BOARD_PATH_RE = re.compile(
    r"^/jobs/([a-z0-9]+(?:-[a-z0-9]+)*)(?:/.*)?$",
    re.IGNORECASE,
)

_JOB_TYPE_MAP = {
    1: "full_time",
    2: "contract",
    3: "contract",
    4: "contract",
}
_JOB_HOURS_MAP = {1: "full_time", 2: "part_time"}
_WORK_TYPE_MAP = {1: "remote", 2: "hybrid", 3: "onsite"}
_ACTIVE_STATUSES = {None, 1, "1"}
_INACTIVE_STATUSES = {0, "0", 2, "2", 3, "3", 4, "4", 5, "5"}


class _SnapshotChanged(ValueError):
    """The Curately inventory changed while its pages were being collected."""


def _short_name_from_url(board_url: str) -> str | None:
    try:
        parsed = urlsplit(board_url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "careers.curately.ai"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None
    match = _BOARD_PATH_RE.fullmatch(parsed.path.rstrip("/"))
    return match.group(1).lower() if match else None


def _client_url(short_name: str) -> str:
    return f"{API_BASE}/getByShortName/{short_name}"


def _job_url(short_name: str, job_id: int) -> str:
    # Curately's React route requires a final title segment but resolves the
    # posting exclusively by jobId.  Keep that segment constant so title or
    # location edits do not create a second source URL for the same job.
    return f"https://careers.curately.ai/jobs/{short_name}/apply-job/{job_id}/job"


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _locations(raw: dict) -> list[str] | None:
    parts = [
        value.strip()
        for key in ("workCity", "workState", "workZipcode")
        if isinstance((value := raw.get(key)), str) and value.strip()
    ]
    if parts:
        return [", ".join(parts)]
    return ["Remote"] if raw.get("workType") == 1 else None


def _employment_type(raw: dict) -> str | None:
    job_type = raw.get("jobType")
    if isinstance(job_type, int) and job_type in _JOB_TYPE_MAP:
        if job_type == 1 and raw.get("jobHours") == 2:
            return "part_time"
        return _JOB_TYPE_MAP[job_type]
    job_hours = raw.get("jobHours")
    return _JOB_HOURS_MAP.get(job_hours) if isinstance(job_hours, int) else None


def _salary(raw: dict, *, currency: str | None, unit: str | None) -> dict | None:
    minimum = _number(raw.get("payrateMin"))
    maximum = _number(raw.get("payrateMax"))
    normalized_unit = normalize_salary_unit(unit)
    if (minimum is None and maximum is None) or not currency or not normalized_unit:
        return None
    return {
        "currency": currency.upper(),
        "min": minimum,
        "max": maximum,
        "unit": normalized_unit,
    }


def _parse_job(
    raw: dict,
    *,
    short_name: str,
    currency: str | None = None,
    salary_unit: str | None = None,
    language: str | None = None,
) -> DiscoveredJob | None:
    status = raw.get("status")
    if status in _INACTIVE_STATUSES:
        return None
    if status not in _ACTIVE_STATUSES:
        raise ValueError(f"Curately job has an unknown status value {status!r}")

    job_id = _positive_int(raw.get("jobId"))
    title = raw.get("jobTitle")
    if job_id is None or not isinstance(title, str) or not title.strip():
        raise ValueError("Curately job is missing a valid jobId or jobTitle")

    description = raw.get("publicJobDescr")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"Curately job {job_id} is missing a non-empty description")

    locations = _locations(raw)
    if not locations:
        raise ValueError(f"Curately job {job_id} is missing a location")

    metadata: dict = {"id": job_id}
    for source, target in (
        ("clientName", "client_name"),
        ("estStartDate", "estimated_start_date"),
        ("estEndDate", "estimated_end_date"),
        ("jobHours", "job_hours_code"),
        ("jobType", "job_type_code"),
        ("workType", "work_type_code"),
    ):
        if raw.get(source) is not None:
            metadata[target] = raw[source]

    work_type = raw.get("workType")
    return DiscoveredJob(
        url=_job_url(short_name, job_id),
        title=title.strip(),
        description=description,
        locations=locations,
        employment_type=_employment_type(raw),
        job_location_type=_WORK_TYPE_MAP.get(work_type) if isinstance(work_type, int) else None,
        date_posted=raw.get("createDate") if isinstance(raw.get("createDate"), str) else None,
        base_salary=_salary(raw, currency=currency, unit=salary_unit),
        language=language,
        metadata=metadata,
    )


def _search_body(client_id: int, *, offset: int, days_back: int) -> dict:
    return {
        "query": "",
        "city": "",
        "state": "",
        "zipcode": "",
        "radius": "30",
        "daysback": str(days_back),
        "jobHours": "",
        "isRemote": False,
        "jobType": "",
        "clientids": str(client_id),
        "next": offset,
        "type": "",
    }


async def _fetch_client(short_name: str, client: httpx.AsyncClient) -> dict:
    try:
        data = await fetch_json_page_with_retry(
            client,
            _client_url(short_name),
            expect_shape=dict,
            retries=3,
            log_event="curately.client_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in {404, 410}:
            raise BoardGoneError(
                f"Curately tenant {short_name!r} no longer exists",
                url=_client_url(short_name),
                status_code=exc.last_status,
            ) from exc
        raise

    if data.get("Success") is not True or data.get("Status") != 200:
        raise ValueError(f"Curately tenant response for {short_name!r} was not successful")
    response_short_name = data.get("shortName")
    client_id = _positive_int(data.get("clientId"))
    if (
        not isinstance(response_short_name, str)
        or response_short_name.lower() != short_name
        or client_id is None
    ):
        raise ValueError(f"Curately tenant response for {short_name!r} has invalid identity")
    return data


async def _fetch_search_page(
    client: httpx.AsyncClient,
    *,
    client_id: int,
    offset: int,
    days_back: int,
) -> dict:
    data = await fetch_json_page_with_retry(
        client,
        SEARCH_URL,
        method="POST",
        json_body=_search_body(client_id, offset=offset, days_back=days_back),
        expect_shape=dict,
        retries=3,
        log_event="curately.search_backoff",
    )
    if data.get("Success") is not True or data.get("Status") != 200:
        raise ValueError("Curately search response was not successful")
    items = data.get("List")
    total = data.get("TotalSize")
    if (
        not isinstance(items, list)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
    ):
        raise ValueError("Curately search response has invalid List or TotalSize")
    return data


def _board_config(board: dict) -> tuple[str, int | None, int, str | None, str | None, str | None]:
    metadata = board.get("metadata") or {}
    url_short_name = _short_name_from_url(board["board_url"])
    if url_short_name is None:
        raise ValueError(f"Cannot derive Curately short_name from {board['board_url']!r}")
    configured_short_name = metadata.get("short_name")
    if configured_short_name is not None and configured_short_name != url_short_name:
        raise ValueError(
            "Curately short_name does not match the careers.curately.ai board URL tenant"
        )
    short_name = configured_short_name or url_short_name
    if not isinstance(short_name, str) or not _SHORT_NAME_RE.fullmatch(short_name):
        raise ValueError("Curately short_name must be a lowercase tenant slug")

    client_id = metadata.get("client_id")
    if client_id is not None:
        client_id = _positive_int(client_id)
        if client_id is None:
            raise ValueError("Curately client_id must be a positive integer")

    days_back = metadata.get("days_back", DEFAULT_DAYS_BACK)
    if isinstance(days_back, bool) or not isinstance(days_back, int) or not 1 <= days_back <= 3650:
        raise ValueError("Curately days_back must be an integer from 1 to 3650")

    currency = metadata.get("currency")
    salary_unit = metadata.get("salary_unit")
    language = metadata.get("language")
    for name, value in (
        ("currency", currency),
        ("salary_unit", salary_unit),
        ("language", language),
    ):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Curately {name} must be a string")
    return short_name, client_id, days_back, currency, salary_unit, language


async def _discover_snapshot(
    client: httpx.AsyncClient,
    *,
    short_name: str,
    client_id: int,
    days_back: int,
    currency: str | None,
    salary_unit: str | None,
    language: str | None,
):
    jobs: list[DiscoveredJob] = []
    seen_ids: set[int] = set()
    offset = 0
    expected_total: int | None = None

    while True:
        data = await _fetch_search_page(
            client,
            client_id=client_id,
            offset=offset,
            days_back=days_back,
        )
        items = data["List"]
        total = data["TotalSize"]
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise _SnapshotChanged(
                f"Curately TotalSize changed during pagination: {expected_total} -> {total}"
            )

        if not items:
            if offset < min(total, MAX_JOBS):
                raise _SnapshotChanged(
                    f"Curately pagination ended at offset {offset} before advertised total {total}"
                )
            break

        for raw in items:
            if not isinstance(raw, dict):
                raise ValueError("Curately List contains a non-object job")
            job_id = _positive_int(raw.get("jobId"))
            if job_id is None:
                raise ValueError("Curately List contains a job without a valid jobId")
            if _positive_int(raw.get("clientId")) != client_id:
                raise ValueError(
                    f"Curately job {job_id} does not belong to configured client_id {client_id}"
                )
            if job_id in seen_ids:
                raise _SnapshotChanged(f"Curately pagination repeated jobId {job_id}")
            seen_ids.add(job_id)
            job = _parse_job(
                raw,
                short_name=short_name,
                currency=currency,
                salary_unit=salary_unit,
                language=language,
            )
            if job is not None:
                jobs.append(job)

        offset += len(items)
        if offset >= min(total, MAX_JOBS):
            break

    if expected_total is None:
        raise ValueError("Curately search returned no pagination metadata")
    if expected_total <= MAX_JOBS and offset != expected_total:
        raise _SnapshotChanged(
            f"Curately pagination returned {offset} rows for advertised total {expected_total}"
        )
    if expected_total > MAX_JOBS:
        log.warning(
            "curately.truncated",
            short_name=short_name,
            total=expected_total,
            cap=MAX_JOBS,
        )
        return truncated_rich_result(jobs[:MAX_JOBS])
    return jobs


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    _ = pw
    short_name, client_id, days_back, currency, salary_unit, language = _board_config(board)
    if client_id is None:
        client_data = await _fetch_client(short_name, client)
        client_id = _positive_int(client_data.get("clientId"))
        assert client_id is not None

    for attempt in range(1, _SNAPSHOT_ATTEMPTS + 1):
        try:
            return await _discover_snapshot(
                client,
                short_name=short_name,
                client_id=client_id,
                days_back=days_back,
                currency=currency,
                salary_unit=salary_unit,
                language=language,
            )
        except _SnapshotChanged as exc:
            if attempt == _SNAPSHOT_ATTEMPTS:
                raise
            log.warning(
                "curately.snapshot_changed",
                short_name=short_name,
                attempt=attempt,
                error=str(exc),
            )
            await asyncio.sleep(_SNAPSHOT_RETRY_DELAY)
    raise AssertionError("unreachable")


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    _ = pw
    short_name = _short_name_from_url(url)
    if short_name is None:
        return None
    if client is None:
        return {"short_name": short_name, "days_back": DEFAULT_DAYS_BACK}
    try:
        client_data = await _fetch_client(short_name, client)
        client_id = _positive_int(client_data.get("clientId"))
        assert client_id is not None
        data = await _fetch_search_page(
            client,
            client_id=client_id,
            offset=0,
            days_back=DEFAULT_DAYS_BACK,
        )
    except Exception:
        log.debug("curately.probe_failed", short_name=short_name, exc_info=True)
        return None
    return {
        "short_name": short_name,
        "client_id": client_id,
        "days_back": DEFAULT_DAYS_BACK,
        "jobs": data["TotalSize"],
    }


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    board = {"board_url": board_url, "metadata": metadata}
    short_name, client_id, days_back, _currency, _salary_unit, _language = _board_config(board)
    if client_id is None:
        client_data = await _fetch_client(short_name, client)
        client_id = _positive_int(client_data.get("clientId"))
        assert client_id is not None
    data = await _fetch_search_page(
        client,
        client_id=client_id,
        offset=0,
        days_back=days_back,
    )
    (artifact_dir / "response.json").write_text(
        json.dumps(data, indent=2, default=str),
        encoding="utf-8",
    )


register("curately", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
