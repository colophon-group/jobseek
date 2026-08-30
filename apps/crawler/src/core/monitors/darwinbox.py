"""Darwinbox public career-site monitor.

Darwinbox's public jobs endpoint is protected by Cloudflare against stateless
HTTP clients. The monitor opens the canonical public portal with Jobseek's
shared Playwright transport and replays the same-origin API in that browser
session. Listing records already contain the available rich job fields, so no
per-job detail fan-out or scraper is needed.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, fetch_page_text, register
from src.core.monitors.dom import _raise_if_bot_challenge
from src.shared.api_sniff import _fetch_page_with_retry, make_browser_fetcher
from src.shared.darwinbox import (
    DarwinboxBoard,
    darwinbox_board_from_metadata,
    darwinbox_board_from_url,
    normalize_darwinbox_job_id,
)
from src.shared.html_normalize import normalize_description_html
from src.shared.http_retry import PaginationFetchError
from src.shared.tdm import check_browser_response
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
_GONE_STATUSES = frozenset({404, 410})
_PAGE_PATTERNS = [
    re.compile(
        r"(https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.darwinbox\.(?:in|com)/"
        r"ms/candidate(?:v2)?/"
        r"(?:[A-Za-z0-9][A-Za-z0-9_-]{0,63}/)?careers"
        r"(?:/jobDetails/[A-Za-z0-9][A-Za-z0-9_-]{0,127})?/?)(?=[?#\"'<\s]|$)",
        re.IGNORECASE,
    )
]
_REQUEST_HEADERS = {
    "accept": "application/json",
    "authorization": "undefined",
    "content-type": "application/json",
    "x-requested-with": "XMLHttpRequest",
}


def _configured_board(metadata: object) -> DarwinboxBoard | None:
    if not isinstance(metadata, dict):
        if metadata:
            raise ValueError("Darwinbox monitor metadata must be an object")
        return None
    configured = darwinbox_board_from_metadata(metadata)
    if configured is None and any(key in metadata for key in ("host", "company_id")):
        raise ValueError("Darwinbox monitor metadata contains an invalid portal identity")
    return configured


def _board_key(board: dict) -> DarwinboxBoard:
    configured = _configured_board(board.get("metadata") or {})
    direct = darwinbox_board_from_url(board["board_url"])
    if configured is not None and direct is not None and configured != direct:
        raise ValueError("Configured Darwinbox portal does not match the board URL")
    resolved = configured or direct
    if resolved is None:
        raise ValueError(
            f"Cannot derive Darwinbox host/company ID from board URL "
            f"{board['board_url']!r} or metadata"
        )
    return resolved


def _request_body(board: DarwinboxBoard, page: int) -> str:
    return json.dumps(
        {
            "companyId": board.company_id,
            "page": page,
            "sort_option": "new",
            "limit": PAGE_SIZE,
        },
        separators=(",", ":"),
    )


async def _fetch_jobs_page(fetch, board: DarwinboxBoard, page: int) -> dict:
    payload = await _fetch_page_with_retry(
        fetch,
        "POST",
        board.jobs_url(),
        _REQUEST_HEADERS,
        _request_body(board, page),
    )
    if not isinstance(payload, dict):
        raise ValueError(f"Darwinbox host {board.host!r} returned a non-object payload")
    return payload


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if isinstance(value, int) and value >= 0 else None


def _page_rows(payload: dict, board: DarwinboxBoard) -> tuple[int, list[object]]:
    status = payload.get("status")
    total = _nonnegative_int(payload.get("job_counts"))
    rows = payload.get("data")
    if not isinstance(status, str) or status.casefold() != "success":
        raise ValueError(f"Darwinbox host {board.host!r} returned a failed jobs response")
    if total is None:
        raise ValueError(f"Darwinbox host {board.host!r} omitted a valid job count")
    if not isinstance(rows, list):
        raise ValueError(f"Darwinbox host {board.host!r} omitted its job records")
    if len(rows) > PAGE_SIZE or len(rows) > total:
        raise ValueError(f"Darwinbox host {board.host!r} returned an inconsistent jobs page")
    return total, rows


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _locations(raw: dict) -> list[str] | None:
    values: object = None
    for field in (
        "tool_tip_locations",
        "officelocations_without_area",
        "officelocations_area",
    ):
        candidate = raw.get(field)
        if isinstance(candidate, list) and candidate:
            values = candidate
            break
    if values is None:
        location = _clean_string(raw.get("locations"))
        values = (
            [] if location is None or location.casefold() == "multiple locations" else [location]
        )

    locations: list[str] = []
    seen: set[str] = set()
    for value in values:
        location = _clean_string(value)
        identity = location.casefold() if location else None
        if location is not None and identity is not None and identity not in seen:
            seen.add(identity)
            locations.append(location)
    return locations or None


def _date_posted(raw: dict) -> str | None:
    value = raw.get("posted_on")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            seconds = value / 1000 if value > 100_000_000_000 else value
            return datetime.fromtimestamp(seconds, UTC).date().isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    created = _clean_string(raw.get("created_on"))
    if created is not None:
        try:
            return datetime.fromisoformat(created.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    return None


def _is_remote(value: object) -> bool:
    return (
        value is True
        or value == 1
        or (isinstance(value, str) and value.strip().casefold() in {"1", "true", "yes"})
    )


def _parse_job(raw: object, board: DarwinboxBoard) -> DiscoveredJob | None:
    if not isinstance(raw, dict):
        return None
    job_id = normalize_darwinbox_job_id(raw.get("id"))
    title = _clean_string(raw.get("title")) or _clean_string(raw.get("designation_display_name"))
    if job_id is None or title is None:
        return None

    metadata: dict[str, object] = {"id": job_id}
    for source, target in (
        ("internal_job_code", "job_code"),
        ("department_name_only", "department"),
        ("experience", "experience"),
        ("salary_range", "salary_range"),
    ):
        value = _clean_string(raw.get(source))
        if value is not None:
            metadata[target] = value

    remote = _is_remote(raw.get("is_remote"))
    description = raw.get("jd") if isinstance(raw.get("jd"), str) else None
    return DiscoveredJob(
        url=board.job_url(job_id),
        title=title,
        description=normalize_description_html(description),
        locations=_locations(raw),
        employment_type=_clean_string(raw.get("emp_type_name")),
        job_location_type="remote" if remote else None,
        date_posted=_date_posted(raw),
        metadata=metadata,
    )


def _rich_result(jobs: list[DiscoveredJob], *, truncated: bool = False):
    from src.core.monitor import MonitorResult

    return MonitorResult(
        urls={job.url for job in jobs},
        jobs_by_url={job.url: job for job in jobs},
        truncated=truncated,
    )


@asynccontextmanager
async def _open_darwinbox_page(
    pw,
    browser_config: dict,
    *,
    use_proxy: bool,
    target_url: str,
):
    from src.shared.browser import open_page

    async with open_page(
        pw,
        browser_config,
        use_proxy=use_proxy,
        target_url=target_url,
    ) as page:
        try:
            yield page
        finally:
            try:
                await page.goto("about:blank", wait_until="commit", timeout=5_000)
            except Exception:
                log.debug("darwinbox.browser_drain_failed", exc_info=True)


async def _prepare_page(page, board: DarwinboxBoard, browser_config: dict) -> None:
    from src.shared.browser import navigate, safe_content

    navigation_config = {"wait": "domcontentloaded", "timeout": 60_000, **browser_config}
    document_responses: list[Any] = []

    def capture_document(response) -> None:
        if response.request.resource_type == "document":
            document_responses.append(response)

    page.on("response", capture_document)
    try:
        await navigate(page, board.listing_url(), navigation_config)
    finally:
        page.remove_listener("response", capture_document)
    final_board = darwinbox_board_from_url(page.url)
    if final_board != board:
        raise ValueError(f"Darwinbox host {board.host!r} redirected outside its public portal")
    matching_responses = [
        response
        for response in document_responses
        if darwinbox_board_from_url(response.url) == board
    ]
    if not matching_responses:
        raise ValueError(f"Darwinbox host {board.host!r} omitted its document response")
    response_headers = await matching_responses[-1].all_headers()
    content = await safe_content(page)
    check_browser_response(response_headers, content, url=page.url)
    _raise_if_bot_challenge(page.url, content)


async def stream(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> AsyncIterator[MonitorResult]:
    """Stream the complete Darwinbox inventory in authoritative API pages."""
    _ = client
    if pw is None:
        raise RuntimeError("Darwinbox monitor requires a Playwright browser")

    from src.shared.browser import BROWSER_KEYS

    key = _board_key(board)
    config = board.get("metadata") or {}
    browser_config = {name: value for name, value in config.items() if name in BROWSER_KEYS}
    async with _open_darwinbox_page(
        pw,
        browser_config,
        use_proxy=bool(config.get("proxy")),
        target_url=key.listing_url(),
    ) as page:
        await _prepare_page(page, key, browser_config)
        fetch = make_browser_fetcher(page)
        expected_total: int | None = None
        count_changed = False
        raw_seen = 0
        invalid = 0
        duplicates = 0
        seen_urls: set[str] = set()

        for page_number in range(1, MAX_PAGES + 1):
            try:
                payload = await _fetch_jobs_page(fetch, key, page_number)
            except PaginationFetchError as exc:
                if page_number == 1 and exc.last_status in _GONE_STATUSES:
                    raise BoardGoneError(
                        "Darwinbox career portal no longer exists",
                        url=key.jobs_url(),
                        status_code=exc.last_status,
                    ) from exc
                raise
            total, rows = _page_rows(payload, key)
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                log.warning(
                    "darwinbox.count_changed",
                    host=key.host,
                    company_id=key.company_id,
                    previous=expected_total,
                    current=total,
                )
                expected_total = total
                count_changed = True

            if not rows and raw_seen < total:
                retry_payload = await _fetch_jobs_page(fetch, key, page_number)
                retry_total, rows = _page_rows(retry_payload, key)
                if retry_total != expected_total:
                    expected_total = retry_total
                    count_changed = True
                if not rows:
                    raise PaginationFetchError(
                        key.jobs_url(),
                        attempts=2,
                        last_status=200,
                        last_error="PrematureEmptyDarwinboxPage",
                    )

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

            if raw_seen < total and len(rows) < PAGE_SIZE:
                raise PaginationFetchError(
                    key.jobs_url(),
                    attempts=1,
                    last_status=200,
                    last_error="PrematurePartialDarwinboxPage",
                )

            done = raw_seen >= total or raw_seen >= MAX_JOBS
            truncated = done and (
                count_changed
                or invalid > 0
                or duplicates > 0
                or raw_seen != total
                or total > MAX_JOBS
            )
            if page_jobs or done:
                yield _rich_result(page_jobs, truncated=truncated)
            if done:
                if expected_total and not seen_urls:
                    raise ValueError(f"Darwinbox host {key.host!r} returned no valid jobs")
                log_method = log.warning if truncated else log.info
                log_method(
                    "darwinbox.discovered",
                    host=key.host,
                    company_id=key.company_id,
                    jobs=len(seen_urls),
                    raw_seen=raw_seen,
                    expected_total=expected_total,
                    invalid=invalid,
                    duplicates=duplicates,
                    count_changed=count_changed,
                    truncated=truncated,
                )
                return
        else:
            yield _rich_result([], truncated=True)


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | MonitorResult:
    """Materialized form used by single-board debug commands and tests."""
    jobs: list[DiscoveredJob] = []
    truncated = False
    async for result in stream(board, client, pw=pw):
        if result.jobs_by_url:
            jobs.extend(result.jobs_by_url.values())
        truncated = truncated or result.truncated
    return truncated_rich_result(jobs) if truncated else jobs


def _metadata(board: DarwinboxBoard) -> dict[str, str]:
    return {"host": board.host, "company_id": board.company_id}


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked Darwinbox public portals."""
    _ = pw
    direct = darwinbox_board_from_url(url)
    if direct is not None:
        return _metadata(direct)
    if client is None:
        return None
    page = await fetch_page_text(url, client)
    if not page:
        return None
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(page)
        if match is None:
            continue
        board = darwinbox_board_from_url(match.group(1))
        if board is not None:
            log.info("darwinbox.detected_in_page", url=url, host=board.host)
            return _metadata(board)
    return None


register(
    "darwinbox",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    stream=stream,
)
