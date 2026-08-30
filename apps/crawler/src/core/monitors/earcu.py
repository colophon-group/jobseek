"""eArcu live-vacancy XML feed monitor.

eArcu career sites expose a public ``allvacancies`` XML feed containing
only currently advertised positions.  The browser-facing search endpoint
is frequently protected by AWS WAF, while both the feed and detail pages
remain publicly accessible.

The feed is rich: it includes the canonical detail URL, title, description,
location, publication timestamp, and useful taxonomy metadata.  Reading it
directly also avoids eArcu's soft-200 response for closed detail pages.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx
from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException

from src.core.monitors import DiscoveredJob, register
from src.shared.http_retry import PaginationFetchError, fetch_with_retry

MAX_JOBS = 50_000
_MAX_FEED_CHARS = 50 * 1024 * 1024
_PROXY_CHALLENGE_STATUSES = frozenset({401, 403})


class EArcuParseError(Exception):
    """Raised when an eArcu feed is present but malformed or incomplete."""


def _candidate_feed_urls(board_url: str) -> list[str]:
    """Return likely ``allvacancies`` feeds from an eArcu board URL."""
    try:
        parsed = urlparse(board_url)
        port = parsed.port
    except ValueError:
        return []
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return []

    origin = f"https://{parsed.hostname.casefold()}"
    path = parsed.path.rstrip("/")
    prefixes: list[str] = []

    marker = "/vacancy/"
    if marker in path.lower():
        marker_at = path.lower().index(marker)
        prefixes.append(path[:marker_at])
    elif path:
        # eArcu CNAMEs commonly mount the portal at one first-level segment,
        # e.g. /jobs/vacancy/find/results/ and /jobs/allvacancies/.
        prefixes.append(f"/{path.strip('/').split('/', 1)[0]}")
    prefixes.append("")

    urls: list[str] = []
    for prefix in prefixes:
        candidate = f"{origin}{prefix}/allvacancies/"
        if candidate not in urls:
            urls.append(candidate)
    return urls


def _is_listing_url(board_url: str) -> bool:
    """Return whether *board_url* has a provider-specific eArcu listing path."""
    try:
        path = urlparse(board_url).path.casefold().rstrip("/")
    except ValueError:
        return False
    return path.endswith("/vacancy/find/results") or path.endswith(
        "/vacancies/vacancy-search-results.aspx"
    )


def _text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _parse_feed(text: str, feed_url: str) -> list[DiscoveredJob]:
    try:
        root = SafeElementTree.fromstring(text)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise EArcuParseError(f"Invalid eArcu XML at {feed_url}") from exc

    if root.tag.lower() != "positions":
        raise EArcuParseError(f"Unexpected eArcu root element at {feed_url}: {root.tag}")

    jobs: list[DiscoveredJob] = []
    seen_urls: set[str] = set()
    for position in root.findall("position"):
        raw_url = _text(position, "DescriptionURL")
        title = _text(position, "JobTitle")
        if not raw_url or not title:
            raise EArcuParseError(
                f"eArcu position missing DescriptionURL or JobTitle at {feed_url}"
            )
        url = _canonical_job_url(feed_url, raw_url)
        if url is None:
            raise EArcuParseError(f"eArcu position has an unsafe job URL at {feed_url}")
        if url in seen_urls:
            raise EArcuParseError(f"eArcu feed contains a duplicate job URL at {feed_url}")
        seen_urls.add(url)

        locations = [
            location.text.strip()
            for location in position.findall("./Locations/Location")
            if location.text and location.text.strip()
        ]
        metadata = {
            key: value
            for key, value in {
                "reference": _text(position, "VacancyRef"),
                "job_function": _text(position, "JobFunction"),
                "brand": _text(position, "Brand"),
                "salary_description": _text(position, "DisplaySalaryDescription"),
            }.items()
            if value is not None
        }
        jobs.append(
            DiscoveredJob(
                url=url,
                title=title,
                description=_text(position, "Description"),
                locations=locations or None,
                date_posted=_text(position, "LastPublishedDate"),
                metadata=metadata or None,
            )
        )

    if len(jobs) > MAX_JOBS:
        raise EArcuParseError(f"eArcu feed exceeds {MAX_JOBS} jobs at {feed_url}")
    return jobs


def _canonical_job_url(feed_url: str, raw_url: str) -> str | None:
    """Accept only canonical HTTPS vacancy URLs from the feed's own portal."""
    try:
        feed = urlparse(feed_url)
        candidate = urlparse(urljoin(feed_url, raw_url))
        feed_port = feed.port
        candidate_port = candidate.port
    except ValueError:
        return None

    feed_suffix = "/allvacancies/"
    if not feed.path.casefold().endswith(feed_suffix):
        return None
    portal_prefix = feed.path[: -len(feed_suffix)]
    vacancy_prefix = f"{portal_prefix}/vacancy/"
    if (
        feed.scheme.casefold() != "https"
        or candidate.scheme.casefold() != "https"
        or feed.hostname is None
        or candidate.hostname is None
        or candidate.hostname.casefold() != feed.hostname.casefold()
        or feed.username is not None
        or feed.password is not None
        or candidate.username is not None
        or candidate.password is not None
        or feed_port not in {None, 443}
        or candidate_port not in {None, 443}
        or feed.query
        or feed.fragment
        or candidate.query
        or candidate.fragment
        or not candidate.path.casefold().startswith(vacancy_prefix.casefold())
    ):
        return None
    return candidate._replace(scheme="https", netloc=feed.hostname.casefold()).geturl()


async def _fetch_feed(client: httpx.AsyncClient, feed_url: str) -> str | None:
    return await fetch_with_retry(
        client,
        feed_url,
        max_chars=_MAX_FEED_CHARS,
        transient_403=True,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    metadata = board.get("metadata") or {}
    feed_url = metadata.get("feed_url")
    candidates = _candidate_feed_urls(board["board_url"])
    if not candidates:
        raise EArcuParseError(f"Unsafe eArcu board URL: {board['board_url']}")
    if feed_url and feed_url not in candidates:
        raise EArcuParseError(f"Unsafe eArcu feed URL for {board['board_url']}")
    if not feed_url:
        detected = await can_handle(board["board_url"], client)
        if not detected:
            raise EArcuParseError(f"No eArcu allvacancies feed found for {board['board_url']}")
        feed_url = detected["feed_url"]

    text = await _fetch_feed(client, feed_url)
    if text is None:
        raise EArcuParseError(f"eArcu feed not found at {feed_url}")
    return _parse_feed(text, feed_url)


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect an eArcu CNAME by probing its live-vacancy feed."""
    if client is None:
        return None

    for feed_url in _candidate_feed_urls(url):
        try:
            text = await _fetch_feed(client, feed_url)
            if text is None:
                continue
            jobs = _parse_feed(text, feed_url)
            return {"feed_url": feed_url, "jobs": len(jobs)}
        except PaginationFetchError as exc:
            # Some eArcu CNAMEs apply the same IP-based WAF rule to the
            # live-only XML feed as to the browser search page.  The two
            # listing paths below are provider-specific enough to retain the
            # deterministic detection and opt the board into the existing
            # proxy transport.  Runtime discovery still fetches and parses
            # the XML fail-closed through that routed client.
            if exc.last_status in _PROXY_CHALLENGE_STATUSES and _is_listing_url(url):
                return {"feed_url": feed_url, "proxy": True}
            raise
        except EArcuParseError:
            return None
    return None


register("earcu", discover, cost=10, can_handle=can_handle, rich=True)
