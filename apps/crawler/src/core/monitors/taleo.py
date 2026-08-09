"""Taleo Business Edition server-rendered listing monitor.

The v2 listing exposes either an authoritative total or a validated ten-row
cursor, plus stable requisition links. Discovery reuses Jobseek's DOM link
extractor and HTTP retry policy; detail extraction stays on the existing
JSON-LD scraper schedule.
"""

from __future__ import annotations

import html
import math
import re
from pathlib import Path

import httpx
import structlog

from src.core.monitors import BoardGoneError, fetch_page_text, register
from src.core.monitors.dom import _extract_links_static, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.taleo import (
    TaleoBoard,
    taleo_board_from_metadata,
    taleo_board_from_url,
    taleo_inactive_redirect,
    taleo_listing_marker_from_html,
    taleo_next_offset_from_html,
    taleo_requisition_id,
    taleo_safe_redirect,
    taleo_total_from_html,
)
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

PAGE_SIZE = 10
MAX_JOBS = 50_000
MAX_HTML_CHARS = 2_000_000
MAX_REDIRECTS = 4
_GONE_STATUSES = frozenset({404, 410})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_TRANSIENT_STATUSES = frozenset({202, 401, 403})
_JOB_URL_RE = re.compile(
    r"/[a-z]{3}[0-9]{2}/ats/careers/v2/viewRequisition\?",
    re.IGNORECASE,
)
_PAGE_PATTERNS = [
    re.compile(
        r"(https://[a-z]{3}\.tbe\.taleo\.net/[a-z]{3}[0-9]{2}/ats/"
        r"careers/v2/(?:searchResults|viewRequisition)\?[^#\"'<\s]+)",
        re.IGNORECASE,
    )
]


def _configured_board(metadata: object) -> TaleoBoard | None:
    if not isinstance(metadata, dict):
        if metadata:
            raise ValueError("Taleo monitor metadata must be an object")
        return None
    configured = taleo_board_from_metadata(metadata)
    if configured is None and any(key in metadata for key in ("host", "partition", "org", "cws")):
        raise ValueError("Taleo monitor metadata contains an invalid board identity")
    return configured


def _board_key(board: dict) -> tuple[TaleoBoard, bool]:
    configured = _configured_board(board.get("metadata") or {})
    direct = taleo_board_from_url(board["board_url"])
    if configured is not None and direct is not None and configured.org != direct.org:
        raise ValueError("Configured Taleo organization does not match the board URL")
    resolved = configured or direct
    if resolved is None:
        raise ValueError(
            f"Cannot derive Taleo identity from board URL {board['board_url']!r} or metadata"
        )
    return resolved, configured is not None


async def _fetch_first_page(
    board: TaleoBoard,
    client: httpx.AsyncClient,
    *,
    allow_migrations: bool,
) -> tuple[TaleoBoard, str]:
    current_board = board
    url = board.listing_url()
    for redirect_count in range(MAX_REDIRECTS + 1):
        try:
            body = await fetch_text_page_with_retry(
                client,
                url,
                require_nonempty=True,
                max_chars=MAX_HTML_CHARS + 1,
                follow_redirects=False,
                end_of_pagination_statuses=(),
                retryable_statuses=_TRANSIENT_STATUSES,
                log_event="taleo.listing_backoff",
            )
        except PaginationFetchError as exc:
            if exc.last_status in _GONE_STATUSES:
                raise BoardGoneError(
                    "Taleo board no longer exists",
                    url=url,
                    status_code=exc.last_status,
                ) from exc
            if exc.last_status not in _REDIRECT_STATUSES:
                raise
            if taleo_inactive_redirect(current_board, url, exc.last_location):
                raise BoardGoneError(
                    "Taleo board is inactive",
                    url=url,
                    status_code=exc.last_status,
                ) from exc
            redirected = taleo_safe_redirect(current_board, url, exc.last_location)
            if redirected is None:
                raise ValueError("Taleo listing returned an untrusted redirect") from exc
            if not allow_migrations and redirected[1] != current_board:
                raise ValueError("Configured Taleo identity redirected unexpectedly") from exc
            if redirect_count == MAX_REDIRECTS:
                raise ValueError("Taleo listing exceeded the trusted redirect cap") from exc
            url, current_board = redirected
            continue
        if body is None:  # Strict status handling above makes this unreachable.
            raise RuntimeError(f"Taleo listing fetch returned no page for {url!r}")
        _raise_if_bot_challenge(url, body)
        if len(body) > MAX_HTML_CHARS:
            raise ValueError(f"Taleo organization {current_board.org!r} exceeded the HTML cap")
        return current_board, body
    raise RuntimeError("unreachable Taleo redirect loop")


async def _fetch_page(board: TaleoBoard, client: httpx.AsyncClient, row_from: int) -> str:
    url = board.listing_url(row_from=row_from)
    body = await fetch_text_page_with_retry(
        client,
        url,
        require_nonempty=True,
        max_chars=MAX_HTML_CHARS + 1,
        follow_redirects=False,
        end_of_pagination_statuses=(),
        retryable_statuses=_TRANSIENT_STATUSES,
        log_event="taleo.page_backoff",
    )
    if body is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"Taleo pagination fetch returned no page for {url!r}")
    _raise_if_bot_challenge(url, body)
    if len(body) > MAX_HTML_CHARS:
        raise ValueError(f"Taleo organization {board.org!r} exceeded the HTML cap")
    return body


