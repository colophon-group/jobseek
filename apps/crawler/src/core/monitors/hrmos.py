"""HRMOS public career-page monitor.

HRMOS serves server-rendered listings at
``https://hrmos.co/pages/{tenant}/jobs`` with at most 100 jobs per page. This
URL-only adapter reuses the generic static link extractor and shared strict
retry; the existing JSON-LD scraper owns detail extraction on the normal
scrape schedule.
"""

from __future__ import annotations

import math
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

PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
MAX_HTML_CHARS = 2_000_000

_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PAGE_PATTERNS = [
    re.compile(
        r"https?://hrmos\.co/pages/([a-z0-9][a-z0-9_-]{0,62})/jobs"
        r"(?:/[A-Za-z0-9_-]{1,64})?/?(?=[#\"'<\s]|$)",
        re.IGNORECASE,
    )
]
_LISTING_MARKER_RE = re.compile(r"\bid=[\"']jsi-joblist[\"']", re.IGNORECASE)
_COUNT_RE = re.compile(r"全\s*([\d,]+)\s*件中\s*([\d,]+)\s*件")
_CURRENT_PAGE_RE = re.compile(
    r"class=[\"'][^\"']*\bcurrent\b[^\"']*[\"'][^>]*>\s*(\d+)\s*<",
    re.IGNORECASE,
)
_PAGE_PARAM_RE = re.compile(r"[?&](?:amp;)?page=(\d+)(?=[&#\"'\s]|$)", re.IGNORECASE)
_GONE_STATUSES = frozenset({404, 410})


def _normalize_tenant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tenant = value.strip().lower()
    return tenant if _TENANT_RE.fullmatch(tenant) else None


def _tenant_from_url(url: str, *, validate_query: bool = True) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "hrmos.co"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or (validate_query and parsed.query)
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) not in {3, 4} or segments[0].lower() != "pages":
        return None
    if segments[2].lower() != "jobs":
        return None
    tenant = _normalize_tenant(segments[1])
    if tenant is None:
        return None
    if len(segments) == 4 and _JOB_ID_RE.fullmatch(segments[3]) is None:
        return None
    return tenant


def _listing_url(tenant: str, page_number: int = 1) -> str:
    base = f"https://hrmos.co/pages/{tenant}/jobs"
    return base if page_number == 1 else f"{base}?page={page_number}"


def _job_matcher(tenant: str) -> re.Pattern[str]:
    return re.compile(
        rf"^https://hrmos\.co/pages/{re.escape(tenant)}/jobs/"
        r"[A-Za-z0-9_-]{1,64}(?:[/?#]|$)",
        re.IGNORECASE,
    )


def _canonical_job_url(url: str, tenant: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "hrmos.co"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) != 4
        or segments[0].lower() != "pages"
        or segments[1].lower() != tenant
        or segments[2].lower() != "jobs"
        or _JOB_ID_RE.fullmatch(segments[3]) is None
    ):
        return None
    return f"https://hrmos.co/pages/{tenant}/jobs/{segments[3]}"


def _parse_listing(page: str, tenant: str) -> set[str]:
    raw_urls = _extract_links_static(page, _listing_url(tenant), _job_matcher(tenant))
    return {
        canonical for url in raw_urls if (canonical := _canonical_job_url(url, tenant)) is not None
    }


def _page_metadata(page: str) -> tuple[int, int, int | None, int]:
    """Return total jobs, displayed jobs, current page, and linked page maximum."""
    count = _COUNT_RE.search(page)
    if count is None:
        raise ValueError("HRMOS listing count marker is missing")
    total = int(count.group(1).replace(",", ""))
    displayed = int(count.group(2).replace(",", ""))
    current_match = _CURRENT_PAGE_RE.search(page)
    current = int(current_match.group(1)) if current_match else None
    linked_page_max = max((int(value) for value in _PAGE_PARAM_RE.findall(page)), default=1)
    return total, displayed, current, linked_page_max


async def _fetch_listing(
    tenant: str,
    page_number: int,
    client: httpx.AsyncClient,
) -> str:
    url = _listing_url(tenant, page_number)
    try:
        page = await fetch_text_page_with_retry(
            client,
            url,
            max_chars=MAX_HTML_CHARS,
            require_nonempty=True,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            retryable_statuses={202, 401, 403},
            log_event="hrmos.list_backoff",
        )
    except PaginationFetchError as exc:
        if page_number == 1 and exc.last_status in _GONE_STATUSES:
            raise BoardGoneError("HRMOS board no longer exists", url=url) from exc
        raise
    if page is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"HRMOS listing fetch returned no page for {tenant!r}")
    _raise_if_bot_challenge(url, page)
    if _LISTING_MARKER_RE.search(page) is None:
        raise ValueError(f"HRMOS tenant {tenant!r} returned a non-listing page")
    return page


