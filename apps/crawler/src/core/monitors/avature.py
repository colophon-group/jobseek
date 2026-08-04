"""Native Avature server-rendered listing monitor.

The public ``SearchJobs`` page is the authoritative inventory.  Avature's
first-party RSS feed is intentionally not used: live portals cap it at 20
records even when the HTML listing advertises hundreds or thousands.  This
adapter follows the listing's explicit next link and returns stable detail
URLs; the shared DOM scraper owns detail extraction on its normal schedule.
"""

from __future__ import annotations

import html as html_module
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog

from src.core.monitors import BoardGoneError, fetch_page_text, register
from src.core.monitors.dom import _extract_links_static, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.avature import (
    AvatureBoard,
    AvaturePage,
    avature_board_from_metadata,
    avature_board_from_url,
    avature_pagination_url,
    parse_avature_page,
)
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

MAX_JOBS = 50_000
MAX_PAGES = 10_000
MAX_HTML_CHARS = 2_000_000
_GONE_STATUSES = frozenset({404, 410})
_TRANSIENT_STATUSES = frozenset({202, 401, 403, 406})
_PAGE_LINK_RE = re.compile(
    r"/(?:SearchJobs(?:Maps)?|(?:Job|Folder|Pipeline)Detail)(?:[/?#]|$)",
    re.IGNORECASE,
)
_RAW_CANDIDATE_RE = re.compile(
    r"https://[^\s\"'<>]+/(?:SearchJobs(?:Maps)?|(?:Job|Folder|Pipeline)Detail)"
    r"[^\s\"'<>]*",
    re.IGNORECASE,
)


async def _fetch_listing(
    url: str,
    client: httpx.AsyncClient,
    *,
    first_page: bool,
) -> str:
    try:
        body = await fetch_text_page_with_retry(
            client,
            url,
            require_nonempty=True,
            max_chars=MAX_HTML_CHARS + 1,
            follow_redirects=first_page,
            end_of_pagination_statuses=(),
            retryable_statuses=_TRANSIENT_STATUSES,
            log_event="avature.listing_backoff",
        )
    except PaginationFetchError as exc:
        if first_page and exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "Avature board no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    if body is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"Avature listing fetch returned no page for {url!r}")
    _raise_if_bot_challenge(url, body)
    if len(body) > MAX_HTML_CHARS:
        raise ValueError("Avature listing exceeded the HTML safety cap")
    return body


def _validate_listing_page(page: AvaturePage, *, expected_start: int) -> None:
    if page.total is None:
        raise ValueError("Avature listing omitted its authoritative result count")
    if page.total == 0:
        if page.jobs or page.next_urls:
            raise ValueError("Avature zero-result listing exposed jobs or pagination")
        return
    if page.range_start is None or page.range_end is None:
        raise ValueError("Avature non-empty listing omitted its displayed result range")
    if page.range_start != expected_start or page.range_end < page.range_start:
        raise ValueError(
            f"Avature listing returned range {page.range_start}-{page.range_end}; "
            f"expected a page beginning at {expected_start}"
        )
    expected_jobs = page.range_end - page.range_start + 1
    if len(page.jobs) != expected_jobs:
        raise ValueError(
            f"Avature listing advertised {expected_jobs} visible jobs but exposed "
            f"{len(page.jobs)} stable detail links"
        )
    if len(page.next_urls) > 1:
        raise ValueError("Avature listing exposed conflicting next-page links")


async def _stream_listing(
    configured: AvatureBoard,
    client: httpx.AsyncClient,
    *,
    identity_is_configured: bool,
    configured_portal_id: str | None,
) -> AsyncIterator[MonitorResult]:
    from src.core.monitor import MonitorResult

    first_body = await _fetch_listing(configured.listing_url, client, first_page=True)
    first = parse_avature_page(first_body, configured.listing_url)
    if first is None or first.board.page != configured.page:
        raise ValueError("Avature URL returned a different listing page")
    if (
        identity_is_configured
        and first.board.listing_url.casefold() != configured.listing_url.casefold()
    ):
        raise ValueError("Configured Avature portal redirected to a different listing identity")
    if configured_portal_id is not None and first.portal_id != configured_portal_id:
        raise ValueError("Configured Avature portal ID changed")

    board = first.board
    portal_id = first.portal_id
    expected_start = 1
    current_offset = 0
    current = first
    urls: dict[str, str] = {}
    first_total = first.total
    first_exact = first.total_exact
    truncated = bool(first_total is not None and first_total > MAX_JOBS)
    pages = 0
    seen_pages: set[str] = {board.listing_url.casefold()}

    while True:
        pages += 1
        _validate_listing_page(current, expected_start=expected_start)
        if current.board.listing_url.casefold() != board.listing_url.casefold():
            raise ValueError("Avature pagination crossed into a different portal")
        if current.portal_id != portal_id:
            raise ValueError("Avature portal ID changed during pagination")
        if current.total != first_total or current.total_exact != first_exact:
            truncated = True

        overlap = urls.keys() & current.jobs.keys()
        if overlap:
            truncated = True
        new_jobs = {identity: url for identity, url in current.jobs.items() if identity not in urls}
        remaining = max(MAX_JOBS - len(urls), 0)
        if len(new_jobs) > remaining:
            new_jobs = dict(sorted(new_jobs.items())[:remaining])
            truncated = True
        urls.update(new_jobs)

        done = len(urls) >= MAX_JOBS or pages >= MAX_PAGES or not current.next_urls
        if done:
            if current.next_urls and (len(urls) >= MAX_JOBS or pages >= MAX_PAGES):
                truncated = True
            if first_total is not None and (
                (first_exact and len(urls) != first_total)
                or (not first_exact and len(urls) <= first_total)
            ):
                truncated = True
        metadata_updates = (
            {"listing_url": board.listing_url, "portal_id": portal_id} if pages == 1 else None
        )
        if new_jobs or done:
            yield MonitorResult(
                urls=set(new_jobs.values()),
                truncated=truncated if done else False,
                metadata_updates=metadata_updates,
            )
        if done:
            log_method = log.warning if truncated else log.info
            log_method(
                "avature.discovered",
                host=board.host,
                portal_id=portal_id,
                jobs=len(urls),
                pages=pages,
                advertised=first_total,
                truncated=truncated,
            )
            return

        next_url = current.next_urls[0]
        pagination = avature_pagination_url(next_url, board)
        if pagination is None:
            raise ValueError("Avature listing exposed an invalid next-page URL")
        canonical_next, next_offset = pagination
        if (
            next_offset <= current_offset
            or (current.range_end is not None and next_offset != current.range_end)
            or canonical_next.casefold() in seen_pages
        ):
            raise ValueError("Avature listing pagination did not advance monotonically")
        seen_pages.add(canonical_next.casefold())
        expected_start = (current.range_end or 0) + 1
        current_offset = next_offset
        body = await _fetch_listing(canonical_next, client, first_page=False)
        following = parse_avature_page(body, canonical_next)
        if following is None or following.board.page != board.page:
            raise ValueError("Avature pagination returned a non-listing page")
        current = following


