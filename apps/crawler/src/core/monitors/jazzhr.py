"""JazzHR public career-page monitor.

JazzHR tenants serve every open job in one server-rendered listing at
``https://{tenant}.applytojob.com/apply/jobs``.  This adapter keeps the
provider-specific surface deliberately small: shared HTTP retry owns transport
semantics and the generic DOM monitor owns link extraction and bot-challenge
detection.
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
MAX_HTML_CHARS = 5_000_000

_TENANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DIRECT_PATH_RE = re.compile(
    r"^(?:/apply(?:/jobs(?:/details/[A-Za-z0-9_-]+)?)?)?/?$",
    re.IGNORECASE,
)
_PAGE_PATTERNS = [
    re.compile(
        r"https?://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.applytojob\.com"
        r"(?:/apply(?:/jobs(?:/details/[A-Za-z0-9_-]+)?)?)?/?"
        r"(?=[?#\"'<\s]|$)",
        re.IGNORECASE,
    )
]
_LISTING_MARKER = 'id="job_listings_wrapper"'
_GONE_STATUSES = frozenset({404, 410})


def _normalize_tenant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tenant = value.strip().lower()
    return tenant if _TENANT_RE.fullmatch(tenant) else None


def _tenant_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    suffix = ".applytojob.com"
    if (
        parsed.scheme != "https"
        or not host.endswith(suffix)
        or host.count(".") != 2
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or _DIRECT_PATH_RE.fullmatch(parsed.path) is None
    ):
        return None
    return _normalize_tenant(host.removesuffix(suffix))


def _listing_url(tenant: str) -> str:
    return f"https://{tenant}.applytojob.com/apply/jobs"


def _job_matcher(tenant: str) -> re.Pattern[str]:
    return re.compile(
        rf"^https://{re.escape(tenant)}\.applytojob\.com/"
        r"apply/jobs/details/[A-Za-z0-9_-]+(?:[/?#]|$)",
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
        or (parsed.hostname or "").lower() != f"{tenant}.applytojob.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 4 or segments[:3] != ["apply", "jobs", "details"]:
        return None
    job_id = segments[3]
    if re.fullmatch(r"[A-Za-z0-9_-]+", job_id) is None:
        return None
    return f"https://{tenant}.applytojob.com/apply/jobs/details/{job_id}"


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
            log_event="jazzhr.list_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError("JazzHR board no longer exists", url=url) from exc
        raise
    if page is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"JazzHR listing fetch returned no page for {tenant!r}")
    _raise_if_bot_challenge(url, page)
    if _LISTING_MARKER not in page:
        raise ValueError(f"JazzHR tenant {tenant!r} returned a non-listing page")
    return page


def _parse_listing(page: str, tenant: str) -> set[str]:
    raw_urls = _extract_links_static(page, _listing_url(tenant), _job_matcher(tenant))
    return {
        canonical for url in raw_urls if (canonical := _canonical_job_url(url, tenant)) is not None
    }


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover canonical JazzHR detail URLs from one static listing."""
    _ = pw
    metadata = board.get("metadata") or {}
    tenant = _normalize_tenant(metadata.get("tenant")) or _tenant_from_url(board["board_url"])
    if tenant is None:
        raise ValueError(
            f"Cannot derive JazzHR tenant from board URL {board['board_url']!r} "
            "and no valid tenant is present in metadata"
        )

    page = await _fetch_listing(tenant, client)
    urls = _parse_listing(page, tenant)
    truncated = len(page) >= MAX_HTML_CHARS or len(urls) > MAX_JOBS
    if len(urls) > MAX_JOBS:
        urls = set(sorted(urls)[:MAX_JOBS])
    if truncated:
        log.warning(
            "jazzhr.truncated",
            tenant=tenant,
            jobs=len(urls),
            html_chars=len(page),
            job_cap=MAX_JOBS,
            html_cap=MAX_HTML_CHARS,
        )
        return truncated_url_result(urls)
    log.info("jazzhr.discovered", tenant=tenant, jobs=len(urls))
    return urls


async def _probe_tenant(tenant: str, client: httpx.AsyncClient) -> ProbeResult:
    try:
        page = await _fetch_listing(tenant, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("jazzhr.probe_failed", tenant=tenant, exc_info=True)
        return False, None
    return True, len(_parse_listing(page, tenant))


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
    """Detect direct or explicitly linked JazzHR public tenants."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="jazzhr",
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
        filename="jazzhr-listing.html",
        follow_redirects=True,
    )


register("jazzhr", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
