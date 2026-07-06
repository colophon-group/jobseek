"""SmartRecruiters Posting API monitor.

Public API:
  List:   GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=0

The list endpoint returns posting IDs and metadata.  The monitor constructs
posting URLs from the token + ID and returns a URL set.  Detail fetching
is handled by the scraper on the daily schedule.
"""

from __future__ import annotations

import asyncio
import random
import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import register, slugs_from_url
from src.shared.http_retry import PaginationFetchError, is_retryable_status
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
PAGE_SIZE = 100

# Pagination retry budget. Symmetric with workday (#2748), lever (#2749),
# api_sniffer (#2733), accenture (#2735) and PCSX (#2734): 3 total
# attempts, exponential backoff with full jitter starting at 0.5s.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5

_PAGE_PATTERNS = [
    re.compile(r"api\.smartrecruiters\.com/v1/companies/([\w-]+)"),
    re.compile(r"jobs\.smartrecruiters\.com/([\w-]+)"),
    re.compile(r"careers\.smartrecruiters\.com/([\w-]+)"),
]

_IGNORE_TOKENS = frozenset({"api", "v1", "js", "css", "assets", "postings", "companies"})
_HTML_SIGNAL_RE = re.compile(r"\b(?:smartrecruiters\.com|smartrecruiters)\b", re.IGNORECASE)


def _is_smartrecruiters_host(host: str) -> bool:
    return host == "smartrecruiters.com" or host.endswith(".smartrecruiters.com")


def _has_smartrecruiters_signal(url: str, html: str | None) -> bool:
    """Return True when URL or page HTML indicates SmartRecruiters presence."""
    host = (urlparse(url).hostname or "").lower()
    if _is_smartrecruiters_host(host):
        return True
    if not html:
        return False
    return bool(_HTML_SIGNAL_RE.search(html))


def _token_from_url(board_url: str) -> str | None:
    """Extract company identifier from a SmartRecruiters URL."""
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(board_url)
        if match:
            token = match.group(1)
            if token not in _IGNORE_TOKENS:
                return token
    return None


