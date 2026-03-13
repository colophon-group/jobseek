"""Workable Posting API monitor.

Public API:
  List:   POST https://apply.workable.com/api/v3/accounts/{slug}/jobs

The list endpoint returns metadata (title, location, department) but not the
full job description.  The monitor discovers job URLs only; a dedicated
scraper (``src/core/scrapers/workable``) fetches details on the daily
scrape schedule.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.monitors import (
    fetch_page_text,
    register,
    slug_guess_allowed,
    slugs_from_url,
)

log = structlog.get_logger()

MAX_JOBS = 10_000

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


async def _api_list(slug: str, client: httpx.AsyncClient) -> set[str]:
    """Paginate the list endpoint to collect all job URLs."""
    urls: set[str] = set()
    body: dict = {"query": "", "location": [], "department": [], "worktype": []}

    while True:
        resp = await client.post(_api_list_url(slug), json=body)
        resp.raise_for_status()
        data = resp.json()

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
            break

    return urls


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
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

    urls = await _api_list(slug, client)
    log.info("workable.listed", slug=slug, postings=len(urls))

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
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Workable: URL pattern -> page HTML scan -> slug-based API probe."""
    token = _token_from_url(url)
    if token:
        if client is not None:
            count = await _fetch_job_count(token, client)
            if count is not None:
                return {"token": token, "jobs": count}
        return {"token": token}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    log.info("workable.detected_in_page", url=url, board_token=found)
                    count = await _fetch_job_count(found, client)
                    result: dict = {"token": found}
                    if count is not None:
                        result["jobs"] = count
                    return result

    if slug_guess_allowed():
        for slug in slugs_from_url(url):
            found, count = await _probe_slug(slug, client)
            if found:
                log.info("workable.detected_by_probe", url=url, board_token=slug)
                result = {"token": slug}
                if count is not None:
                    result["jobs"] = count
                return result

    return None


register("workable", discover, cost=10, can_handle=can_handle, rich=False)
