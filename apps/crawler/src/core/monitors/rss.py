"""Generic RSS 2.0 feed monitor with ATS presets.

Supports multiple ATS platforms that expose job listings via RSS feeds:
- **successfactors**: SAP SuccessFactors CSB ``/googlefeed.xml`` (Google Base namespace)
- **teamtailor**: Teamtailor ``/jobs.rss`` (offset-paginated, ``tt:`` namespace)
- **generic**: Standard RSS 2.0 (manual config, not auto-detected)

Config: ``{"preset": "<name>", "feed_url": "..."}``
"""

from __future__ import annotations

import asyncio
import html
import random
import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.core.monitors.raw import save_text_response
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

MAX_JOBS = 50_000
_STREAM_BATCH = 200
_HTTP_CHUNK_BYTES = 64 * 1024
_SNIFF_BYTES = 512


async def _sleep(delay: float) -> None:
    """Patchable retry sleep used by RSS stream tests."""
    await asyncio.sleep(delay)


class RssFeedNotXml(ValueError):
    """Feed endpoint returned a non-XML body (e.g. publisher disabled the feed)."""


def _parse_feed(text: str, feed_url: str) -> ET.Element:
    """Parse an RSS response body, with a clear error for non-XML content.

    Some publishers retire their feed endpoint but keep the URL live, serving
    an HTML landing page or a plain-text "feed disabled" message with 200 OK.
    ``ET.fromstring`` then surfaces a cryptic ``not well-formed`` error that
    gives no clue about the actual cause. Sniff the leading bytes and raise a
    named error up-front so the monitor's ``last_error`` identifies the root
    cause instead of a column offset in a JavaScript blob.
    """
    head = text.lstrip()[:512].lower()
    if not head.startswith(("<?xml", "<rss", "<feed")):
        raise RssFeedNotXml(f"feed returned non-XML content: {feed_url}")
    return ET.fromstring(text)


# ── Preset definitions ──────────────────────────────────────────────────


@dataclass(frozen=True)
class _Preset:
    feed_paths: list[str]
    page_patterns: list[re.Pattern]
    feed_ns: dict[str, str]
    paginated: bool = False
    page_size: int = 100
    retryable_statuses: frozenset[int] = frozenset()


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
        # Teamtailor occasionally emits a transient 400 from an otherwise
        # healthy feed. Keep this provider-specific: a generic HTTP 400 is a
        # permanent request error and must still fail fast.
        retryable_statuses=frozenset({400}),
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


_PARSERS: dict[str, Callable[[ET.Element], DiscoveredJob | None]] = {
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


def _feed_head_is_xml(head: bytes, encoding: str | None) -> bool:
    """Return whether the bounded response prefix looks like XML/RSS."""
    text = head.decode(encoding or "utf-8", errors="ignore").lstrip().lower()
    return text.startswith(("<?xml", "<rss", "<feed"))


def _feed_parser_items(parser: ET.XMLPullParser, chunk: bytes) -> Iterator[ET.Element]:
    """Feed one bounded byte chunk and yield completed RSS items."""
    parser.feed(chunk)
    events = cast(Iterator[tuple[str, ET.Element]], parser.read_events())
    for _event, element in events:
        if element.tag == "item" or element.tag.endswith("}item"):
            yield element


async def _stream_feed_items(
    feed_url: str,
    preset: _Preset,
    client: httpx.AsyncClient,
    *,
    retries: int = 3,
    base_delay: float = 0.5,
) -> AsyncIterator[ET.Element]:
    """Yield feed items without buffering the HTTP body or XML tree.

    ``httpx.get`` buffers the complete decoded response, and ``ET.fromstring``
    then builds a second full-size representation. SuccessFactors feeds can
    exceed hundreds of MiB, so that pair can exhaust a 1 GiB worker before the
    generic monitor wrapper gets a chance to split the result into batches.

    Status and transport failures retain the crawler's bounded retry policy.
    Once an item has been yielded, a later transport failure is propagated
    immediately: the board run remains failed (and therefore cannot tombstone
    an unseen tail) rather than replaying already-processed batches.
    """
    from src.metrics import http_retry_attempts_total, http_retry_host
    from src.shared.http_retry import PaginationFetchError, is_retryable_status
    from src.shared.tdm import TDMReservedError
    from src.shared.tdm import check_response as _tdm_check

    host = http_retry_host(feed_url)
    last_error: BaseException | None = None
    last_status: int | None = None
    retried = False

    for attempt in range(retries):
        emitted = 0
        try:
            async with client.stream("GET", feed_url, follow_redirects=True) as response:
                last_status = response.status_code
                if response.status_code != 200:
                    if (
                        is_retryable_status(response.status_code)
                        or response.status_code in preset.retryable_statuses
                    ):
                        last_error = None
                    else:
                        raise PaginationFetchError(
                            feed_url,
                            attempts=attempt + 1,
                            last_status=response.status_code,
                        )
                else:
                    # RSS/XML cannot carry an HTML meta policy declaration at
                    # document level; the canonical HTTP header still applies.
                    _tdm_check(response)
                    parser = ET.XMLPullParser(events=("end",))
                    prefix = bytearray()
                    sniffed = False

                    async for chunk in response.aiter_bytes(chunk_size=_HTTP_CHUNK_BYTES):
                        if not sniffed:
                            prefix.extend(chunk)
                            if len(prefix) < _SNIFF_BYTES:
                                continue
                            head = bytes(prefix[:_SNIFF_BYTES])
                            if not _feed_head_is_xml(head, response.encoding):
                                raise RssFeedNotXml(f"feed returned non-XML content: {feed_url}")
                            chunk = bytes(prefix)
                            prefix.clear()
                            sniffed = True

                        for item in _feed_parser_items(parser, chunk):
                            emitted += 1
                            yield item
                            # The pull parser's root retains the element shell;
                            # clear its potentially huge description children.
                            item.clear()

                    if not sniffed:
                        if not _feed_head_is_xml(bytes(prefix), response.encoding):
                            raise RssFeedNotXml(f"feed returned non-XML content: {feed_url}")
                        for item in _feed_parser_items(parser, bytes(prefix)):
                            emitted += 1
                            yield item
                            item.clear()

                    parser.close()  # validate that the streamed XML completed
                    if retried:
                        http_retry_attempts_total.labels(host=host, outcome="recovered").inc()
                    return
        except (PaginationFetchError, RssFeedNotXml, ET.ParseError, TDMReservedError):
            raise
        except httpx.HTTPError as exc:
            last_error = exc
            last_status = None
            if emitted:
                raise PaginationFetchError(
                    feed_url,
                    attempts=attempt + 1,
                    last_error=type(exc).__name__,
                ) from exc

        retried = True
        http_retry_attempts_total.labels(host=host, outcome="retry").inc()
        if attempt < retries - 1:
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "rss.feed_backoff",
                url=feed_url,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                last_status=last_status,
                last_error=type(last_error).__name__ if last_error else None,
            )
            await _sleep(delay)

    http_retry_attempts_total.labels(host=host, outcome="exhausted").inc()
    raise PaginationFetchError(
        feed_url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_error).__name__ if last_error else None,
    )


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
        preset = _PRESETS.get(preset_name or "") or _Preset([], [], {})
        count = 0
        async for _item in _stream_feed_items(feed_url, preset, client):
            count += 1
        return True, count
    except Exception:
        return False, None


