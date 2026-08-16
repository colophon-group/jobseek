"""104 Job Bank company-page monitor.

104 exposes public, server-rendered employer pages at
``https://www.104.com.tw/company/{token}``.  The pages contain canonical job
links even though the provider's private JSON endpoints are guarded by
Cloudflare and change independently of the public surface.  This adapter
therefore treats the HTML page as the source of truth and leaves detail-field
extraction to the existing JSON-LD scraper.

Boards that are challenged from datacenter egress should set ``proxy: true``;
the normal board runner then supplies this monitor with a proxy-routed client.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, register
from src.core.monitors.dom import _extract_links_static, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.extract import flatten
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_HTML_CHARS = 5_000_000

_TOKEN_RE = re.compile(r"^[a-z0-9]{5,16}$", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"^[a-z0-9]{5,16}$", re.IGNORECASE)
_VISIBLE_COUNT_RE = re.compile(r"工作機會\s*[（(]\s*([\d,]+)\s*[）)]")
_COUNT_PATTERNS = (
    _VISIBLE_COUNT_RE,
    re.compile(r'["\']jobCount["\']\s*:\s*["\']?([\d,]+)', re.IGNORECASE),
)
_GONE_STATUSES = frozenset({404, 410})


def _normalize_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    return token if _TOKEN_RE.fullmatch(token) else None


def _token_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "www.104.com.tw"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 2 or segments[0].lower() != "company":
        return None
    return _normalize_token(segments[1])


def _resolve_token(board_url: str, metadata: dict) -> str | None:
    return _normalize_token(metadata.get("token")) or _token_from_url(board_url)


def _listing_url(token: str) -> str:
    return f"https://www.104.com.tw/company/{token}"


def _job_matcher() -> re.Pattern[str]:
    return re.compile(
        r"^https://www\.104\.com\.tw/job/[a-z0-9]{5,16}(?:[/?#]|$)",
        re.IGNORECASE,
    )


def _canonical_job_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "www.104.com.tw"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) != 2
        or segments[0].lower() != "job"
        or _JOB_ID_RE.fullmatch(segments[1]) is None
    ):
        return None
    return f"https://www.104.com.tw/job/{segments[1].lower()}"


def _parse_listing(page: str, token: str) -> set[str]:
    raw_urls = _extract_links_static(page, _listing_url(token), _job_matcher())
    return {canonical for url in raw_urls if (canonical := _canonical_job_url(url)) is not None}


def _advertised_count(page: str) -> int | None:
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(page)
        if match is not None:
            return int(match.group(1).replace(",", ""))
    return None


def _explicitly_advertises_zero_jobs(page: str) -> bool:
    """Only visible listing text can authorize an empty inventory.

    A generic SPA shell may contain a JSON default such as ``jobCount: 0``
    before client-side data loads. Treating that bootstrap value as a real
    empty board would allow one shell response to delist every known job.
    """

    visible_text = " ".join(element["text"] for element in flatten(page))
    match = _VISIBLE_COUNT_RE.search(visible_text)
    return match is not None and int(match.group(1).replace(",", "")) == 0


async def _fetch_listing(token: str, client: httpx.AsyncClient) -> str:
    url = _listing_url(token)
    try:
        page = await fetch_text_page_with_retry(
            client,
            url,
            max_chars=MAX_HTML_CHARS,
            require_nonempty=True,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            retryable_statuses={202, 401, 403, 429},
            log_event="jobbank104.list_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "104 Job Bank company page no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    if page is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"104 Job Bank listing fetch returned no page for {token!r}")
    _raise_if_bot_challenge(url, page)
    return page


def _validated_listing(page: str, token: str) -> tuple[set[str], bool]:
    urls = _parse_listing(page, token)
    advertised = _advertised_count(page)
    if not urls and not _explicitly_advertises_zero_jobs(page):
        raise ValueError(f"104 Job Bank company {token!r} returned a non-listing page")

    truncated = len(page) >= MAX_HTML_CHARS or len(urls) > MAX_JOBS
    if advertised is None or advertised != len(urls):
        # Fail safely: retain what the public page returned but prevent gone
        # detection from tombstoning jobs when the count disappears, the
        # provider paginates, or a larger employer is only partially rendered.
        truncated = True
    if len(urls) > MAX_JOBS:
        urls = set(sorted(urls)[:MAX_JOBS])
    return urls, truncated


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover canonical job URLs from one 104 employer page."""
    _ = pw
    metadata = board.get("metadata") or {}
    token = _resolve_token(board["board_url"], metadata)
    if token is None:
        raise ValueError(
            f"Cannot derive 104 Job Bank company token from {board['board_url']!r} "
            "and no valid token is present in metadata"
        )

    page = await _fetch_listing(token, client)
    urls, truncated = _validated_listing(page, token)
    log.info(
        "jobbank104.discovered",
        token=token,
        jobs=len(urls),
        advertised_jobs=_advertised_count(page),
        truncated=truncated,
    )
    return truncated_url_result(urls) if truncated else urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize an exact public 104 company URL and verify when reachable.

    Direct 104 URLs are provider-specific enough to identify without guessing.
    If a probe client is blocked by Cloudflare, retain the deterministic match
    so operators can select the monitor with ``proxy: true``.
    """
    _ = pw
    token = _token_from_url(url)
    if token is None:
        return None
    result: dict = {"token": token}
    if client is None:
        return result
    try:
        page = await _fetch_listing(token, client)
        urls, _truncated = _validated_listing(page, token)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("jobbank104.probe_failed", token=token, exc_info=True)
        return result
    result["jobs"] = len(urls)
    return result


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    token = _resolve_token(board_url, metadata)
    if token is None:
        return
    from src.shared.http import client_for

    async with client_for(client, metadata) as routed_client:
        await save_text_response(
            artifact_dir,
            routed_client,
            _listing_url(token),
            filename="jobbank104-listing.html",
            follow_redirects=False,
        )


register("jobbank104", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
