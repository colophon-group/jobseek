"""Rippling ATS Job Board API monitor.

Public API:
  List:   GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs

The list endpoint returns all jobs (no pagination) with basic metadata.
The monitor extracts UUIDs and constructs posting URLs.  Detail fetching
is handled by the scraper (``src/core/scrapers/rippling``).
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.monitors import fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000

_API_BASE = "https://api.rippling.com/platform/api/ats/v1/board"

# Matches ats.rippling.com or ats.us1.rippling.com, with optional locale prefix
_URL_RE = re.compile(r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs")

_PAGE_PATTERNS = [
    re.compile(r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs"),
    re.compile(r"api\.rippling\.com/platform/api/ats/\w+/board/([\w-]+)"),
]

_IGNORE_SLUGS = frozenset({"api", "platform", "static", "assets", "js", "css"})


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Rippling board slug from an ats.rippling.com URL."""
    match = _URL_RE.search(board_url)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_list_url(slug: str) -> str:
    return f"{_API_BASE}/{slug}/jobs"


def _posting_url(slug: str, uuid: str) -> str:
    """Build the public posting URL for a Rippling job."""
    return f"https://ats.rippling.com/{slug}/jobs/{uuid}"


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Fetch job listing URLs from the Rippling public API.

    Lists all jobs via the V1 endpoint (single request, no pagination)
    and constructs posting URLs from slug + uuid.
    """
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Rippling board slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    resp = await client.get(_api_list_url(slug))
    resp.raise_for_status()

    job_list: list[dict] = resp.json()
    if not isinstance(job_list, list):
        return set()

    uuids = [j["uuid"] for j in job_list if j.get("uuid")]

    if len(uuids) > MAX_JOBS:
        log.warning("rippling.truncated", slug=slug, total=len(uuids), cap=MAX_JOBS)
        uuids = uuids[:MAX_JOBS]

    log.info("rippling.listed", slug=slug, jobs=len(uuids))

    return {_posting_url(slug, uuid) for uuid in uuids}


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Rippling API for a slug. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_list_url(slug))
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        if isinstance(data, list):
            return True, len(data)
        return False, None
    except Exception:
        return False, None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Rippling: URL pattern -> page HTML scan -> slug-based API probe."""
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_slug(slug, client)
            if found:
                result: dict = {"slug": slug}
                if count is not None:
                    result["jobs"] = count
                return result
        return {"slug": slug}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found_slug = match.group(1)
                if found_slug not in _IGNORE_SLUGS:
                    log.info("rippling.detected_in_page", url=url, slug=found_slug)
                    found, count = await _probe_slug(found_slug, client)
                    if found:
                        result = {"slug": found_slug}
                        if count is not None:
                            result["jobs"] = count
                        return result

    for slug_candidate in slugs_from_url(url):
        found, count = await _probe_slug(slug_candidate, client)
        if found:
            log.info("rippling.detected_by_probe", url=url, slug=slug_candidate)
            result = {"slug": slug_candidate}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("rippling", discover, cost=10, can_handle=can_handle, rich=False)
