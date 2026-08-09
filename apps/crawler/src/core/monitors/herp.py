"""HERP Hire public career-page monitor.

HERP serves every open requisition in one server-rendered listing at
``https://herp.careers/v1/{slug}``. This URL-only adapter reuses the generic
static link extractor and shared strict retry; the existing JSON-LD scraper
owns detail extraction on the normal scrape schedule.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.dom import _extract_links_static, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_HTML_CHARS = 2_000_000

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_~-]{6,64}$")
_PAGE_PATTERNS = [
    re.compile(
        r"https?://herp\.careers/v1/([a-z0-9][a-z0-9_-]{0,62})"
        r"(?:/[A-Za-z0-9_~-]{6,64})?/?(?=[#\"'<\s]|$)",
        re.IGNORECASE,
    )
]
_LISTING_MARKER_RE = re.compile(r"class=[\"']requisition-list[\"']", re.IGNORECASE)
_GONE_STATUSES = frozenset({404, 410})


def _normalize_slug(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    slug = value.strip().lower()
    return slug if _SLUG_RE.fullmatch(slug) else None


def _slug_from_url(url: str, *, validate_query: bool = True) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "herp.careers"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or (validate_query and parsed.query)
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) not in {2, 3} or segments[0].lower() != "v1":
        return None
    slug = _normalize_slug(segments[1])
    if slug is None:
        return None
    if len(segments) == 3 and _JOB_ID_RE.fullmatch(segments[2]) is None:
        return None
    return slug


def _listing_url(slug: str) -> str:
    return f"https://herp.careers/v1/{slug}"


def _job_matcher(slug: str) -> re.Pattern[str]:
    return re.compile(
        rf"^https://herp\.careers/v1/{re.escape(slug)}/"
        r"[A-Za-z0-9_~-]{6,64}(?:[/?#]|$)",
        re.IGNORECASE,
    )


def _canonical_job_url(url: str, slug: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "herp.careers"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) != 3
        or segments[:2] != ["v1", slug]
        or _JOB_ID_RE.fullmatch(segments[2]) is None
    ):
        return None
    return f"https://herp.careers/v1/{slug}/{segments[2]}"


async def _fetch_listing(slug: str, client: httpx.AsyncClient) -> str:
    url = _listing_url(slug)
    try:
        page = await fetch_text_page_with_retry(
            client,
            url,
            max_chars=MAX_HTML_CHARS,
            require_nonempty=True,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            retryable_statuses={202, 401, 403},
            log_event="herp.list_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "HERP board no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    if page is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"HERP listing fetch returned no page for {slug!r}")
    _raise_if_bot_challenge(url, page)
    if _LISTING_MARKER_RE.search(page) is None:
        raise ValueError(f"HERP slug {slug!r} returned a non-listing page")
    return page


def _parse_listing(page: str, slug: str) -> set[str]:
    raw_urls = _extract_links_static(page, _listing_url(slug), _job_matcher(slug))
    return {
        canonical for url in raw_urls if (canonical := _canonical_job_url(url, slug)) is not None
    }


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover canonical HERP detail URLs from one static listing."""
    _ = pw
    metadata = board.get("metadata") or {}
    slug = _normalize_slug(metadata.get("slug")) or _slug_from_url(board["board_url"])
    if slug is None:
        raise ValueError(
            f"Cannot derive HERP slug from board URL {board['board_url']!r} "
            "and no valid slug is present in metadata"
        )

    page = await _fetch_listing(slug, client)
    urls = _parse_listing(page, slug)
    truncated = len(page) >= MAX_HTML_CHARS or len(urls) > MAX_JOBS
    if len(urls) > MAX_JOBS:
        urls = set(sorted(urls)[:MAX_JOBS])
    if truncated:
        log.warning(
            "herp.truncated",
            slug=slug,
            jobs=len(urls),
            html_chars=len(page),
            job_cap=MAX_JOBS,
            html_cap=MAX_HTML_CHARS,
        )
        return truncated_url_result(urls)
    log.info("herp.discovered", slug=slug, jobs=len(urls))
    return urls


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> ProbeResult:
    try:
        page = await _fetch_listing(slug, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("herp.probe_failed", slug=slug, exc_info=True)
        return False, None
    return True, len(_parse_listing(page, slug))


async def _fetch_job_count(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_slug(token, client)
    return count if found else None


async def _probe_candidate(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_slug(token, client)


def _build_result(slug: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    result: dict = {"slug": slug}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked HERP public boards."""
    _ = pw
    if _slug_from_url(url) is None and _slug_from_url(url, validate_query=False) is not None:
        return None
    return await ats_can_handle(
        url,
        client,
        monitor_name="herp",
        token_from_url=_slug_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=frozenset(),
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_candidate,
        initial_context=None,
        result_builder=_build_result,
        page_token_probe=_probe_candidate,
        require_direct_count=True,
        allow_slug_guess=False,
        log_token_field="slug",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    slug = _normalize_slug(metadata.get("slug")) or _slug_from_url(board_url)
    if slug is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        _listing_url(slug),
        filename="herp-listing.html",
        follow_redirects=False,
    )


register("herp", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