async def _discover_pages(
    tenant: str,
    client: httpx.AsyncClient,
) -> tuple[set[str], bool, int]:
    first_page = await _fetch_listing(tenant, 1, client)
    total_jobs, displayed, current, linked_page_max = _page_metadata(first_page)
    if current not in {None, 1}:
        raise ValueError(f"HRMOS tenant {tenant!r} returned page {current} for page 1")
    if total_jobs > 0 and displayed == 0:
        raise ValueError(f"HRMOS tenant {tenant!r} advertised jobs but returned none")

    advertised_pages = max(
        1,
        linked_page_max,
        math.ceil(total_jobs / max(displayed, 1)),
    )
    urls: set[str] = set()
    truncated = advertised_pages > MAX_PAGES or total_jobs > MAX_JOBS

    def merge_page(page_number: int, page: str) -> None:
        nonlocal truncated
        page_total, page_displayed, page_current, _linked_max = _page_metadata(page)
        if page_current is not None and page_current != page_number:
            raise ValueError(
                f"HRMOS tenant {tenant!r} returned page {page_current} for page {page_number}"
            )
        page_urls = _parse_listing(page, tenant)
        if page_number > 1 and not page_urls:
            raise ValueError(
                f"HRMOS tenant {tenant!r} returned an empty advertised page {page_number}"
            )
        if page_total != total_jobs or page_displayed != len(page_urls):
            truncated = True
        if urls.intersection(page_urls):
            truncated = True
        urls.update(page_urls)
        if len(page) >= MAX_HTML_CHARS:
            truncated = True

    merge_page(1, first_page)
    page_limit = min(advertised_pages, MAX_PAGES)
    for page_number in range(2, page_limit + 1):
        if len(urls) >= MAX_JOBS:
            truncated = True
            break
        # Sequential requests are intentionally gentle on the shared HRMOS
        # origin and allow each potentially large HTML body to be discarded
        # immediately after parsing.
        page = await _fetch_listing(tenant, page_number, client)
        merge_page(page_number, page)

    if len(urls) != total_jobs:
        truncated = True
    return urls, truncated, advertised_pages


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover canonical HRMOS detail URLs with complete bounded pagination."""
    _ = pw
    metadata = board.get("metadata") or {}
    tenant = _normalize_tenant(metadata.get("tenant")) or _tenant_from_url(board["board_url"])
    if tenant is None:
        raise ValueError(
            f"Cannot derive HRMOS tenant from board URL {board['board_url']!r} "
            "and no valid tenant is present in metadata"
        )

    urls, truncated, pages = await _discover_pages(tenant, client)
    log_method = log.warning if truncated else log.info
    log_method(
        "hrmos.discovered",
        tenant=tenant,
        jobs=len(urls),
        pages=pages,
        truncated=truncated,
    )
    return truncated_url_result(urls) if truncated else urls


async def _probe_tenant(tenant: str, client: httpx.AsyncClient) -> ProbeResult:
    try:
        page = await _fetch_listing(tenant, 1, client)
        total, displayed, _current, _linked_max = _page_metadata(page)
        if total > 0 and (displayed == 0 or not _parse_listing(page, tenant)):
            raise ValueError("HRMOS listing advertised jobs without detail links")
    except TDMReservedError:
        raise
    except Exception:
        log.debug("hrmos.probe_failed", tenant=tenant, exc_info=True)
        return False, None
    return True, total


async def _fetch_job_count(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_tenant(token, client)
    return count if found else None


async def _probe_candidate(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_tenant(token, client)


def _build_result(tenant: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    result: dict = {"tenant": tenant}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect only direct or explicitly linked HRMOS public boards."""
    _ = pw
    if _tenant_from_url(url) is None and _tenant_from_url(url, validate_query=False) is not None:
        return None
    return await ats_can_handle(
        url,
        client,
        monitor_name="hrmos",
        token_from_url=_tenant_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=frozenset(),
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_candidate,
        initial_context=None,
        result_builder=_build_result,
        page_token_probe=_probe_candidate,
        require_direct_count=True,
        allow_slug_guess=False,
        log_token_field="tenant",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    tenant = _normalize_tenant(metadata.get("tenant")) or _tenant_from_url(board_url)
    if tenant is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        _listing_url(tenant),
        filename="hrmos-listing.html",
        follow_redirects=False,
    )


register("hrmos", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
