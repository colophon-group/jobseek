"""YCombinator Jobs monitor (last resort).

WARNING: YC Jobs is a **last-resort** monitor. All companies eventually
outgrow the platform and migrate to a dedicated ATS (Greenhouse, Lever,
Ashby, etc.). Only use this monitor when no real ATS board exists.

Listing pages are server-rendered HTML with job links in a predictable
URL pattern. Individual job pages contain JSON-LD ``JobPosting`` structured
data — pair with the ``json-ld`` scraper.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.monitors import fetch_page_text, register

log = structlog.get_logger()

_SLUG_RE = re.compile(r"^https?://(?:www\.)?ycombinator\.com/companies/([\w-]+)/jobs(?:[/?#]|$)")

_JOB_HREF_RE = re.compile(r'"/companies/([\w-]+)/jobs/([A-Za-z0-9]+-[^"]+)"')


def _slug_from_url(url: str) -> str | None:
    """Extract company slug from a YC jobs URL."""
    m = _SLUG_RE.match(url)
    return m.group(1) if m else None


def _listing_url(slug: str) -> str:
    return f"https://www.ycombinator.com/companies/{slug}/jobs"


def _extract_job_urls(html: str, slug: str) -> set[str]:
    """Extract job URLs for *slug* from raw HTML href attributes."""
    urls: set[str] = set()
    for m in _JOB_HREF_RE.finditer(html):
        if m.group(1) == slug:
            urls.add(f"https://www.ycombinator.com/companies/{slug}/jobs/{m.group(2)}")
    return urls


# ── Discovery ────────────────────────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover job URLs from a YC company jobs page."""
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive YCombinator slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    resp = await client.get(_listing_url(slug), follow_redirects=True)
    resp.raise_for_status()

    urls = _extract_job_urls(resp.text, slug)
    log.info("ycombinator.listed", slug=slug, jobs=len(urls))
    return urls


# ── Probing ──────────────────────────────────────────────────────────────


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect YCombinator jobs page from URL pattern, optionally counting jobs."""
    slug = _slug_from_url(url)
    if not slug:
        return None

    if client is None:
        return {"slug": slug}

    html = await fetch_page_text(_listing_url(slug), client)
    if html is None:
        return None

    urls = _extract_job_urls(html, slug)
    return {"slug": slug, "jobs": len(urls)}


register("ycombinator", discover, cost=10, can_handle=can_handle)