def _board_identity(board: dict) -> tuple[AvatureBoard, bool, str | None]:
    metadata = board.get("metadata") or {}
    configured = avature_board_from_metadata(metadata)
    direct = avature_board_from_url(board["board_url"], allow_custom_host=True)
    resolved = configured or direct
    if resolved is None:
        raise ValueError(
            f"Cannot derive an Avature listing from {board['board_url']!r}; "
            "configure metadata.listing_url"
        )
    configured_portal_id = metadata.get("portal_id")
    if configured_portal_id is not None:
        configured_portal_id = str(configured_portal_id)
        if not configured_portal_id.isdigit() or int(configured_portal_id) <= 0:
            raise ValueError("Avature portal_id metadata must be a positive integer")
    return resolved, configured is not None, configured_portal_id


async def stream(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> AsyncIterator[MonitorResult]:
    """Stream validated listing pages so large portals keep worker heartbeats alive."""

    _ = pw
    resolved, identity_is_configured, configured_portal_id = _board_identity(board)
    async for result in _stream_listing(
        resolved,
        client,
        identity_is_configured=identity_is_configured,
        configured_portal_id=configured_portal_id,
    ):
        yield result


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> MonitorResult:
    """Materialized form used by workspace commands and focused tests."""

    from src.core.monitor import MonitorResult

    urls: set[str] = set()
    truncated = False
    metadata_updates: dict = {}
    async for result in stream(board, client, pw=pw):
        urls.update(result.urls)
        truncated = truncated or result.truncated
        if result.metadata_updates:
            metadata_updates.update(result.metadata_updates)
    return MonitorResult(
        urls=urls,
        truncated=truncated,
        metadata_updates=metadata_updates or None,
    )


async def _probe_board(board: AvatureBoard, client: httpx.AsyncClient) -> dict | None:
    try:
        body = await _fetch_listing(board.listing_url, client, first_page=True)
        page = parse_avature_page(body, board.listing_url)
        if page is None:
            return None
        _validate_listing_page(page, expected_start=1)
        jobs = page.total
    except TDMReservedError:
        raise
    except Exception:
        log.debug("avature.probe_failed", listing_url=board.listing_url, exc_info=True)
        return None
    result: dict = {
        "listing_url": page.board.listing_url,
        "portal_id": page.portal_id,
    }
    if jobs is not None:
        result["jobs"] = jobs if page.total_exact else f"{jobs}+"
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked Avature portals without slug guesses."""

    _ = pw
    direct = avature_board_from_url(url, allow_custom_host=True)
    if direct is not None:
        if client is None:
            # Only vendor-host URLs are safe to classify without checking the
            # page-level Avature marker. Branded hosts require a live probe.
            vendor = avature_board_from_url(url)
            return {"listing_url": vendor.listing_url} if vendor is not None else None
        return await _probe_board(direct, client)
    if client is None:
        return None

    page_html = await fetch_page_text(url, client)
    if not page_html:
        return None
    decoded = html_module.unescape(page_html)
    raw_candidates = _extract_links_static(decoded, url, _PAGE_LINK_RE)
    raw_candidates.update(_RAW_CANDIDATE_RE.findall(decoded))
    candidates: dict[str, AvatureBoard] = {}
    for candidate in sorted(raw_candidates):
        board = avature_board_from_url(candidate, allow_custom_host=True)
        if board is not None:
            candidates.setdefault(board.listing_url.casefold(), board)
        if len(candidates) >= 8:
            break
    for candidate in candidates.values():
        if result := await _probe_board(candidate, client):
            log.info("avature.detected_in_page", url=url, listing_url=result["listing_url"])
            return result
    return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    configured = avature_board_from_metadata(metadata)
    direct = avature_board_from_url(board_url, allow_custom_host=True)
    board = configured or direct
    if board is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        board.listing_url,
        filename="listing.html",
        follow_redirects=True,
    )


register(
    "avature",
    discover,
    cost=10,
    can_handle=can_handle,
    stream=stream,
    save_raw=save_raw,
)
