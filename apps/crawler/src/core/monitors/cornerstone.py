"""Cornerstone public career-site monitor.

Each ``*.csod.com`` listing embeds a short-lived public token and a trusted
regional ``*.api.csod.com`` origin. The monitor refreshes that bootstrap when
authorization expires and streams complete job records from Cornerstone's
public paginated search endpoint without per-job detail requests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.dom import _raise_if_bot_challenge
from src.shared.cornerstone import (
    CornerstoneBoard,
    CornerstoneContext,
    CornerstoneContextMissingError,
    cornerstone_board_from_metadata,
    cornerstone_board_from_url,
    extract_cornerstone_context,
)
from src.shared.http_retry import (
    PaginationFetchError,
    fetch_json_page_with_retry,
    fetch_text_page_with_retry,
)
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
MAX_HTML_CHARS = 1_000_000
_BOOTSTRAP_CONTEXT_ATTEMPTS = 2
_BOOTSTRAP_CONTEXT_RETRY_DELAY = 0.5

_PAGE_PATTERNS = [
    re.compile(
        r"(https?://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.csod\.com/"
        r"ux/ats/careersite/[1-9]\d{0,9}/home"
        r"(?:/requisition/[1-9]\d{0,19})?\?c=[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?)"
        r"(?=[#\"'<\s]|$)",
        re.IGNORECASE,
    )
]
_GONE_STATUSES = frozenset({404, 410})


def _board_key(board: dict) -> CornerstoneBoard:
    metadata = board.get("metadata") or {}
    key = cornerstone_board_from_metadata(metadata) or cornerstone_board_from_url(
        board["board_url"]
    )
    if key is None:
        raise ValueError(
            f"Cannot derive Cornerstone tenant/site/corp from board URL "
            f"{board['board_url']!r} or metadata"
        )
    return key


async def _bootstrap(board: CornerstoneBoard, client: httpx.AsyncClient) -> CornerstoneContext:
    url = board.listing_url()
    for attempt in range(1, _BOOTSTRAP_CONTEXT_ATTEMPTS + 1):
        try:
            page = await fetch_text_page_with_retry(
                client,
                url,
                require_nonempty=True,
                max_chars=MAX_HTML_CHARS,
                follow_redirects=False,
                end_of_pagination_statuses=(),
                retryable_statuses={202, 401, 403},
                log_event="cornerstone.bootstrap_backoff",
            )
        except PaginationFetchError as exc:
            if exc.last_status in _GONE_STATUSES:
                raise BoardGoneError("Cornerstone board no longer exists", url=url) from exc
            raise
        if page is None:  # Strict status handling above makes this unreachable.
            raise RuntimeError(f"Cornerstone listing fetch returned no page for {board!r}")
        _raise_if_bot_challenge(url, page)
        if len(page) >= MAX_HTML_CHARS:
            raise ValueError("Cornerstone listing exceeded the bootstrap HTML safety cap")
        try:
            return extract_cornerstone_context(page, board)
        except CornerstoneContextMissingError:
            if attempt == _BOOTSTRAP_CONTEXT_ATTEMPTS:
                raise
            folded = page.casefold()
            log.warning(
                "cornerstone.bootstrap_context_missing",
                tenant=board.tenant,
                site_id=board.site_id,
                attempt=attempt,
                html_chars=len(page),
                script_tags=min(folded.count("<script"), 10_000),
                has_html="<html" in folded,
                has_csod_text="csod" in folded,
                body_sha256_16=hashlib.sha256(page.encode()).hexdigest()[:16],
            )
            await asyncio.sleep(_BOOTSTRAP_CONTEXT_RETRY_DELAY)
    raise AssertionError("unreachable")


def _search_payload(
    board: CornerstoneBoard,
    context: CornerstoneContext,
    page_number: int,
    *,
    page_size: int = PAGE_SIZE,
) -> dict:
    return {
        "careerSiteId": board.site_id,
        "careerSitePageId": board.site_id,
        "pageNumber": page_number,
        "pageSize": page_size,
        "cultureId": context.culture_id,
        "searchText": "",
        "cultureName": context.culture_name,
        "states": [],
        "countryCodes": [],
        "cities": [],
        "placeID": "",
        "radius": None,
        "postingsWithinDays": None,
        "customFieldCheckboxKeys": [],
        "customFieldDropdowns": [],
        "customFieldRadios": [],
    }


async def _fetch_search_page(
    board: CornerstoneBoard,
    context: CornerstoneContext,
    client: httpx.AsyncClient,
    page_number: int,
    *,
    page_size: int = PAGE_SIZE,
) -> tuple[dict, CornerstoneContext]:
    async def fetch(active: CornerstoneContext) -> dict:
        return await fetch_json_page_with_retry(
            client,
            active.search_url,
            expect_shape=dict,
            method="POST",
            json_body=_search_payload(board, active, page_number, page_size=page_size),
            headers=active.headers,
            follow_redirects=False,
            retries=3,
            base_delay=0.5,
            log_event="cornerstone.search_backoff",
        )

    try:
        return await fetch(context), context
    except PaginationFetchError as exc:
        if exc.last_status not in {401, 403}:
            raise
    refreshed = await _bootstrap(board, client)
    return await fetch(refreshed), refreshed


def _page_rows(payload: dict, board: CornerstoneBoard) -> tuple[int, list[object]]:
    if payload.get("status") != "Success":
        raise ValueError(f"Cornerstone {board.tenant!r} search did not report success")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"Cornerstone {board.tenant!r} search omitted its data object")
    total = data.get("totalCount")
    rows = data.get("requisitions")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError(f"Cornerstone {board.tenant!r} search omitted a valid count")
    if not isinstance(rows, list):
        raise ValueError(f"Cornerstone {board.tenant!r} search omitted its requisitions")
    return total, rows


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _parse_locations(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    locations: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        location = ", ".join(
            part
            for key in ("city", "state", "country")
            if (part := _clean_string(raw.get(key))) is not None
        )
        if location and location not in seen:
            seen.add(location)
            locations.append(location)
    return locations or None


def _parse_date(value: object, culture_name: str) -> str | None:
    raw = _clean_string(value)
    if raw is None or raw == "-":
        return None
    formats = ["%Y-%m-%d"]
    formats.extend(
        ["%m/%d/%Y", "%m-%d-%Y"] if culture_name.casefold() == "en-us" else ["%d/%m/%Y", "%d-%m-%Y"]
    )
    for format_ in formats:
        try:
            return datetime.strptime(raw, format_).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_job(
    raw: object,
    board: CornerstoneBoard,
    culture_name: str,
) -> DiscoveredJob | None:
    if not isinstance(raw, dict):
        return None
    requisition_id = raw.get("requisitionId")
    if isinstance(requisition_id, bool) or not isinstance(requisition_id, (int, str)):
        return None
    requisition = str(requisition_id)
    if not requisition.isdigit() or not 1 <= len(requisition) <= 20 or int(requisition) <= 0:
        return None
    title = _clean_string(raw.get("displayJobTitle"))
    if title is None:
        return None
    language = culture_name.split("-", 1)[0].lower()
    return DiscoveredJob(
        url=board.job_url(int(requisition)),
        title=title,
        description=_clean_string(raw.get("externalDescription")),
        locations=_parse_locations(raw.get("locations")),
        date_posted=_parse_date(raw.get("postingEffectiveDate"), culture_name),
        language=language if len(language) == 2 else None,
        metadata={"requisition_id": int(requisition)},
    )


def _rich_result(jobs: list[DiscoveredJob], *, truncated: bool = False) -> MonitorResult:
    from src.core.monitor import MonitorResult

    return MonitorResult(
        urls={job.url for job in jobs},
        jobs_by_url={job.url: job for job in jobs},
        truncated=truncated,
    )


async def stream(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> AsyncIterator[MonitorResult]:
    """Stream bounded, complete API pages while preserving failure semantics."""
    _ = pw
    key = _board_key(board)
    context = await _bootstrap(key, client)
    expected_total: int | None = None
    raw_seen = 0
    invalid = 0
    duplicates = 0
    seen_urls: set[str] = set()

    for page_number in range(1, MAX_PAGES + 1):
        payload, context = await _fetch_search_page(key, context, client, page_number)
        total, rows = _page_rows(payload, key)
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValueError(
                f"Cornerstone {key.tenant!r} count changed during pagination "
                f"({expected_total} -> {total})"
            )

        if not rows and raw_seen < total:
            payload, context = await _fetch_search_page(key, context, client, page_number)
            retry_total, rows = _page_rows(payload, key)
            if retry_total != expected_total:
                raise ValueError(
                    f"Cornerstone {key.tenant!r} count changed during pagination "
                    f"({expected_total} -> {retry_total})"
                )
            if not rows:
                raise PaginationFetchError(
                    context.search_url,
                    attempts=2,
                    last_status=200,
                    last_error="PrematureEmptyCornerstonePage",
                )

        if len(rows) > PAGE_SIZE or raw_seen + len(rows) > total:
            raise ValueError(f"Cornerstone {key.tenant!r} returned an inconsistent search page")

        page_jobs: list[DiscoveredJob] = []
        for raw in rows:
            raw_seen += 1
            job = _parse_job(raw, key, context.culture_name)
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
            invalid > 0 or duplicates > 0 or raw_seen != total or total > MAX_JOBS
        )
        if page_jobs or done:
            yield _rich_result(page_jobs, truncated=truncated)
        if done:
            if expected_total and not seen_urls:
                raise ValueError(f"Cornerstone {key.tenant!r} returned no valid requisitions")
            log_method = log.warning if truncated else log.info
            log_method(
                "cornerstone.discovered",
                tenant=key.tenant,
                site_id=key.site_id,
                jobs=len(seen_urls),
                raw_seen=raw_seen,
                expected_total=expected_total,
                invalid=invalid,
                duplicates=duplicates,
                truncated=truncated,
            )
            return
    else:
        # MAX_PAGES is derived from MAX_JOBS, but keep a terminal batch so
        # callers still suppress gone detection if constants ever diverge.
        yield _rich_result([], truncated=True)


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Materialized form used by single-board debug commands and tests."""
    jobs: list[DiscoveredJob] = []
    truncated = False
    async for result in stream(board, client, pw=pw):
        if result.jobs_by_url:
            jobs.extend(result.jobs_by_url.values())
        truncated = truncated or result.truncated
    return truncated_rich_result(jobs) if truncated else jobs


