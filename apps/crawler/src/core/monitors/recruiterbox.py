"""Recruiterbox / Trakstar Hire server-rendered listing monitor.

The platform renders stable job links and an authoritative total into each
listing page. This monitor composes Jobseek's DOM link extractor with shared
HTTP retry and truncation primitives; the dedicated Recruiterbox scraper reads
job details from Trakstar's server-rendered markup.
"""

from __future__ import annotations

import html
import math
import re
from pathlib import Path

import httpx
import structlog

from src.core.monitors import BoardGoneError, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.dom import _extract_links_static, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.recruiterbox import (
    RecruiterboxBoard,
    recruiterbox_board_from_metadata,
    recruiterbox_board_from_url,
    recruiterbox_inactive_from_html,
    recruiterbox_job_token,
    recruiterbox_total_from_html,
)
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
MAX_HTML_CHARS = 2_000_000
_TRANSIENT_STATUSES = frozenset({202, 401, 403})
_JOB_URL_RE = re.compile(r"/jobs/[a-z0-9]{3,64}/?(?:[?#]|$)", re.IGNORECASE)
_PAGE_PATTERNS = [
    re.compile(
        r"(https?://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\."
        r"(?:recruiterbox\.com|hire\.trakstar\.com)"
        r"(?:/jobs(?:/[a-z0-9]{3,64})?)?/?(?:\?[^\"'<\s]+)?)",
        re.IGNORECASE,
    )
]


def _board_key(board: dict) -> RecruiterboxBoard:
    metadata = board.get("metadata") or {}
    key = (
        recruiterbox_board_from_metadata(metadata) if isinstance(metadata, dict) else None
    ) or recruiterbox_board_from_url(board["board_url"])
    if key is None:
        raise ValueError(
            f"Cannot derive Recruiterbox tenant from board URL {board['board_url']!r} or metadata"
        )
    return key


def _parse_page(
    body: str,
    board: RecruiterboxBoard,
    *,
    page: int,
) -> tuple[int, set[str], bool]:
    total = recruiterbox_total_from_html(body)
    if total is None:
        raise ValueError(f"Recruiterbox tenant {board.tenant!r} omitted a valid job total")
    raw_urls = _extract_links_static(body, board.listing_url(), _JOB_URL_RE)
    tokens = {
        token for url in raw_urls if (token := recruiterbox_job_token(url, board)) is not None
    }
    urls = {board.job_url(token) for token in tokens}
    expected = min(PAGE_SIZE, max(total - ((page - 1) * PAGE_SIZE), 0))
    if expected > 0 and not urls:
        raise ValueError(
            f"Recruiterbox tenant {board.tenant!r} advertised {expected} jobs "
            f"on page {page} but exposed no valid detail links"
        )
    return total, urls, len(urls) == expected


async def _fetch_page(
    board: RecruiterboxBoard,
    client: httpx.AsyncClient,
    page: int,
) -> tuple[int, set[str], bool] | None:
    url = board.page_url(page, page_size=PAGE_SIZE)
    body = await fetch_text_page_with_retry(
        client,
        url,
        # Identities are canonical before fetching. Refuse redirects so a
        # tenant cannot cross into another board or a branded error shell.
        follow_redirects=False,
        retryable_statuses=_TRANSIENT_STATUSES,
        require_nonempty=True,
        max_chars=MAX_HTML_CHARS + 1,
        log_event="recruiterbox.page_backoff",
    )
    if body is None:
        return None
    if recruiterbox_inactive_from_html(body):
        raise BoardGoneError("Recruiterbox account is inactive", url=url)
    _raise_if_bot_challenge(url, body)
    over_html_cap = len(body) > MAX_HTML_CHARS
    total, urls, complete = _parse_page(body[:MAX_HTML_CHARS], board, page=page)
    return total, urls, complete and not over_html_cap


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover all canonical Trakstar Hire job URLs for one tenant."""
    _ = pw
    key = _board_key(board)
    first = await _fetch_page(key, client, 1)
    if first is None:
        raise BoardGoneError("Recruiterbox board no longer exists", url=key.listing_url())

    first_total, first_urls, first_complete = first
    urls = set(first_urls)
    observed_totals = {first_total}
    truncated = not first_complete or first_total > MAX_JOBS
    page_count = min(math.ceil(first_total / PAGE_SIZE), MAX_PAGES)

    for page in range(2, page_count + 1):
        result = await _fetch_page(key, client, page)
        if result is None:
            truncated = True
            break
        total, page_urls, page_complete = result
        observed_totals.add(total)
        before = len(urls)
        urls.update(page_urls)
        if not page_complete or len(urls) - before != len(page_urls):
            truncated = True

    expected = min(first_total, MAX_JOBS)
    truncated = truncated or len(observed_totals) != 1 or len(urls) != expected
    log_method = log.warning if truncated else log.info
    log_method(
        "recruiterbox.discovered",
        tenant=key.tenant,
        jobs=len(urls),
        expected=first_total,
        truncated=truncated,
    )
    if truncated and not urls:
        raise ValueError(f"Recruiterbox tenant {key.tenant!r} produced an incomplete empty listing")
    return truncated_url_result(urls) if truncated else urls


async def _probe_listing_url(
    listing_url: str,
    client: httpx.AsyncClient,
) -> tuple[bool, int | None]:
    board = recruiterbox_board_from_url(html.unescape(listing_url))
    if board is None:
        return False, None
    try:
        result = await _fetch_page(board, client, 1)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("recruiterbox.probe_failed", listing_url=listing_url, exc_info=True)
        return False, None
    return (True, result[0]) if result is not None else (False, None)


async def _fetch_job_count(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, total = await _probe_listing_url(token, client)
    return total if found else None


async def _probe_candidate(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_listing_url(token, client)


def _listing_url_from_url(url: str) -> str | None:
    board = recruiterbox_board_from_url(html.unescape(url))
    return board.listing_url() if board is not None else None


def _build_result(
    listing_url: str,
    count: ProbeCount | None,
    context: None,
) -> dict:
    _ = context
    board = recruiterbox_board_from_url(html.unescape(listing_url))
    if board is None:
        raise ValueError("Recruiterbox result builder received an invalid listing URL")
    result: dict[str, object] = {"tenant": board.tenant}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked Recruiterbox / Trakstar boards."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="recruiterbox",
        token_from_url=_listing_url_from_url,
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
    key = _board_key({"board_url": board_url, "metadata": metadata})
    await save_text_response(
        artifact_dir,
        client,
        key.page_url(1, page_size=PAGE_SIZE),
        filename="recruiterbox-listing.html",
        follow_redirects=False,
    )


register(
    "recruiterbox",
    discover,
    cost=10,
    can_handle=can_handle,
    save_raw=save_raw,
)
