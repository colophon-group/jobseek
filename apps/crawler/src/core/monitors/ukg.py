"""UKG Pro public recruiting API monitor.

UKG boards expose stable opportunity IDs and useful summary fields through a
same-origin JSON search endpoint. The detail page embeds the full opportunity
as JSON, so Jobseek's generic embedded scraper enriches descriptions without a
UKG-specific scraper implementation.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import httpx
import structlog

from src.core.enum_normalize import normalize_employment_type
from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.shared.html_normalize import normalize_description_html
from src.shared.http_retry import PaginationFetchError, fetch_json_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.ukg import UKGBoard, normalize_ukg_uuid, ukg_board_from_metadata, ukg_board_from_url

log = structlog.get_logger()

PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
_GONE_STATUSES = frozenset({404, 410})
_TRANSIENT_STATUSES = frozenset({202, 401, 403})
# UKG's first-party ``Content/locales/en-US/translation.json`` defines these
# numeric values as Hybrid, On-site, and Remote respectively.
_LOCATION_TYPES = {0: "hybrid", 1: "onsite", 2: "remote"}
_PAGE_PATTERNS = [
    re.compile(
        r"(https://recruiting(?:[2-9])?\.ultipro\.com/"
        r"[A-Za-z0-9]{3,64}/JobBoard/[0-9a-f-]{36})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(https://recruiting\.ultipro\.ca/"
        r"[A-Za-z0-9]{3,64}/JobBoard/[0-9a-f-]{36})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.rec\.pro\.ukg\.net/"
        r"[A-Za-z0-9]{3,64}/JobBoard/[0-9a-f-]{36})",
        re.IGNORECASE,
    ),
]


def _board_key(board: dict) -> UKGBoard:
    metadata = board.get("metadata") or {}
    resolved = ukg_board_from_metadata(metadata) or ukg_board_from_url(board["board_url"])
    if resolved is None:
        raise ValueError(
            f"Cannot derive a UKG board from {board['board_url']!r}; configure "
            "metadata.host, metadata.tenant, and metadata.board_id"
        )
    return resolved


def _search_payload(*, skip: int, take: int = PAGE_SIZE) -> dict:
    return {
        "opportunitySearch": {
            "Top": take,
            "Skip": skip,
            "QueryString": "",
            "Filters": [],
        }
    }


async def _fetch_page(
    board: UKGBoard,
    client: httpx.AsyncClient,
    *,
    skip: int,
    take: int = PAGE_SIZE,
) -> dict:
    return await fetch_json_page_with_retry(
        client,
        board.search_url(),
        method="POST",
        json_body=_search_payload(skip=skip, take=take),
        headers={"content-type": "application/json"},
        expect_shape=dict,
        follow_redirects=False,
        retryable_statuses=_TRANSIENT_STATUSES,
        retries=3,
        base_delay=0.5,
        log_event="ukg.search_backoff",
    )


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(html.unescape(value).split())
    return cleaned or None


def _parse_date(value: object) -> str | None:
    raw = _clean_string(value)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _location_name(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    address = raw.get("Address")
    if not isinstance(address, dict):
        address = {}
    parts: list[str] = []
    seen: set[str] = set()
    for value in (
        address.get("City"),
        address.get("State"),
        address.get("Country"),
    ):
        if isinstance(value, dict):
            value = value.get("Name") or value.get("Code")
        part = _clean_string(value)
        if part and part.casefold() not in seen:
            key = part.casefold()
            seen.add(key)
            parts.append(part)
    if parts:
        return ", ".join(parts)
    return _clean_string(raw.get("LocalizedName")) or _clean_string(raw.get("LocalizedDescription"))


def _parse_locations(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    locations: list[str] = []
    seen: set[str] = set()
    for raw in value:
        location = _location_name(raw)
        if location and location.casefold() not in seen:
            key = location.casefold()
            seen.add(key)
            locations.append(location)
    return locations or None


def _parse_job(raw: object, board: UKGBoard) -> DiscoveredJob | None:
    if not isinstance(raw, dict):
        return None
    opportunity_id = normalize_ukg_uuid(raw.get("Id"))
    title = _clean_string(raw.get("Title"))
    if opportunity_id is None or title is None:
        return None

    full_time = raw.get("FullTime")
    employment_type = (
        normalize_employment_type("full-time" if full_time else "part-time")
        if isinstance(full_time, bool)
        else None
    )
    raw_location_type = raw.get("JobLocationType")
    location_type = (
        _LOCATION_TYPES.get(raw_location_type)
        if isinstance(raw_location_type, int) and not isinstance(raw_location_type, bool)
        else None
    )
    metadata: dict[str, object] = {"opportunity_id": opportunity_id}
    for source, target in (
        ("RequisitionNumber", "requisition_number"),
        ("JobCategoryName", "category"),
        ("OpportunityType", "opportunity_type"),
    ):
        value = raw.get(source)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and value != "":
            metadata[target] = value

    description = raw.get("BriefDescription")
    return DiscoveredJob(
        url=board.job_url(opportunity_id),
        title=title,
        description=normalize_description_html(
            description if isinstance(description, str) else None
        ),
        locations=_parse_locations(raw.get("Locations")),
        employment_type=employment_type,
        job_location_type=location_type,
        date_posted=_parse_date(raw.get("PostedDate")),
        metadata=metadata,
    )


def _page_rows(payload: dict, board: UKGBoard) -> tuple[int, list[object]]:
    total = payload.get("totalCount")
    rows = payload.get("opportunities")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ValueError(f"UKG board {board.board_id!r} omitted a valid totalCount")
    if not isinstance(rows, list):
        raise ValueError(f"UKG board {board.board_id!r} omitted its opportunities list")
    return total, rows


def _rich_result(
    jobs: list[DiscoveredJob],
    *,
    truncated: bool = False,
    metadata_updates: dict | None = None,
):
    from src.core.monitor import MonitorResult

    return MonitorResult(
        urls={job.url for job in jobs},
        jobs_by_url={job.url: job for job in jobs},
        truncated=truncated,
        metadata_updates=metadata_updates,
    )


async def stream(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> AsyncIterator:
    """Stream authoritative UKG inventory pages with safe failure semantics."""
    _ = pw
    key = _board_key(board)
    expected_total: int | None = None
    count_changed = False
    raw_seen = 0
    invalid = 0
    duplicates = 0
    seen_urls: set[str] = set()

    for page_number in range(1, MAX_PAGES + 1):
        try:
            payload = await _fetch_page(key, client, skip=raw_seen)
        except PaginationFetchError as exc:
            if page_number == 1 and exc.last_status in _GONE_STATUSES:
                raise BoardGoneError(
                    "UKG recruiting board no longer exists",
                    url=key.listing_url(),
                    status_code=exc.last_status,
                ) from exc
            raise
        total, rows = _page_rows(payload, key)
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            log.warning(
                "ukg.count_changed",
                board_id=key.board_id,
                previous=expected_total,
                current=total,
            )
            expected_total = total
            count_changed = True

        if len(rows) > PAGE_SIZE or raw_seen + len(rows) > total:
            raise ValueError(f"UKG board {key.board_id!r} returned an inconsistent search page")
        if not rows and raw_seen < total:
            retry_payload = await _fetch_page(key, client, skip=raw_seen)
            retry_total, rows = _page_rows(retry_payload, key)
            if retry_total != expected_total:
                expected_total = retry_total
                count_changed = True
            total = retry_total
            if not rows and raw_seen < total:
                raise PaginationFetchError(
                    key.search_url(),
                    attempts=2,
                    last_status=200,
                    last_error="PrematureEmptyUKGPage",
                )

        if rows and raw_seen + len(rows) < total and len(rows) < PAGE_SIZE:
            retry_payload = await _fetch_page(key, client, skip=raw_seen)
            retry_total, retry_rows = _page_rows(retry_payload, key)
            if retry_total != expected_total:
                expected_total = retry_total
                count_changed = True
            total = retry_total
            if len(retry_rows) > PAGE_SIZE or raw_seen + len(retry_rows) > total:
                raise ValueError(f"UKG board {key.board_id!r} returned an inconsistent retry page")
            if raw_seen + len(retry_rows) < total and len(retry_rows) < PAGE_SIZE:
                raise PaginationFetchError(
                    key.search_url(),
                    attempts=2,
                    last_status=200,
                    last_error="PrematurePartialUKGPage",
                )
            rows = retry_rows

        page_jobs: list[DiscoveredJob] = []
        for raw in rows:
            raw_seen += 1
            job = _parse_job(raw, key)
            if job is None:
                invalid += 1
                continue
            if job.url in seen_urls:
                duplicates += 1
                continue
            seen_urls.add(job.url)
            page_jobs.append(job)

        done = raw_seen >= total or raw_seen >= MAX_JOBS
        truncated = done and (
            count_changed or invalid > 0 or duplicates > 0 or raw_seen != total or total > MAX_JOBS
        )
        metadata_updates = (
            {
                "host": key.host,
                "tenant": key.tenant,
                "board_id": key.board_id,
                "listing_url": key.listing_url(),
            }
            if page_number == 1
            else None
        )
        if page_jobs or done:
            yield _rich_result(
                page_jobs,
                truncated=truncated,
                metadata_updates=metadata_updates,
            )
        if done:
            if expected_total and not seen_urls:
                raise ValueError(f"UKG board {key.board_id!r} returned no valid opportunities")
            log_method = log.warning if truncated else log.info
            log_method(
                "ukg.discovered",
                host=key.host,
                tenant=key.tenant,
                board_id=key.board_id,
                jobs=len(seen_urls),
                raw_seen=raw_seen,
                expected_total=expected_total,
                invalid=invalid,
                duplicates=duplicates,
                truncated=truncated,
            )
            return
    else:
        yield _rich_result([], truncated=True)


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    jobs: list[DiscoveredJob] = []
    truncated = False
    metadata_updates: dict = {}
    async for result in stream(board, client, pw=pw):
        if result.jobs_by_url:
            jobs.extend(result.jobs_by_url.values())
        truncated = truncated or result.truncated
        if result.metadata_updates:
            metadata_updates.update(result.metadata_updates)
    if not truncated:
        return jobs
    return _rich_result(jobs, truncated=True, metadata_updates=metadata_updates or None)


async def _probe_listing(listing_url: str, client: httpx.AsyncClient) -> ProbeResult:
    board = ukg_board_from_url(listing_url)
    if board is None:
        return False, None
    try:
        payload = await _fetch_page(board, client, skip=0, take=1)
        total, rows = _page_rows(payload, board)
        if len(rows) > 1 or (total == 0 and rows):
            return False, None
    except TDMReservedError:
        raise
    except Exception:
        log.debug("ukg.probe_failed", listing_url=listing_url, exc_info=True)
        return False, None
    return True, total


async def _fetch_job_count(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_listing(token, client)
    return count if found else None


async def _probe_candidate(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_listing(token, client)


def _listing_token_from_url(url: str) -> str | None:
    board = ukg_board_from_url(url)
    return board.listing_url() if board is not None else None


def _build_result(listing_url: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    board = ukg_board_from_url(listing_url)
    if board is None:
        return {}
    result: dict = {
        "host": board.host,
        "tenant": board.tenant,
        "board_id": board.board_id,
        "listing_url": board.listing_url(),
    }
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked UKG boards without slug guesses."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="ukg",
        token_from_url=_listing_token_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=frozenset(),
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_candidate,
        initial_context=None,
        result_builder=_build_result,
        page_token_probe=_probe_candidate,
        require_direct_count=True,
        allow_slug_guess=False,
        log_token_field="listing_url",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    board = ukg_board_from_metadata(metadata) or ukg_board_from_url(board_url)
    if board is None:
        return
    payload = await _fetch_page(board, client, skip=0)
    import json

    (artifact_dir / "ukg-search.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


register(
    "ukg",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    stream=stream,
    save_raw=save_raw,
)