async def _probe_listing_url(listing_url: str, client: httpx.AsyncClient) -> ProbeResult:
    board = cornerstone_board_from_url(listing_url)
    if board is None:
        return False, None
    try:
        context = await _bootstrap(board, client)
        payload, _context = await _fetch_search_page(
            board,
            context,
            client,
            page_number=1,
            page_size=1,
        )
        total, _rows = _page_rows(payload, board)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("cornerstone.probe_failed", listing_url=listing_url, exc_info=True)
        return False, None
    return True, total


async def _fetch_job_count(
    listing_url: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_listing_url(listing_url, client)
    return count if found else None


async def _probe_candidate(
    listing_url: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_listing_url(listing_url, client)


def _listing_token_from_url(url: str) -> str | None:
    board = cornerstone_board_from_url(url)
    return board.listing_url() if board is not None else None


def _build_result(listing_url: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    board = cornerstone_board_from_url(listing_url)
    if board is None:  # The shared ATS adapter only supplies validated tokens.
        raise ValueError("Cornerstone result builder received an invalid listing URL")
    result: dict = {
        "tenant": board.tenant,
        "site_id": board.site_id,
        "corp": board.corp,
    }
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked canonical Cornerstone boards."""
    _ = pw
    if (
        cornerstone_board_from_url(url) is None
        and cornerstone_board_from_url(url, validate_query=False) is not None
    ):
        return None
    return await ats_can_handle(
        url,
        client,
        monitor_name="cornerstone",
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
    board = cornerstone_board_from_metadata(metadata) or cornerstone_board_from_url(board_url)
    if board is None:
        return
    context = await _bootstrap(board, client)
    payload, _context = await _fetch_search_page(board, context, client, 1)
    (artifact_dir / "cornerstone-search.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


register(
    "cornerstone",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    stream=stream,
    save_raw=save_raw,
)
