"""XML sitemap monitor with automatic sitemap URL discovery."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import structlog

from src.core.monitors import register

log = structlog.get_logger()

MAX_URLS = 10_000
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

_JOB_KEYWORDS = ("job", "career", "posting", "position", "vacancy", "opening")


class SitemapParseError(Exception):
    """Raised when the sitemap XML cannot be parsed."""


class SitemapDiscoveryError(Exception):
    """Raised when no sitemap URL can be found for a board."""


def _strip_utm(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in params.items() if not k.startswith("utm_")}
    if not filtered:
        return parsed._replace(query="").geturl()
    return parsed._replace(query=urlencode(filtered, doseq=True)).geturl()


def _extract_urls(root: ET.Element) -> list[str]:
    urls: list[str] = []
    for url_el in root.findall(f"{NS}url"):
        loc = url_el.find(f"{NS}loc")
        if loc is not None and loc.text:
            urls.append(_strip_utm(loc.text.strip()))
    if not urls:
        for url_el in root.findall("url"):
            loc = url_el.find("loc")
            if loc is not None and loc.text:
                urls.append(_strip_utm(loc.text.strip()))
    return urls


def _is_sitemap_index(root: ET.Element) -> bool:
    tag = root.tag.lower()
    return "sitemapindex" in tag


def _extract_child_sitemaps(root: ET.Element) -> list[str]:
    urls: list[str] = []
    for el in root.findall(f"{NS}sitemap"):
        loc = el.find(f"{NS}loc")
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    if not urls:
        for el in root.findall("sitemap"):
            loc = el.find("loc")
            if loc is not None and loc.text:
                urls.append(loc.text.strip())
    return urls


def _is_job_related(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(kw in path for kw in _JOB_KEYWORDS)


async def _try_fetch_xml(url: str, client: httpx.AsyncClient) -> ET.Element | None:
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        content_type = resp.headers.get("content-type", "")
        if "xml" not in content_type:
            return None
        return ET.fromstring(resp.text)
    except (httpx.HTTPError, ET.ParseError):
        return None


def _walk_up_candidates(board_url: str) -> list[str]:
    parsed = urlparse(board_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    candidates: list[str] = []
    candidates.append(f"{origin}{path}/sitemap.xml")

    while "/" in path:
        path = path.rsplit("/", 1)[0]
        if path:
            candidates.append(f"{origin}{path}/sitemap.xml")

    root = f"{origin}/sitemap.xml"
    if root not in candidates:
        candidates.append(root)

    return candidates


def _common_nonstandard_candidates(board_url: str) -> list[str]:
    parsed = urlparse(board_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return [
        f"{origin}/sitemaps/sitemapIndex",
        f"{origin}/sitemap/sitemap.xml",
        f"{origin}/sitemaps/sitemap.xml",
    ]


async def _parse_robots_sitemaps(
    board_url: str,
    client: httpx.AsyncClient,
) -> list[str]:
    parsed = urlparse(board_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url)
        if resp.status_code != 200:
            return []
        if "xml" in resp.headers.get("content-type", ""):
            return []
        if "<html" in resp.text[:500].lower():
            return []
    except httpx.HTTPError:
        return []

    sitemaps: list[str] = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line.lower().startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url:
                sitemaps.append(url)
    return sitemaps


async def _resolve_sitemap_index(
    root: ET.Element,
    client: httpx.AsyncClient,
) -> ET.Element | None:
    children = _extract_child_sitemaps(root)
    if not children:
        return None
    job_children = [u for u in children if _is_job_related(u)]
    target = job_children[0] if job_children else children[0]
    log.debug("sitemap.index_resolved", target=target, total_children=len(children))
    return await _try_fetch_xml(target, client)


async def _discover_sitemap(
    board_url: str,
    client: httpx.AsyncClient,
) -> tuple[str, ET.Element]:
    """Try multiple strategies to find and fetch the sitemap XML.

    Returns (sitemap_url, parsed_xml_root).
    Raises SitemapDiscoveryError if nothing works.
    """
    for candidate in _walk_up_candidates(board_url):
        root = await _try_fetch_xml(candidate, client)
        if root is not None:
            if _is_sitemap_index(root):
                child_root = await _resolve_sitemap_index(root, client)
                if child_root is not None:
                    return candidate, child_root
            else:
                return candidate, root

    for candidate in _common_nonstandard_candidates(board_url):
        root = await _try_fetch_xml(candidate, client)
        if root is not None:
            if _is_sitemap_index(root):
                child_root = await _resolve_sitemap_index(root, client)
                if child_root is not None:
                    return candidate, child_root
            else:
                return candidate, root

    for candidate in await _parse_robots_sitemaps(board_url, client):
        root = await _try_fetch_xml(candidate, client)
        if root is not None:
            if _is_sitemap_index(root):
                child_root = await _resolve_sitemap_index(root, client)
                if child_root is not None:
                    return candidate, child_root
            else:
                return candidate, root

    raise SitemapDiscoveryError(f"No sitemap found for {board_url}")


async def discover(
    board: dict,
    client: httpx.AsyncClient,
) -> tuple[set[str], str | None]:
    """Fetch and parse an XML sitemap, returning (discovered_urls, sitemap_url).

    sitemap_url is non-None when a new sitemap was discovered (not cached),
    signaling the caller to persist it in board metadata.
    """
    metadata = board.get("metadata") or {}
    cached_sitemap = metadata.get("sitemap_url")
    new_sitemap_url: str | None = None

    if cached_sitemap:
        root = await _try_fetch_xml(cached_sitemap, client)
        if root is None:
            log.warning("sitemap.cache_miss", cached=cached_sitemap)
            sitemap_url, root = await _discover_sitemap(board["board_url"], client)
            new_sitemap_url = sitemap_url
        else:
            if _is_sitemap_index(root):
                child_root = await _resolve_sitemap_index(root, client)
                if child_root is None:
                    raise SitemapParseError(
                        f"Sitemap index at {cached_sitemap} has no usable children"
                    )
                root = child_root
    else:
        sitemap_url, root = await _discover_sitemap(board["board_url"], client)
        new_sitemap_url = sitemap_url
        log.info("sitemap.discovered", board_url=board["board_url"], sitemap_url=sitemap_url)

    urls = _extract_urls(root)

    if len(urls) > MAX_URLS:
        log.warning("sitemap.truncated", total=len(urls), cap=MAX_URLS)
        urls = sorted(urls)[:MAX_URLS]

    return set(urls), new_sitemap_url


async def can_handle(url: str, client) -> dict | None:
    """Try to discover a sitemap — if found, return its URL as metadata."""
    try:
        sitemap_url, _root = await _discover_sitemap(url, client)
        return {"sitemap_url": sitemap_url}
    except SitemapDiscoveryError:
        return None


register("sitemap", discover, cost=50, can_handle=can_handle)