def _api_list_url(token: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{token}/postings"


def _posting_url(token: str, posting_id: str) -> str:
    """Build a canonical posting URL from token + ID."""
    return f"https://jobs.smartrecruiters.com/{token}/{posting_id}"


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> dict:
    """GET a SmartRecruiters list-API page with bounded retries (#2749).

    Mirrors the contract used by ``fetch_with_retry`` (#2722) and the
    sibling monitor helpers (workday #2748, lever #2749, accenture
    #2735, PCSX #2734, api_sniffer #2733): retryable failures back off
    exponentially and, on budget exhaustion, raise
    :class:`PaginationFetchError` so the run is recorded as a failure
    rather than silently truncating to whatever pages happened to
    succeed.

    SmartRecruiters' end-of-pagination signal is
    ``offset >= totalFound or len(content) < PAGE_SIZE``. Pre-fix, a
    CDN/anti-bot incident dropping the body and returning ``200 {}``
    decoded to an empty dict whose ``totalFound`` defaulted to 0 and
    whose ``content`` defaulted to ``[]`` — the loop broke after the
    first page and the caller treated the partial discovery as
    success, which fed ``_MARK_GONE_BY_TIMESTAMP`` for the missing
    URLs (same shape as #2722 / #2737 / #2748).

    Retried:
      - HTTP 5xx (Cloudflare 520-526/530 included), 408, 425, 429.
      - Arbitrary network exceptions (timeout, connection reset, JSON
        parse error on a captcha/HTML body served as 200).
      - HTTP 200 with a body that decodes to a non-dict shape — e.g.,
        a CDN error envelope or ``null`` served as 200 (same shape as
        the workday ``null``-body guard).

    Fail-fast (non-retryable 4xx — auth-expired 401, misconfigured 400,
    forbidden 403, board-removed 404): raises
    :class:`PaginationFetchError` on the first attempt.

    Backoff: ``base_delay × 2^attempt × (0.5 + random())`` — exponential
    with full jitter, identical cadence to workday (#2748).
    """
    # Retry observability (#3210). Same counter as ``http_retry.py`` so
    # cross-monitor "retry storm" queries aggregate smartrecruiters in.
    from src.metrics import http_retry_attempts_total, http_retry_host
    from src.shared.tdm import TDMReservedError
    from src.shared.tdm import check_response as _tdm_check

    host = http_retry_host(url)

    last_exc: BaseException | None = None
    last_status: int | None = None
    retried = False

    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params)
            last_status = resp.status_code
            if resp.status_code == 200:
                # TDM-Reservation respect (#2842) — header-only check on
                # API endpoints (JSON body, no HTML meta to scan).
                _tdm_check(resp)
                # ``resp.json()`` may raise ``json.JSONDecodeError`` on a
                # captcha/HTML body served as 200 — falls into the
                # ``except Exception`` branch below, retried, then
                # surfaced as ``PaginationFetchError``. No silent break.
                data = resp.json()
                # SmartRecruiters' list endpoint returns a dict with
                # ``content`` / ``totalFound``. A body that decodes to
                # ``null`` or to a non-dict shape (e.g., an unexpected
                # error envelope) is treated as a transient failure:
                # retry, then raise. Without this guard, ``data.get(...)``
                # on a list/None silently falls back to defaults which
                # fake a legitimate end-of-pagination — same shape of
                # silent-break bug the issue (#2749) is fixing.
                if not isinstance(data, dict):
                    raise ValueError(
                        f"smartrecruiters list endpoint returned non-dict body: "
                        f"{type(data).__name__}"
                    )
                if retried:
                    http_retry_attempts_total.labels(host=host, outcome="recovered").inc()
                return data
            if is_retryable_status(resp.status_code):
                last_exc = None  # status-only, no exception
                http_retry_attempts_total.labels(host=host, outcome="retry").inc()
                retried = True
            else:
                # Non-retryable 4xx — fail fast. ``resp.raise_for_status``
                # would raise ``HTTPStatusError`` which the caller doesn't
                # uniformly handle; raise ``PaginationFetchError`` directly
                # for cross-monitor symmetry.
                raise PaginationFetchError(
                    url,
                    attempts=attempt + 1,
                    last_status=resp.status_code,
                )
        except (PaginationFetchError, TDMReservedError):
            raise
        except Exception as exc:  # noqa: BLE001 — timeout, network, JSON parse
            last_exc = exc
            last_status = None
            http_retry_attempts_total.labels(host=host, outcome="retry").inc()
            retried = True

        if attempt < retries - 1:
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "smartrecruiters.list_backoff",
                url=url,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                last_status=last_status,
                last_error=type(last_exc).__name__ if last_exc else None,
            )
            await asyncio.sleep(delay)

    http_retry_attempts_total.labels(host=host, outcome="exhausted").inc()
    raise PaginationFetchError(
        url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_exc).__name__ if last_exc else None,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch job listing URLs from the SmartRecruiters public API.

    Paginates the list endpoint and constructs posting URLs from token + ID.

    Failure semantics (#2749). Each page GET is wrapped by
    :func:`_get_page_with_retry`, which raises
    :class:`PaginationFetchError` on persistent transient failures or
    non-retryable 4xx. The exception propagates out of this function
    (no intervening try/except) and lands in
    ``_process_one_board_streaming``'s generic ``except Exception``,
    which records the run as a failure rather than a partial success
    — preventing ``_MARK_GONE_BY_TIMESTAMP`` from tombstoning the
    URLs that live on the unfetched pages (same shape of bug as
    #2722, #2737, #2748).

    Truncation semantics (#3216). When ``MAX_JOBS`` is reached the
    monitor returns a :class:`MonitorResult` with ``truncated=True``
    so the pipeline marks the cycle as partial and skips gone-detection
    — the unseen tail beyond the cap must not be tombstoned.
    """
    metadata = board.get("metadata") or {}
    token = metadata.get("token") or _token_from_url(board["board_url"])

    if not token:
        raise ValueError(
            f"Cannot derive SmartRecruiters token from board URL {board['board_url']!r} "
            "and no token in metadata"
        )

    urls: set[str] = set()
    offset = 0
    list_url = _api_list_url(token)
    truncated = False

    while True:
        data = await _get_page_with_retry(client, list_url, {"limit": PAGE_SIZE, "offset": offset})

        content = data.get("content", [])
        for item in content:
            pid = item.get("id")
            if pid:
                urls.add(_posting_url(token, str(pid)))

        total_found = data.get("totalFound", 0)
        offset += PAGE_SIZE

        if offset >= total_found or len(content) < PAGE_SIZE:
            break

        if len(urls) >= MAX_JOBS:
            log.warning(
                "smartrecruiters.truncated",
                token=token,
                total=len(urls),
                cap=MAX_JOBS,
            )
            truncated = True
            break

    log.info("smartrecruiters.listed", token=token, postings=len(urls))
    if truncated:
        return truncated_url_result(urls)
    return urls


async def _probe_token(token: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the SmartRecruiters API for a token. Returns (found, job_count)."""
    try:
        resp = await client.get(
            _api_list_url(token),
            params={"limit": 1, "offset": 0},
        )
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        total = data.get("totalFound")
        if isinstance(total, int):
            return True, total
        # Check if content exists at all
        content = data.get("content")
        if isinstance(content, list):
            return True, len(content)
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(token: str, client: httpx.AsyncClient) -> int | None:
    """Lightweight API call to get the job count for a token."""
    try:
        resp = await client.get(
            _api_list_url(token),
            params={"limit": 1, "offset": 0},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("totalFound")
        return total if isinstance(total, int) else None
    except Exception:
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect SmartRecruiters: URL pattern -> page HTML scan -> slug-based API probe."""
    token = _token_from_url(url)
    if token:
        if client is None:
            return {"token": token}

        # Validate direct SmartRecruiters URLs against the API and final redirect URL.
        final_url = url
        html: str | None = None
        try:
            resp = await client.get(url, follow_redirects=True)
            final_url = str(resp.url)
            if resp.status_code == 200:
                html = resp.text
        except Exception:
            pass

        final_token = _token_from_url(final_url)
        if final_token:
            token = final_token
        elif not _has_smartrecruiters_signal(final_url, html):
            return None

        count = await _fetch_job_count(token, client)
        if count is None:
            return None
        return {"token": token, "jobs": count}

    if client is None:
        return None

    final_url = url
    html: str | None = None
    try:
        resp = await client.get(url, follow_redirects=True)
        final_url = str(resp.url)
        if resp.status_code == 200:
            html = resp.text
    except Exception:
        pass

    signal = _has_smartrecruiters_signal(final_url, html)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    log.info("smartrecruiters.detected_in_page", url=url, board_token=found)
                    count = await _fetch_job_count(found, client)
                    if count is not None:
                        return {"token": found, "jobs": count}

    if not signal:
        return None

    for slug in slugs_from_url(url):
        found, count = await _probe_token(slug, client)
        if found:
            log.info("smartrecruiters.detected_by_probe", url=url, board_token=slug)
            result: dict[str, str | int] = {"token": slug}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("smartrecruiters", discover, cost=10, can_handle=can_handle, rich=False)
