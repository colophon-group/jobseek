"""Gupy public career-page monitor.

Gupy embeds the complete public job inventory in ``__NEXT_DATA__`` at
``https://{tenant}.gupy.io/``. This URL-only adapter deliberately reuses
Jobseek's NextData extraction and URL-building machinery; the existing
JSON-LD scraper owns detail extraction on the normal scrape schedule.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.dom import _raise_if_bot_challenge
from src.core.monitors.nextdata import _extract_urls
from src.core.monitors.raw import save_text_response
from src.shared.gupy import gupy_tenant_from_url, normalize_gupy_tenant
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.nextdata import extract_next_data, resolve_path
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_HTML_CHARS = 5_000_000

_JOB_ID_RE = re.compile(r"^[1-9]\d{0,19}$")
_PAGE_PATTERNS = [
    re.compile(
        r"https?://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.gupy\.io"
        r"(?:/jobs/[1-9]\d{0,19})?/?"
        r"(?:\?jobBoardSource=gupy_public_page)?(?=[#\"'<\s]|$)",
        re.IGNORECASE,
    )
]
_GONE_STATUSES = frozenset({404, 410})


def _listing_url(tenant: str) -> str:
    return f"https://{tenant}.gupy.io/"


def _canonical_job_url(url: str, tenant: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != f"{tenant}.gupy.io"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 2 or segments[0].lower() != "jobs":
        return None
    if _JOB_ID_RE.fullmatch(segments[1]) is None:
        return None
    return f"https://{tenant}.gupy.io/jobs/{segments[1]}"


async def _fetch_listing(tenant: str, client: httpx.AsyncClient) -> str:
    url = _listing_url(tenant)
    try:
        page = await fetch_text_page_with_retry(
            client,
            url,
            max_chars=MAX_HTML_CHARS,
            require_nonempty=True,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            retryable_statuses={202, 401, 403},
            log_event="gupy.list_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError("Gupy board no longer exists", url=url) from exc
        raise
    if page is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"Gupy listing fetch returned no page for {tenant!r}")
    _raise_if_bot_challenge(url, page)
    return page


def _parse_listing(page: str, tenant: str) -> tuple[set[str], int]:
    data = extract_next_data(page)
    page_props = resolve_path(data or {}, "props.pageProps")
    if not isinstance(page_props, dict):
        raise ValueError(f"Gupy tenant {tenant!r} returned no NextData page props")
    if normalize_gupy_tenant(page_props.get("subdomain")) != tenant:
        raise ValueError(f"Gupy tenant {tenant!r} returned mismatched NextData")
    if not isinstance(page_props.get("careerPage"), dict):
        raise ValueError(f"Gupy tenant {tenant!r} returned no career-page metadata")
    items = page_props.get("jobs")
    if not isinstance(items, list):
        raise ValueError(f"Gupy tenant {tenant!r} returned no jobs array")

    candidate_urls = _extract_urls(
        items,
        f"https://{tenant}.gupy.io/jobs/{{id}}",
        None,
    )
    urls = {
        canonical
        for url in candidate_urls
        if (canonical := _canonical_job_url(url, tenant)) is not None
    }
    return urls, len(items)


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover canonical Gupy detail URLs from the shared NextData parser."""
    _ = pw
    metadata = board.get("metadata") or {}
    tenant = normalize_gupy_tenant(metadata.get("tenant")) or gupy_tenant_from_url(
        board["board_url"]
    )
    if tenant is None:
        raise ValueError(
            f"Cannot derive Gupy tenant from board URL {board['board_url']!r} "
            "and no valid tenant is present in metadata"
        )

    page = await _fetch_listing(tenant, client)
    urls, item_count = _parse_listing(page, tenant)
    truncated = len(page) >= MAX_HTML_CHARS or item_count > MAX_JOBS or len(urls) != item_count
    log_method = log.warning if truncated else log.info
    log_method(
        "gupy.discovered",
        tenant=tenant,
        jobs=len(urls),
        items=item_count,
        truncated=truncated,
    )
    return truncated_url_result(urls) if truncated else urls


async def _probe_tenant(tenant: str, client: httpx.AsyncClient) -> ProbeResult:
    normalized = normalize_gupy_tenant(tenant)
    if normalized is None:
        return False, None
    tenant = normalized
    try:
        page = await _fetch_listing(tenant, client)
        urls, item_count = _parse_listing(page, tenant)
        if len(urls) != item_count:
            raise ValueError("Gupy jobs array contains invalid or duplicate identifiers")
    except TDMReservedError:
        raise
    except Exception:
        log.debug("gupy.probe_failed", tenant=tenant, exc_info=True)
        return False, None
    return True, item_count


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
    result: dict = {"tenant": tenant.lower()}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect only direct or explicitly linked Gupy public boards."""
    _ = pw
    if (
        gupy_tenant_from_url(url) is None
        and gupy_tenant_from_url(url, validate_query=False) is not None
    ):
        return None
    return await ats_can_handle(
        url,
        client,
        monitor_name="gupy",
        token_from_url=gupy_tenant_from_url,
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
    tenant = normalize_gupy_tenant(metadata.get("tenant")) or gupy_tenant_from_url(board_url)
    if tenant is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        _listing_url(tenant),
        filename="gupy-listing.html",
        follow_redirects=False,
    )


register("gupy", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
