"""Workable Posting API monitor.

Public APIs:
  List:      POST https://apply.workable.com/api/v3/accounts/{slug}/jobs
  Fallback:  GET  https://apply.workable.com/{slug}/llms.txt

The list endpoint returns metadata (title, location, department) but not the
full job description.  The monitor discovers job URLs only; a dedicated
scraper (``src/core/scrapers/workable``) fetches details on the daily
scrape schedule.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog

from src.core.monitors import (
    register,
    slug_guess_allowed,
)
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.shared.http_retry import (
    PaginationFetchError,
    fetch_json_page_with_retry,
    fetch_text_page_with_retry,
)
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
_RETRY_ATTEMPTS = 4
_RETRY_BASE_DELAY = 5.0
_MARKDOWN_RETRY_ATTEMPTS = 3
_MARKDOWN_RETRY_BASE_DELAY = 0.5

_PAGE_PATTERNS = [
    re.compile(r"apply\.workable\.com/([\w-]+)"),
]

_IGNORE_TOKENS = frozenset({"api", "v2", "v3", "js", "css", "assets", "accounts", "jobs"})


def _token_from_url(board_url: str) -> str | None:
    """Extract company slug from a Workable URL."""
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(board_url)
        if match:
            token = match.group(1)
            if token not in _IGNORE_TOKENS:
                return token
    return None


def _api_list_url(slug: str) -> str:
    return f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"


def _job_url(slug: str, shortcode: str) -> str:
    return f"https://apply.workable.com/{slug}/j/{shortcode}/"


def _llms_url(slug: str) -> str:
    return f"https://apply.workable.com/{slug}/llms.txt"


def _parse_markdown_count(markdown: str) -> int:
    match = re.search(r"All open roles .*?:\s*([\d,]+) current openings\b", markdown)
    if match is None:
        raise ValueError("Workable llms.txt did not advertise a current-opening count")
    return int(match.group(1).replace(",", ""))


async def _markdown_empty(slug: str, client: httpx.AsyncClient) -> bool:
    """Verify the one authoritative fallback state exposed by ``llms.txt``.

    A positive Workable ``jobs.md`` request returns search facets rather than
    an unfiltered inventory, so it cannot safely replace the list API. Only an
    explicit advertised zero is accepted; positive counts fail closed.
    """
    llms = await fetch_text_page_with_retry(
        client,
        _llms_url(slug),
        retries=_MARKDOWN_RETRY_ATTEMPTS,
        base_delay=_MARKDOWN_RETRY_BASE_DELAY,
        end_of_pagination_statuses=(),
        require_nonempty=True,
        log_event="workable.markdown_index_backoff",
        sleep=asyncio.sleep,
    )
    assert llms is not None
    advertised = _parse_markdown_count(llms)
    if advertised == 0:
        log.info("workable.markdown_empty", slug=slug)
        return True
    raise ValueError(
        "Workable list API was rate limited and llms.txt advertises "
        f"{advertised} current openings; positive markdown inventories are not authoritative"
    )


async def _api_list(slug: str, client: httpx.AsyncClient) -> tuple[set[str], bool, bool]:
    """Paginate the list endpoint to collect all job URLs.

    Returns ``(urls, truncated, verified_empty)``. ``truncated`` is True iff
    the MAX_JOBS cap was hit before pagination completed; the pipeline uses
    the flag to suppress gone-detection on this cycle (#3216).
    """
    urls: set[str] = set()
    truncated = False
    body: dict = {"query": "", "location": [], "department": [], "worktype": []}

    while True:
        try:
            data = await fetch_json_page_with_retry(
                client,
                _api_list_url(slug),
                method="POST",
                json_body=body,
                expect_shape=dict,
                retries=_RETRY_ATTEMPTS,
                base_delay=_RETRY_BASE_DELAY,
                log_event="workable.list_backoff",
                sleep=asyncio.sleep,
            )
        except PaginationFetchError as exc:
            if exc.last_status != 429 or urls:
                raise
            log.warning("workable.rate_limited_markdown_fallback", slug=slug)
            verified_empty = await _markdown_empty(slug, client)
            return set(), False, verified_empty

        results = data.get("results", [])
        for item in results:
            sc = item.get("shortcode")
            if sc:
                urls.add(_job_url(slug, sc))

        next_page = data.get("nextPage")
        if not next_page or not results:
            break

        body["token"] = next_page

        if len(urls) >= MAX_JOBS:
            log.warning("workable.truncated", slug=slug, total=len(urls), cap=MAX_JOBS)
            truncated = True
            break

    return urls, truncated, False


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover job URLs from the Workable public API.

    Paginates the list endpoint to collect all job URLs.
    Details are fetched by the workable scraper on the daily schedule.
    """
    metadata = board.get("metadata") or {}
    slug = metadata.get("token") or _token_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Workable slug from board URL {board['board_url']!r} "
            "and no token in metadata"
        )

    urls, truncated, verified_empty = await _api_list(slug, client)
    log.info("workable.listed", slug=slug, postings=len(urls))

    if verified_empty:
        from src.core.monitor import MonitorResult

        return MonitorResult(
            urls=set(),
            verified_empty_reason="Workable llms.txt advertises zero current openings",
        )
    if truncated:
        return truncated_url_result(urls)
    return urls


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Workable API for a slug. Returns (found, job_count)."""
    try:
        body = {"query": "", "location": [], "department": [], "worktype": []}
        resp = await client.post(_api_list_url(slug), json=body)
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        total = data.get("total")
        if isinstance(total, int):
            return True, total
        results = data.get("results")
        if isinstance(results, list):
            return True, len(results)
        return False, None
    except Exception:
        log.debug(
            "workable.probe_failed",
            probe="slug",
            slug=slug,
            url=_api_list_url(slug),
            exc_info=True,
        )
        return False, None


async def _fetch_job_count(slug: str, client: httpx.AsyncClient) -> int | None:
    """Lightweight API call to get the job count for a slug."""
    try:
        body = {"query": "", "location": [], "department": [], "worktype": []}
        resp = await client.post(_api_list_url(slug), json=body)
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("total")
        return total if isinstance(total, int) else None
    except Exception:
        log.debug(
            "workable.probe_failed",
            probe="job_count",
            slug=slug,
            url=_api_list_url(slug),
            exc_info=True,
        )
        return None


async def _fetch_template_count(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    return await _fetch_job_count(token, client)


async def _probe_template_slug(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_slug(token, client)


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Workable: URL pattern -> page HTML scan -> slug-based API probe."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="workable",
        token_from_url=_token_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=_IGNORE_TOKENS,
        fetch_job_count=_fetch_template_count,
        api_probe=_probe_template_slug,
        initial_context=None,
        allow_slug_guess=slug_guess_allowed(),
    )


register("workable", discover, cost=10, can_handle=can_handle, rich=False)
