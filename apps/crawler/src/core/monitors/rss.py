"""Generic RSS 2.0 feed monitor with ATS presets.

Supports multiple ATS platforms that expose job listings via RSS feeds:
- **successfactors**: SAP SuccessFactors CSB ``/googlefeed.xml`` (Google Base namespace)
- **teamtailor**: Teamtailor ``/jobs.rss`` (offset-paginated, ``tt:`` namespace)
- **generic**: Standard RSS 2.0 (manual config, not auto-detected)

Config: ``{"preset": "<name>", "feed_url": "..."}``
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 10_000


# ── Preset definitions ──────────────────────────────────────────────────

@dataclass(frozen=True)
class _Preset:
    feed_paths: list[str]
    page_patterns: list[re.Pattern]
    feed_ns: dict[str, str]
    paginated: bool = False
    page_size: int = 100


_PRESETS: dict[str, _Preset] = {
    "successfactors": _Preset(
        feed_paths=["/googlefeed.xml"],
        page_patterns=[
            re.compile(r"successfactors\.(?:eu|com)"),
            re.compile(r"rmkcdn\.successfactors\.com"),
            re.compile(r"jobs2web\.com"),
        ],
        feed_ns={"g": "http://base.google.com/ns/1.0"},
    ),
    "teamtailor": _Preset(
        feed_paths=["/jobs.rss"],
        page_patterns=[
            re.compile(r"teamtailor-cdn\.com"),
        ],
        feed_ns={"tt": "https://teamtailor.com/locations"},
        paginated=True,
        page_size=100,
    ),
}

# Teamtailor namespace — used in item parsing
_TT_NS = "https://teamtailor.com/locations"
# Google Base namespace — used in item parsing
_G_NS = "http://base.google.com/ns/1.0"

# Location suffix in title, e.g. " (Tempe, AZ, US, 85288)"
_TITLE_LOCATION_RE = re.compile(r"\s*\([^)]+,\s*[^)]+\)\s*$")


# ── Item parsers ────────────────────────────────────────────────────────

def _text(item: ET.Element, tag: str) -> str | None:
    """Get text content of a child element."""
    child = item.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _g(item: ET.Element, tag: str) -> str | None:
    """Get text from a Google Base namespace child element."""
    child = item.find(f"{{{_G_NS}}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _tt(item: ET.Element, tag: str) -> str | None:
    """Get text from a Teamtailor namespace child element."""
    child = item.find(f"{{{_TT_NS}}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _tt_all(item: ET.Element, tag: str) -> list[str]:
    """Get all text values from repeated Teamtailor namespace elements."""
    results = []
    for child in item.findall(f"{{{_TT_NS}}}{tag}"):
        if child.text and child.text.strip():
            results.append(child.text.strip())
    return results


def _parse_sf_item(item: ET.Element) -> DiscoveredJob | None:
    """Parse a SuccessFactors RSS item (Google Base namespace)."""
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = html.unescape(raw_desc) if raw_desc else None

    location = _g(item, "location")
    locations = [location] if location else None

    if title and location:
        cleaned = _TITLE_LOCATION_RE.sub("", title)
        if cleaned:
            title = cleaned

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


def _tt_location_string(loc_el: ET.Element) -> str | None:
    """Build a location string from a tt:location element.

    Prefers tt:name when populated, falls back to "city, country".
    """
    name_el = loc_el.find(f"{{{_TT_NS}}}name")
    name = name_el.text.strip() if name_el is not None and name_el.text else ""
    if name:
        return name

    city_el = loc_el.find(f"{{{_TT_NS}}}city")
    country_el = loc_el.find(f"{{{_TT_NS}}}country")
    city = city_el.text.strip() if city_el is not None and city_el.text else ""
    country = country_el.text.strip() if country_el is not None and country_el.text else ""

    if city and country:
        return f"{city}, {country}"
    return city or country or None


def _parse_tt_item(item: ET.Element) -> DiscoveredJob | None:
    """Parse a Teamtailor RSS item (tt: namespace)."""
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = html.unescape(raw_desc) if raw_desc else None

    # Structured locations: tt:locations > tt:location > (tt:name | tt:city, tt:country)
    locations: list[str] = []
    locations_el = item.find(f"{{{_TT_NS}}}locations")
    if locations_el is not None:
        for loc_el in locations_el.findall(f"{{{_TT_NS}}}location"):
            loc_str = _tt_location_string(loc_el)
            if loc_str:
                locations.append(loc_str)

    # remoteStatus is a plain element (not namespaced)
    remote_status = _text(item, "remoteStatus")
    job_location_type: str | None = None
    if remote_status:
        lower = remote_status.lower()
        if "fully" in lower or lower == "remote":
            job_location_type = "remote"
        elif "hybrid" in lower:
            job_location_type = "hybrid"
        elif lower in ("none", "onsite", "on-site"):
            job_location_type = "onsite"

    date_posted = _text(item, "pubDate")
    department = _tt(item, "department")
    role = _tt(item, "role")
    guid = _text(item, "guid")

    metadata: dict = {}
    if guid:
        metadata["id"] = guid
    if department:
        metadata["department"] = department
    if role:
        metadata["role"] = role

    return DiscoveredJob(
        url=link,
        title=title,
        description=description,
        locations=locations or None,
        job_location_type=job_location_type,
        date_posted=date_posted,
        metadata=metadata or None,
    )


def _parse_generic_item(item: ET.Element) -> DiscoveredJob | None:
    """Parse a standard RSS 2.0 item."""
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = html.unescape(raw_desc) if raw_desc else None
    date_posted = _text(item, "pubDate")
    guid = _text(item, "guid")

    metadata: dict = {}
    if guid:
        metadata["id"] = guid

    return DiscoveredJob(
        url=link,
        title=title,
        description=description,
        date_posted=date_posted,
        metadata=metadata or None,
    )


_PARSERS: dict[str, type[None] | object] = {
    "successfactors": _parse_sf_item,
    "teamtailor": _parse_tt_item,
    "generic": _parse_generic_item,
}


# ── Feed URL helpers ────────────────────────────────────────────────────

def _build_feed_url(board_url: str, path: str) -> str:
    """Build a feed URL from a board URL and feed path."""
    parsed = urlparse(board_url)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _add_pagination(url: str, offset: int, per_page: int) -> str:
    """Add pagination query parameters to a URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    params["offset"] = [str(offset)]
    params["per_page"] = [str(per_page)]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


