"""XML sitemap monitor with automatic sitemap URL discovery."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import structlog

from src.core.monitors import register

log = structlog.get_logger()

MAX_URLS = 50_000
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
# Some generators emit https:// instead of http:// in the namespace declaration.
NS_HTTPS = "{https://www.sitemaps.org/schemas/sitemap/0.9}"

_JOB_KEYWORDS = ("job", "career", "posting", "position", "vacancy", "opening")

# Sitemaps and robots.txt are canonically bot-facing resources. The shared
# HTTP client sends a Chrome User-Agent to evade bot-detection on regular
# HTML pages (issue #2193 — Deloitte / Infosys / L'Oreal / TSMC / Bain
# reject non-browser UAs on /careers). Meta inverts that gate: a Chrome UA
# gets HTTP 400 on ``metacareers.com/jobsearch/sitemap.xml`` while any
# identified-bot UA gets the sitemap XML. The shared UA is therefore wrong
# for this monitor — override with a self-identifying crawler UA and a
# minimal Accept so we're eligible for both gates (verified on a sample of
# seven currently-working sitemap boards that return identical XML under
# either UA).
_SITEMAP_HEADERS = {
    "User-Agent": "jobseek-crawler (+https://jseek.co/)",
    "Accept": "application/xml,text/xml,*/*;q=0.8",
}


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


def _detect_ns(root: ET.Element) -> str:
    """Return the XML namespace prefix (e.g. ``{http://...}``) from the root tag.

    Falls back to the canonical ``NS`` constant when the tag carries no namespace
    so that callers always get a usable prefix string.
    """
    tag = root.tag
    if tag.startswith("{"):
        return tag[: tag.index("}") + 1]
    return NS


def _extract_urls(root: ET.Element) -> list[str]:
    ns = _detect_ns(root)
    urls: list[str] = []
    for url_el in root.findall(f"{ns}url"):
        loc = url_el.find(f"{ns}loc")
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
    ns = _detect_ns(root)
    urls: list[str] = []
    for el in root.findall(f"{ns}sitemap"):
        loc = el.find(f"{ns}loc")
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
    """Lenient XML fetch — returns ``None`` on any error.

    Used by sitemap *discovery* (walking candidate URLs) where any
    failure should fall through to the next candidate without
    retries. For *child sitemap* fetching from an index — where a
    silent failure causes URL truncation (#2722) — use
    :func:`_fetch_child_xml` instead.

    TDM-Reservation respect (#2842, #2925). The W3C opt-out signal is
    honored even on the lenient discovery path: a publisher who
    declares ``tdm-reservation: 1`` on their sitemap doesn't lose the
    signal just because we're walking candidate URLs.
    :class:`TDMReservedError` is *not* swallowed by the broad
    ``except`` — it propagates up to the discover/can_handle wrapper
    and onward to ``_process_one_board_streaming`` for graceful skip.
    Sitemap bodies are XML, not HTML, so the body-meta scan has no
    realistic match surface — the check is effectively header-only
    here, but we pass ``body_excerpt`` for parity with the static
    httpx hook in ``http_retry.py`` (and on the chance a sitemap
    serves HTML 200 with a meta declaration on the same origin).
    """
    from src.shared.tdm import TDMReservedError
    from src.shared.tdm import check_response as _tdm_check

    try:
        resp = await client.get(url, headers=_SITEMAP_HEADERS)
        if resp.status_code != 200:
            return None
        # Run the TDM check before content-type filtering so that an
        # HTML 200 response declaring opt-out (rare but valid: a CDN
        # serving its branded "no sitemap" page from the same origin
        # while still emitting the publisher's policy header) is
        # honored rather than silently dropped on the content-type
        # gate.
        _tdm_check(resp, body_excerpt=resp.text)
        content_type = resp.headers.get("content-type", "")
        if "xml" not in content_type:
            return None
        return ET.fromstring(resp.text)
    except TDMReservedError:
        # Publisher policy declaration — propagate, never swallow.
        raise
    except (httpx.HTTPError, ET.ParseError):
        return None


async def _fetch_child_xml(url: str, client: httpx.AsyncClient) -> ET.Element | None:
    """Strict XML fetch for child sitemaps inside a sitemap index.

    Returns the parsed root on 200 + parseable XML, or ``None`` on
    legitimate not-found (404/410) and on non-parseable bodies. **Raises**
    :exc:`PaginationFetchError` when transient errors (5xx, 429, timeout,
    network) persist past the retry budget.

    The 2026-04-26 NHS spike (#2722) showed why this distinction
    matters: a multi-shard sitemap (e.g. ``sitemap-jobs-1.xml`` …
    ``sitemap-jobs-12.xml``) where one shard returns 503 silently
    drops the URLs in that shard. The lenient
    :func:`_try_fetch_xml` variant treats that as a benign skip,
    which the caller's ``continue`` loop then converts into a
    successful run with a partial URL set — and
    ``_MARK_GONE_BY_TIMESTAMP`` tombstones the missing URLs. Raising
    here propagates the failure to ``_process_one_board_streaming``'s
    generic ``except Exception`` so the run is recorded as a
    failure (no delistings) instead.
    """
    from src.shared.http_retry import fetch_with_retry

    text = await fetch_with_retry(client, url, headers=_SITEMAP_HEADERS)
    if text is None:
        # 404 / 410 / non-retryable 4xx — child sitemap is gone or
        # the URL is wrong. Caller's ``continue`` is appropriate: a
        # genuinely-missing shard is an upstream config issue, not a
        # silent-truncation bug.
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # 200 OK but body isn't well-formed XML. Mirror the lenient
        # variant — treat as skip rather than failure, since this is
        # usually a board-config mistake (CDN serving HTML for a
        # missing sitemap path) rather than a transient error.
        return None
    # Sanity check the root tag. Sitemap protocol mandates ``urlset``
    # or ``sitemapindex``; a CDN serving a parseable HTML 200 (e.g.
    # an SPA shell) would otherwise be accepted as a valid sitemap
    # and silently yield zero URLs from the shard.
    tag = root.tag.lower() if isinstance(root.tag, str) else ""
    if not (tag.endswith("urlset") or tag.endswith("sitemapindex")):
        log.warning("sitemap.child_xml.unexpected_root", url=url, tag=root.tag)
        return None
    return root


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
    """Discover sitemap URLs declared in /robots.txt.

    TDM-Reservation respect (#2842, #2925). The W3C opt-out signal can
    be declared on the robots.txt response itself; honor it before
    parsing the body. ``TDMReservedError`` propagates out (not
    swallowed by the broad ``except``) so a publisher who has opted
    out doesn't have their policy implicitly bypassed by the
    discovery probe. Robots.txt is plain text — meta-tag scan is
    effectively a no-op there — but ``body_excerpt`` is passed for
    symmetry with the other hooked helpers.
    """
    from src.shared.tdm import TDMReservedError
    from src.shared.tdm import check_response as _tdm_check

    parsed = urlparse(board_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url, headers=_SITEMAP_HEADERS)
        if resp.status_code != 200:
            return []
        _tdm_check(resp, body_excerpt=resp.text)
        if "xml" in resp.headers.get("content-type", ""):
            return []
        if "<html" in resp.text[:500].lower():
            return []
    except TDMReservedError:
        # Publisher policy declaration — propagate, never swallow.
        raise
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
    *,
    seen: set[str] | None = None,
) -> list[ET.Element]:
    """Fetch all child sitemaps from a sitemap index.

    Returns a list of parsed XML roots (one per child sitemap that was
    successfully fetched).  Prefers job-related children when present.
    """
    children = _extract_child_sitemaps(root)
    if not children:
        return []
    job_children = [u for u in children if _is_job_related(u)]
    targets = job_children if job_children else children
    log.debug("sitemap.index_resolved", targets=len(targets), total_children=len(children))
    if seen is None:
        seen = set()
    results: list[ET.Element] = []
    for target in targets:
        if target in seen:
            log.debug("sitemap.index_cycle_skipped", target=target)
            continue
        seen.add(target)
        # Strict fetch — transient errors raise PaginationFetchError
        # rather than returning None. A silent skip on a 503 child
        # would drop that shard's URLs and trigger the 2026-04-26
        # NHS-style truncation tombstoning (#2722).
        child_root = await _fetch_child_xml(target, client)
        if child_root is None:
            # 404 / non-XML body — genuinely missing shard, skip.
            continue
        if _is_sitemap_index(child_root):
            results.extend(await _resolve_sitemap_index(child_root, client, seen=seen))
        else:
            results.append(child_root)
    return results


async def _discover_sitemap(
    board_url: str,
    client: httpx.AsyncClient,
) -> tuple[str, list[ET.Element]]:
    """Try multiple strategies to find and fetch the sitemap XML.

    Returns (sitemap_url, parsed_xml_roots).  When the sitemap is an index,
    all child sitemaps are fetched and returned so that URLs from every
    sub-sitemap are included.
    Raises SitemapDiscoveryError if nothing works.
    """
    all_candidates = (
        _walk_up_candidates(board_url)
        + _common_nonstandard_candidates(board_url)
        + await _parse_robots_sitemaps(board_url, client)
    )

    for candidate in all_candidates:
        root = await _try_fetch_xml(candidate, client)
        if root is not None:
            if _is_sitemap_index(root):
                children = await _resolve_sitemap_index(root, client)
                if children:
                    return candidate, children
            else:
                return candidate, [root]

    raise SitemapDiscoveryError(f"No sitemap found for {board_url}")


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
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
            sitemap_url, roots = await _discover_sitemap(board["board_url"], client)
            new_sitemap_url = sitemap_url
        else:
            if _is_sitemap_index(root):
                roots = await _resolve_sitemap_index(root, client)
                if not roots:
                    raise SitemapParseError(
                        f"Sitemap index at {cached_sitemap} has no usable children"
                    )
            else:
                roots = [root]
    else:
        sitemap_url, roots = await _discover_sitemap(board["board_url"], client)
        new_sitemap_url = sitemap_url
        log.info("sitemap.discovered", board_url=board["board_url"], sitemap_url=sitemap_url)

    urls: list[str] = []
    for r in roots:
        urls.extend(_extract_urls(r))

    if len(urls) > MAX_URLS:
        log.warning("sitemap.truncated", total=len(urls), cap=MAX_URLS)
        urls = sorted(urls)[:MAX_URLS]

    return set(urls), new_sitemap_url


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Try to discover a sitemap — if found, return its URL and URL count as metadata."""
    try:
        sitemap_url, roots = await _discover_sitemap(url, client)
        url_count = sum(len(_extract_urls(r)) for r in roots)
        return {"sitemap_url": sitemap_url, "urls": url_count}
    except SitemapDiscoveryError:
        return None


register("sitemap", discover, cost=50, can_handle=can_handle)
