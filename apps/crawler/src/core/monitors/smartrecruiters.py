"""SmartRecruiters Posting API monitor.

Public API:
  List:   GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=0

The list endpoint returns posting IDs and metadata.  The monitor constructs
posting URLs from the token + ID and returns a URL set.  Detail fetching
is handled by the scraper on the daily schedule.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000
PAGE_SIZE = 100

_PAGE_PATTERNS = [
    re.compile(r"api\.smartrecruiters\.com/v1/companies/([\w-]+)"),
    re.compile(r"jobs\.smartrecruiters\.com/([\w-]+)"),
    re.compile(r"careers\.smartrecruiters\.com/([\w-]+)"),
]

_IGNORE_TOKENS = frozenset({"api", "v1", "js", "css", "assets", "postings", "companies"})


def _has_smartrecruiters_signal(url: str, html: str | None) -> bool:
    """Return True when URL or page HTML indicates SmartRecruiters presence."""
    host = (urlparse(url).hostname or "").lower()
    if "smartrecruiters.com" in host:
        return True
    if not html:
        return False
    lowered = html.lower()
    return "smartrecruiters.com" in lowered or "smartrecruiters" in lowered


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


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Fetch job listing URLs from the SmartRecruiters public API.

    Paginates the list endpoint and constructs posting URLs from token + ID.
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

    while True:
        resp = await client.get(
            _api_list_url(token),
            params={"limit": PAGE_SIZE, "offset": offset},
        )
        resp.raise_for_status()
        data = resp.json()

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
            break

    log.info("smartrecruiters.listed", token=token, postings=len(urls))
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
            result = {"token": slug}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("smartrecruiters", discover, cost=10, can_handle=can_handle, rich=False)