# ── Feed fetching ───────────────────────────────────────────────────────

async def _fetch_all_items(
    feed_url: str,
    preset: _Preset,
    client: httpx.AsyncClient,
) -> list[ET.Element]:
    """Fetch all RSS items, handling pagination for presets that need it."""
    if not preset.paginated:
        resp = await client.get(feed_url, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        return channel.findall("item") if channel is not None else []

    # Paginated fetch (Teamtailor-style offset pagination)
    all_items: list[ET.Element] = []
    offset = 0
    page_size = preset.page_size

    while True:
        page_url = _add_pagination(feed_url, offset, page_size)
        resp = await client.get(page_url, follow_redirects=True)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            break

        items = channel.findall("item")
        all_items.extend(items)

        if len(items) < page_size:
            break  # Last page

        offset += page_size

        if len(all_items) >= MAX_JOBS:
            break

    return all_items


async def _probe_feed(
    feed_url: str,
    client: httpx.AsyncClient,
    preset_name: str | None = None,
) -> tuple[bool, int | None]:
    """Probe an RSS feed URL. Returns (found, job_count).

    For paginated presets, only the first page is fetched — count may be
    approximate (capped at page_size).
    """
    try:
        resp = await client.get(feed_url, follow_redirects=True)
        if resp.status_code != 200:
            return False, None

        text = resp.text[:2000]
        if "<rss" not in text:
            return False, None

        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return False, None

        items = channel.findall("item")
        return True, len(items)
    except Exception:
        return False, None


# ── Discover ────────────────────────────────────────────────────────────

async def discover(
    board: dict, client: httpx.AsyncClient, pw=None
) -> list[DiscoveredJob]:
    """Fetch job listings from an RSS feed."""
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}

    preset_name = metadata.get("preset", "generic")
    preset = _PRESETS.get(preset_name)

    # Determine feed URL: explicit config > derive from preset > fallback
    feed_url = metadata.get("feed_url")
    if not feed_url and preset:
        feed_url = _build_feed_url(board_url, preset.feed_paths[0])
    if not feed_url:
        log.error("rss.no_feed_url", board_url=board_url, preset=preset_name)
        return []

    if preset is None:
        # Generic fallback — non-paginated, standard parser
        preset = _Preset(
            feed_paths=[],
            page_patterns=[],
            feed_ns={},
        )

    items = await _fetch_all_items(feed_url, preset, client)

    parser = _PARSERS.get(preset_name, _parse_generic_item)
    jobs: list[DiscoveredJob] = []
    for item in items:
        parsed = parser(item)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("rss.truncated", feed=feed_url, total=len(jobs), cap=MAX_JOBS)
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


# ── Can Handle (auto-detection) ─────────────────────────────────────────

async def can_handle(
    url: str, client: httpx.AsyncClient | None = None, pw=None
) -> dict | None:
    """Detect RSS-based ATS: HTML scan for preset markers → feed probe."""
    if client is None:
        return None

    # 1. Fetch page HTML once for all preset pattern checks
    html_text = await fetch_page_text(url, client)

    for preset_name, preset in _PRESETS.items():
        detected = False
        if html_text and preset.page_patterns:
            detected = any(p.search(html_text) for p in preset.page_patterns)

        if detected:
            # Try all feed paths for this preset
            for path in preset.feed_paths:
                feed = _build_feed_url(url, path)
                found, count = await _probe_feed(feed, client, preset_name)
                if found:
                    result: dict = {"preset": preset_name, "feed_url": feed}
                    if count is not None:
                        result["jobs"] = count
                    log.info(
                        "rss.detected_in_page",
                        url=url,
                        preset=preset_name,
                        jobs=count,
                    )
                    return result

    # 2. Blind feed probe as fallback — try all presets' feed paths
    for preset_name, preset in _PRESETS.items():
        for path in preset.feed_paths:
            feed = _build_feed_url(url, path)
            found, count = await _probe_feed(feed, client, preset_name)
            if found:
                result = {"preset": preset_name, "feed_url": feed}
                if count is not None:
                    result["jobs"] = count
                log.info(
                    "rss.detected_by_probe",
                    url=url,
                    preset=preset_name,
                    jobs=count,
                )
                return result

    return None


register("rss", discover, cost=10, can_handle=can_handle, rich=True)