# ── Discover ────────────────────────────────────────────────────────────


def _feed_config(board: dict) -> tuple[str, str, _Preset] | None:
    """Resolve a board into ``(preset_name, feed_url, preset)``."""
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
        return None

    if preset is None:
        # Generic fallback — non-paginated, standard parser
        preset = _Preset(
            feed_paths=[],
            page_patterns=[],
            feed_ns={},
        )
    return preset_name, feed_url, preset


async def discover_stream(
    board: dict, client: httpx.AsyncClient, pw=None
) -> AsyncIterator[list[DiscoveredJob] | MonitorResult]:
    """Yield bounded parsed-job batches across streamed RSS pages."""
    config = _feed_config(board)
    if config is None:
        return
    preset_name, feed_url, preset = config
    parser = _PARSERS.get(preset_name, _parse_generic_item)
    jobs: list[DiscoveredJob] = []
    total_jobs = 0
    offset = 0

    while True:
        page_url = (
            _add_pagination(feed_url, offset, preset.page_size) if preset.paginated else feed_url
        )
        page_items = 0
        async for item in _stream_feed_items(page_url, preset, client):
            page_items += 1
            parsed = parser(item)
            if parsed is None:
                continue
            jobs.append(parsed)
            total_jobs += 1

            if total_jobs >= MAX_JOBS:
                log.warning("rss.truncated", feed=feed_url, total=total_jobs, cap=MAX_JOBS)
                yield truncated_rich_result(jobs)
                return
            if len(jobs) >= _STREAM_BATCH:
                yield jobs
                jobs = []

        if not preset.paginated or page_items < preset.page_size:
            break
        offset += preset.page_size

    if jobs:
        yield jobs


async def discover(
    board: dict, client: httpx.AsyncClient, pw=None
) -> list[DiscoveredJob] | MonitorResult:
    """Fetch job listings while retaining the non-streaming public API."""
    from src.core.monitor import MonitorResult

    jobs: list[DiscoveredJob] = []
    was_truncated = False
    async for batch in discover_stream(board, client, pw=pw):
        if isinstance(batch, MonitorResult):
            jobs.extend((batch.jobs_by_url or {}).values())
            was_truncated = was_truncated or bool(batch.truncated)
        else:
            jobs.extend(batch)

    if was_truncated:
        return truncated_rich_result(jobs)
    return jobs


# ── Can Handle (auto-detection) ─────────────────────────────────────────


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
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


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    feed = metadata.get("feed_url")
    if not feed:
        preset = _PRESETS.get(metadata.get("preset", "generic"))
        if preset:
            feed = _build_feed_url(board_url, preset.feed_paths[0])
    if not feed:
        return
    await save_text_response(
        artifact_dir,
        client,
        feed,
        filename="response.xml",
        follow_redirects=True,
    )


register(
    "rss",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    stream=discover_stream,
    save_raw=save_raw,
)
