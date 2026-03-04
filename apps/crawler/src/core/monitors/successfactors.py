"""SAP SuccessFactors Career Site Builder (CSB) monitor.

Public RSS 2.0 feed: GET https://{domain}/googlefeed.xml
Returns full job data with Google Base namespace extensions — no pagination needed.
The feed includes title, full HTML description, job URL, location, employer,
job function, and expiration date for every job.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 10_000

# Google Base namespace used in the RSS feed
_G_NS = "http://base.google.com/ns/1.0"

# Patterns that indicate a SuccessFactors CSB site in page HTML
_PAGE_PATTERNS = [
    re.compile(r"successfactors\.(?:eu|com)"),
    re.compile(r"rmkcdn\.successfactors\.com"),
    re.compile(r"jobs2web\.com"),
]

# Location suffix in title, e.g. " (Tempe, AZ, US, 85288)"
_TITLE_LOCATION_RE = re.compile(r"\s*\([^)]+,\s*[^)]+\)\s*$")


def _feed_url(board_url: str) -> str:
    """Build the googlefeed.xml URL from a board URL."""
    parsed = urlparse(board_url)
    return f"{parsed.scheme}://{parsed.netloc}/googlefeed.xml"


def _g(item: ET.Element, tag: str) -> str | None:
    """Get text from a Google Base namespace child element."""
    child = item.find(f"{{{_G_NS}}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _text(item: ET.Element, tag: str) -> str | None:
    """Get text content of a child element."""
    child = item.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _decode_description(raw: str) -> str:
    """Decode HTML-entity-encoded description from the RSS feed.

    The feed wraps descriptions in CDATA containing HTML-entity-encoded HTML.
    One decode pass restores the actual HTML.
    """
    return html.unescape(raw)


def _clean_title(title: str, location: str | None) -> str:
    """Strip location suffix from title when it duplicates g:location."""
    if location:
        cleaned = _TITLE_LOCATION_RE.sub("", title)
        if cleaned:
            return cleaned
    return title


def _parse_item(item: ET.Element) -> DiscoveredJob | None:
    """Parse an RSS <item> element into a DiscoveredJob."""
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = _decode_description(raw_desc) if raw_desc else None

    location = _g(item, "location")
    locations = [location] if location else None

    if title:
        title = _clean_title(title, location)

    job_id = _text(item, "guid")
    expiration_date = _g(item, "expiration_date")
    employer = _g(item, "employer")
    job_function = _g(item, "job_function")

    metadata: dict = {}
    if job_id:
        metadata["id"] = job_id
    if employer:
        metadata["employer"] = employer
    if job_function and job_function != "ATS_WEBFORM":
        metadata["job_function"] = job_function
    if expiration_date:
        metadata["expiration_date"] = expiration_date

    return DiscoveredJob(
        url=link,
        title=title,
        description=description,
        locations=locations,
        metadata=metadata or None,
    )


async def _probe_feed(
    feed: str, client: httpx.AsyncClient
) -> tuple[bool, int | None]:
    """Probe the googlefeed.xml RSS feed. Returns (found, job_count)."""
    try:
        resp = await client.get(feed, follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        # Quick check: RSS with Google Base namespace
        text = resp.text[:2000]
        if "<rss" not in text or "base.google.com" not in text:
            return False, None
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return False, None
        items = channel.findall("item")
        return True, len(items)
    except Exception:
        return False, None


async def discover(
    board: dict, client: httpx.AsyncClient, pw=None
) -> list[DiscoveredJob]:
    """Fetch job listings from the SuccessFactors CSB RSS feed."""
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}

    feed = metadata.get("feed_url") or _feed_url(board_url)

    response = await client.get(feed, follow_redirects=True)
    response.raise_for_status()

    root = ET.fromstring(response.text)
    channel = root.find("channel")
    if channel is None:
        return []

    items = channel.findall("item")
    jobs: list[DiscoveredJob] = []
    for item in items:
        parsed = _parse_item(item)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning(
            "successfactors.truncated", feed=feed, total=len(jobs), cap=MAX_JOBS
        )
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


async def can_handle(
    url: str, client: httpx.AsyncClient | None = None, pw=None
) -> dict | None:
    """Detect SuccessFactors CSB: HTML scan -> feed probe."""
    if client is None:
        return None

    # 1. HTML scan for SuccessFactors markers
    html_text = await fetch_page_text(url, client)
    if html_text:
        detected = any(p.search(html_text) for p in _PAGE_PATTERNS)
        if detected:
            feed = _feed_url(url)
            found, count = await _probe_feed(feed, client)
            if found:
                result: dict = {"feed_url": feed}
                if count is not None:
                    result["jobs"] = count
                log.info("successfactors.detected_in_page", url=url, jobs=count)
                return result

    # 2. Blind feed probe as fallback
    feed = _feed_url(url)
    found, count = await _probe_feed(feed, client)
    if found:
        result = {"feed_url": feed}
        if count is not None:
            result["jobs"] = count
        log.info("successfactors.detected_by_probe", url=url, jobs=count)
        return result

    return None


register("successfactors", discover, cost=10, can_handle=can_handle)
