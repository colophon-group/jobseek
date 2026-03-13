"""Breezy HR monitor.

Public endpoints observed across Breezy portals:
  List:   GET https://{portal}/json

The listing endpoint returns structured job metadata. Detail pages are handled
by the json-ld scraper (auto-configured).

This monitor supports:
- Direct Breezy portals ({slug}.breezy.hr)
- Custom pages that embed/link to a Breezy portal (Powered by Breezy widgets)
- Optional explicit override via monitor config: {"portal_url": "..."}
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from src.core.monitors import register

log = structlog.get_logger()

MAX_JOBS = 10_000

_BREEZY_DOMAIN_RE = re.compile(r"^([\w-]+)\.breezy\.hr$")
_PORTAL_HOST_RE = re.compile(r"(?:https?:)?//([\w-]+\.breezy\.hr)", re.IGNORECASE)

_IGNORE_SLUGS = frozenset(
    {
        "www",
        "api",
        "app",
        "developer",
        "marketing",
        "assets-cdn",
        "attachments-cdn",
        "gallery-cdn",
    }
)

_PORTAL_MARKERS = (
    "breezy-portal",
    "powered by breezy",
    "assets-cdn.breezy.hr/breezy-portal",
    "app.breezy.hr/api/apply",
    ".breezy.hr",
)


def _origin(url: str) -> str | None:
    """Normalize URL to scheme+host origin."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return None
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host.lower()}"


def _breezy_portal_from_host(host: str, scheme: str = "https") -> str | None:
    """Return Breezy portal origin for a valid *.breezy.hr host."""
    host_l = host.lower().strip(".")
    match = _BREEZY_DOMAIN_RE.match(host_l)
    if not match:
        return None
    slug = match.group(1)
    if slug in _IGNORE_SLUGS:
        return None
    return f"{scheme}://{host_l}"


def _breezy_portal_from_url(url: str) -> str | None:
    """Extract Breezy portal origin from URL host."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    scheme = parsed.scheme or "https"
    return _breezy_portal_from_host(host, scheme=scheme)


def _slug_from_portal(portal_url: str) -> str | None:
    """Extract Breezy slug from a portal origin."""
    host = (urlparse(portal_url).hostname or "").lower()
    match = _BREEZY_DOMAIN_RE.match(host)
    if not match:
        return None
    return match.group(1)


def _api_url(portal_url: str) -> str:
    return f"{portal_url.rstrip('/')}/json"


def _has_breezy_signal(url: str, html: str | None) -> bool:
    """Return True when URL or page HTML indicates Breezy portal usage."""
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".breezy.hr"):
        return True
    if not html:
        return False
    lowered = html.lower()
    return any(marker in lowered for marker in _PORTAL_MARKERS)


def _portal_candidates_from_html(html: str) -> list[str]:
    """Extract candidate Breezy portal origins from raw HTML."""
    seen: set[str] = set()
    candidates: list[str] = []
    for match in _PORTAL_HOST_RE.finditer(html):
        portal = _breezy_portal_from_host(match.group(1))
        if portal and portal not in seen:
            seen.add(portal)
            candidates.append(portal)
    return candidates


def _opening_url(opening: dict, portal_url: str) -> str | None:
    """Build absolute opening URL from listing item."""
    raw = opening.get("url")
    if isinstance(raw, str) and raw.strip():
        return urljoin(f"{portal_url.rstrip('/')}/", raw.strip())
    friendly_id = opening.get("friendly_id")
    if isinstance(friendly_id, str) and friendly_id.strip():
        return f"{portal_url.rstrip('/')}/p/{friendly_id.strip()}"
    return None


async def _probe_portal(portal_url: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Validate Breezy listing endpoint for a candidate portal."""
    try:
        resp = await client.get(_api_url(portal_url), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        if not isinstance(data, list):
            return False, None
        if data:
            first = data[0]
            if not isinstance(first, dict):
                return False, None
            if "id" not in first or "url" not in first:
                return False, None
        return True, len(data)
    except Exception:
        return False, None


async def _fetch_openings(portal_url: str, client: httpx.AsyncClient) -> list[dict]:
    """Fetch openings list from Breezy listing endpoint."""
    resp = await client.get(_api_url(portal_url), follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Breezy endpoint {_api_url(portal_url)!r} did not return a JSON list")
    return [item for item in data if isinstance(item, dict)]


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Fetch job listing URLs from Breezy listing endpoint."""
    metadata = board.get("metadata") or {}

    portal_url = None
    if isinstance(metadata.get("portal_url"), str):
        portal_url = _origin(metadata["portal_url"])
    if portal_url is None:
        portal_url = _breezy_portal_from_url(board["board_url"])
    if portal_url is None and isinstance(metadata.get("slug"), str):
        slug = metadata["slug"].strip()
        if slug:
            portal_url = f"https://{slug}.breezy.hr"

    if not portal_url:
        raise ValueError(
            f"Cannot derive Breezy portal URL from board URL {board['board_url']!r} "
            "and no portal_url/slug in metadata"
        )

    openings = await _fetch_openings(portal_url, client)
    if len(openings) > MAX_JOBS:
        log.warning("breezy.truncated", portal=portal_url, total=len(openings), cap=MAX_JOBS)
        openings = openings[:MAX_JOBS]

    urls: set[str] = set()
    for opening in openings:
        url = _opening_url(opening, portal_url)
        if url:
            urls.add(url)
    return urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Breezy boards via URL, redirect target, embedded links, and /json validation."""
    portal = _breezy_portal_from_url(url)
    if portal:
        slug = _slug_from_portal(portal)
        if client is None:
            result: dict = {"portal_url": portal}
            if slug:
                result["slug"] = slug
            return result
        found, count = await _probe_portal(portal, client)
        if found:
            result = {"portal_url": portal}
            if slug:
                result["slug"] = slug
            if count is not None:
                result["jobs"] = count
            return result
        return None

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

    # 1) Redirect target is a Breezy portal
    redirected_portal = _breezy_portal_from_url(final_url)
    if redirected_portal:
        found, count = await _probe_portal(redirected_portal, client)
        if found:
            result = {"portal_url": redirected_portal}
            slug = _slug_from_portal(redirected_portal)
            if slug:
                result["slug"] = slug
            if count is not None:
                result["jobs"] = count
            return result

    # 2) Page embeds/links a Breezy portal
    if html:
        for candidate in _portal_candidates_from_html(html):
            found, count = await _probe_portal(candidate, client)
            if found:
                log.info("breezy.detected_in_page", url=url, portal_url=candidate)
                result = {"portal_url": candidate}
                slug = _slug_from_portal(candidate)
                if slug:
                    result["slug"] = slug
                if count is not None:
                    result["jobs"] = count
                return result

    # 3) CNAME-style custom domain Breezy portal (same-origin /json)
    if _has_breezy_signal(final_url, html):
        custom_origin = _origin(final_url)
        if custom_origin:
            found, count = await _probe_portal(custom_origin, client)
            if found:
                result = {"portal_url": custom_origin}
                if count is not None:
                    result["jobs"] = count
                return result

    return None


register("breezy", discover, cost=10, can_handle=can_handle, rich=False)
