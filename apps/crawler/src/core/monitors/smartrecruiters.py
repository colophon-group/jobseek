"""SmartRecruiters Posting API monitor.

Public API:
  List:   GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=0

The list endpoint returns posting IDs and metadata.  The monitor constructs
posting URLs from the token + ID and returns a URL set.  Detail fetching
is handled by the scraper on the daily schedule.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import register, slugs_from_url
from src.shared.http_retry import fetch_json_page_with_retry
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
    """GET a SmartRecruiters list-API page with bounded retries (#2749)."""
    return await fetch_json_page_with_retry(
        client,
        url,
        params=params,
        expect_shape=dict,
        retries=retries,
        base_delay=base_delay,
        log_event="smartrecruiters.list_backoff",
        sleep=asyncio.sleep,
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
            # Network failures leave only the original URL token to validate below.
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
        # Fetch failures mean no page signal was observed for this URL.
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