def _parse_page(
    body: str,
    board: TaleoBoard,
    *,
    row_from: int,
) -> tuple[int | None, set[str], int | None]:
    total = taleo_total_from_html(body)
    raw_urls = _extract_links_static(body, board.listing_url(row_from=row_from), _JOB_URL_RE)
    requisition_ids = {
        requisition_id
        for url in raw_urls
        if (requisition_id := taleo_requisition_id(url, board)) is not None
    }
    urls = {board.job_url(requisition_id) for requisition_id in requisition_ids}
    next_offset = (
        taleo_next_offset_from_html(body, board, current_offset=row_from) if total is None else None
    )
    if total is not None:
        expected = min(PAGE_SIZE, max(total - row_from, 0))
        if len(urls) != expected:
            raise ValueError(
                f"Taleo organization {board.org!r} advertised {expected} jobs at offset "
                f"{row_from} but exposed {len(urls)} valid detail links"
            )
    else:
        if not taleo_listing_marker_from_html(body):
            raise ValueError(f"Taleo organization {board.org!r} omitted a valid listing marker")
        if len(urls) > PAGE_SIZE or (next_offset is not None and len(urls) != PAGE_SIZE):
            raise ValueError(
                f"Taleo organization {board.org!r} exposed an incomplete cursor page "
                f"at offset {row_from}"
            )
    return total, urls, next_offset


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover every canonical Taleo Business Edition requisition URL."""
    _ = pw
    configured, is_configured = _board_key(board)
    resolved, first_body = await _fetch_first_page(
        configured,
        client,
        allow_migrations=not is_configured,
    )
    total, first_urls, next_offset = _parse_page(first_body, resolved, row_from=0)
    if total is not None and total > MAX_JOBS:
        raise ValueError(
            f"Taleo organization {resolved.org!r} exceeded the {MAX_JOBS:,}-job safety cap"
        )

    urls = set(first_urls)
    if total is not None:
        page_count = math.ceil(total / PAGE_SIZE)
        for page in range(1, page_count):
            row_from = page * PAGE_SIZE
            body = await _fetch_page(resolved, client, row_from)
            page_total, page_urls, _page_next = _parse_page(
                body,
                resolved,
                row_from=row_from,
            )
            if page_total != total:
                raise ValueError(
                    f"Taleo organization {resolved.org!r} changed total during pagination"
                )
            before = len(urls)
            urls.update(page_urls)
            if len(urls) - before != len(page_urls):
                raise ValueError(
                    f"Taleo organization {resolved.org!r} repeated requisitions across pages"
                )
        if len(urls) != total:
            raise ValueError(
                f"Taleo organization {resolved.org!r} returned {len(urls)} of {total} jobs"
            )
    else:
        while next_offset is not None:
            if len(urls) >= MAX_JOBS:
                raise ValueError(
                    f"Taleo organization {resolved.org!r} exceeded the {MAX_JOBS:,}-job safety cap"
                )
            body = await _fetch_page(resolved, client, next_offset)
            page_total, page_urls, following_offset = _parse_page(
                body,
                resolved,
                row_from=next_offset,
            )
            if page_total is not None:
                raise ValueError(
                    f"Taleo organization {resolved.org!r} changed pagination themes mid-run"
                )
            if not page_urls:
                raise ValueError(
                    f"Taleo organization {resolved.org!r} returned an empty cursor child page"
                )
            before = len(urls)
            urls.update(page_urls)
            if len(urls) - before != len(page_urls):
                raise ValueError(
                    f"Taleo organization {resolved.org!r} repeated requisitions across pages"
                )
            next_offset = following_offset
    log.info(
        "taleo.discovered",
        host=resolved.host,
        partition=resolved.partition,
        org=resolved.org,
        cws=resolved.cws,
        jobs=len(urls),
    )
    return urls


def _result(board: TaleoBoard, jobs: int | str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "host": board.host,
        "partition": board.partition,
        "org": board.org,
        "cws": board.cws,
    }
    if jobs is not None:
        result["jobs"] = jobs
    return result


async def _probe_board(board: TaleoBoard, client: httpx.AsyncClient) -> dict | None:
    try:
        resolved, body = await _fetch_first_page(board, client, allow_migrations=True)
        total, urls, next_offset = _parse_page(body, resolved, row_from=0)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("taleo.probe_failed", url=board.listing_url(), exc_info=True)
        return None
    jobs: int | str = total if total is not None else f"{len(urls)}+" if next_offset else len(urls)
    return _result(resolved, jobs)


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked Taleo Business Edition boards."""
    _ = pw
    direct = taleo_board_from_url(html.unescape(url))
    if direct is not None:
        return _result(direct) if client is None else await _probe_board(direct, client)
    if client is None:
        return None

    page = await fetch_page_text(url, client)
    if not page:
        return None
    for pattern in _PAGE_PATTERNS:
        for match in pattern.finditer(page):
            candidate = taleo_board_from_url(html.unescape(match.group(1)))
            if candidate is None:
                continue
            result = await _probe_board(candidate, client)
            if result is not None:
                log.info("taleo.detected_in_page", url=url, org=candidate.org)
                return result
    return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    board, _is_configured = _board_key({"board_url": board_url, "metadata": metadata})
    await save_text_response(
        artifact_dir,
        client,
        board.listing_url(),
        filename="taleo-listing.html",
        follow_redirects=False,
    )


register(
    "taleo",
    discover,
    cost=10,
    can_handle=can_handle,
    save_raw=save_raw,
)
