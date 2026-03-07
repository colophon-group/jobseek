"""Umantis ATS monitor (Haufe Group / Abacus).

Server-rendered HTML listing pages at ``recruitingapp-{ID}[.de].umantis.com``.
Job links use class ``HSTableLinkSubTitle`` across all customer templates.

Listing:  GET /Jobs/All  (paginated via ``tc{tableNr}=p{page}``)
Detail:   /Vacancies/{id}/Description/{langId}

URL-only monitor — returns ``set[str]`` of job detail URLs.
Templates vary widely across customers; no shared structured data
(no JSON-LD, no common DOM) on detail pages.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from src.core.monitors import fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 10_000
MAX_PAGES = 100
PAGE_SIZE = 10  # Umantis default per page

# recruitingapp-{ID}[.de|.ch].umantis.com
_HOST_RE = re.compile(r"^recruitingapp-(\d+)(?:\.\w+)?\.umantis\.com$", re.IGNORECASE)

_CUSTOM_HOST_RE = re.compile(r"\.umantis\.com$", re.IGNORECASE)

_IGNORE_SUBDOMAINS = {"www", "api", "app", "static", "cdn", "mail", "help"}

_PAGE_MARKERS = [
    re.compile(r"recruitingapp-\d+(?:\.\w+)?\.umantis\.com"),
    re.compile(r"umantis\.com/Vacancies/"),
    re.compile(r"umantis\.com/Jobs/"),
    re.compile(r"globalUmantisParams"),
    re.compile(r"HSTableLinkSubTitle"),
]


# ── URL helpers ─────────────────────────────────────────────────────────


def _parse_host(url: str) -> tuple[str | None, str | None]:
    """Extract (customer_id, region) from an Umantis URL.

    Returns e.g. ("2698", "") for .umantis.com or ("5181", "de") for .de.umantis.com.
    Returns (None, None) for non-Umantis URLs.
    """
    host = urlparse(url).hostname or ""
    m = _HOST_RE.match(host)
    if not m:
        return None, None
    cid = m.group(1)
    # Determine region from subdomain: recruitingapp-{ID}.de.umantis.com
    parts = host.split(".")
    # e.g. ['recruitingapp-{ID}', 'de', 'umantis', 'com']
    if len(parts) == 4:
        return cid, parts[1]  # "de", "ch", etc.
    return cid, ""


def _base_url(customer_id: str, region: str = "") -> str:
    """Build the base URL for a customer."""
    if region:
        return f"https://recruitingapp-{customer_id}.{region}.umantis.com"
    return f"https://recruitingapp-{customer_id}.umantis.com"


# ── Listing page parsing ────────────────────────────────────────────────


class _JobLinkParser(HTMLParser):
    """Extract job links with class ``HSTableLinkSubTitle`` from listing HTML."""

    def __init__(self, base_url: str):
        super().__init__()
        self.base = base_url
        self.jobs: list[tuple[str, str]] = []  # (url, title)
        self._in_link = False
        self._current_url: str | None = None
        self._current_title: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        if "HSTableLinkSubTitle" not in cls:
            return
        href = attrs_dict.get("href")
        if not href or "/Vacancies/" not in href:
            return
        self._in_link = True
        # Strip query params from vacancy URL for cleaner output
        clean = href.split("?")[0]
        self._current_url = urljoin(self.base, clean)
        self._current_title = ""

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._current_title += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            self._in_link = False
            title = self._current_title.strip()
            if self._current_url and title:
                self.jobs.append((self._current_url, title))
            self._current_url = None
            self._current_title = ""


def _extract_table_nr(html: str) -> str | None:
    """Extract the table number used for pagination from listing HTML.

    Looks for ``initial-data-string`` attribute on the ``<table-navigation>``
    Vue component, or falls back to ``tc(\\d+)=`` in pagination URLs.
    """
    # Primary: from initial-data-string JSON
    m = re.search(r'"TableNr"\s*:\s*"(\d+)"', html)
    if m:
        return m.group(1)
    # Fallback: from pagination URL pattern tc{nr}=p{page}
    m = re.search(r"tc(\d+)=p\d+", html)
    if m:
        return m.group(1)
    return None


def _parse_jobs_from_html(html: str, base_url: str) -> list[tuple[str, str]]:
    """Parse job links from listing HTML. Returns [(url, title), ...]."""
    parser = _JobLinkParser(base_url)
    parser.feed(html)
    return parser.jobs


# ── Discovery ──────────────────────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover job URLs from an Umantis board.

    Paginates through /Jobs/All using tc{tableNr}=p{page} params.
    Returns a set of job detail URLs.
    """
    metadata = board.get("metadata") or {}
    customer_id = metadata.get("customer_id")
    region = metadata.get("region", "")
    cname = metadata.get("cname")

    if not customer_id:
        # Try to extract from board URL
        cid, reg = _parse_host(board["board_url"])
        if cid:
            customer_id = cid
            if reg is not None:
                region = reg
        else:
            # Check for CNAME .umantis.com domain
            host = (urlparse(board["board_url"]).hostname or "").lower()
            if host.endswith(".umantis.com"):
                cname = host
            else:
                raise ValueError(
                    f"Umantis monitor requires 'customer_id' in metadata "
                    f"for board {board['board_url']!r}"
                )

    if cname:
        parsed = urlparse(board["board_url"])
        base = f"{parsed.scheme}://{cname}"
    else:
        base = _base_url(customer_id, region)
    listing_path = metadata.get("listing_path", "/Jobs/All")

    # Fetch first page
    listing_url = f"{base}{listing_path}"
    resp = await client.get(listing_url, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    jobs = _parse_jobs_from_html(html, base)
    table_nr = _extract_table_nr(html)

    # Paginate
    if table_nr:
        page = 2
        while len(jobs) < MAX_JOBS and page <= MAX_PAGES:
            page_url = f"{listing_url}?tc{table_nr}=p{page}"
            try:
                resp = await client.get(page_url, follow_redirects=True)
                if resp.status_code != 200:
                    break
                page_jobs = _parse_jobs_from_html(resp.text, base)
                if not page_jobs:
                    break
                # Check for duplicates (pagination loops)
                new_urls = {url for url, _ in page_jobs}
                existing_urls = {url for url, _ in jobs}
                if not (new_urls - existing_urls):
                    break
                jobs.extend(page_jobs)
                page += 1
            except Exception as exc:
                log.warning("umantis.page_error", page=page, error=str(exc))
                break

    label = cname or customer_id
    if not jobs:
        log.info("umantis.no_jobs", customer_id=label)
        return set()

    if len(jobs) > MAX_JOBS:
        log.warning("umantis.truncated", total=len(jobs), cap=MAX_JOBS)
        jobs = jobs[:MAX_JOBS]

    log.info("umantis.listed", customer_id=label, jobs=len(jobs))

    # Deduplicate by URL
    seen: set[str] = set()
    unique: set[str] = set()
    for url, _ in jobs:
        if url not in seen:
            seen.add(url)
            unique.add(url)

    return unique


# ── Probing ─────────────────────────────────────────────────────────────


async def _probe_listing(customer_id: str, region: str, client: httpx.AsyncClient) -> int | None:
    """Probe a listing page and return job count, or None if not found."""
    base = _base_url(customer_id, region)
    try:
        resp = await client.get(f"{base}/Jobs/All", follow_redirects=True)
        if resp.status_code != 200:
            return None
        jobs = _parse_jobs_from_html(resp.text, base)
        if jobs:
            return len(jobs)
        # Page loaded but no jobs found — might still be valid
        if "umantis" in resp.text.lower():
            return 0
        return None
    except Exception:
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Umantis: URL pattern match or HTML marker scan."""
    # 1. URL pattern match
    cid, region = _parse_host(url)
    if cid:
        if client:
            count = await _probe_listing(cid, region or "", client)
            if count is not None:
                result: dict = {"customer_id": cid, "region": region or ""}
                if count > 0:
                    result["jobs"] = count
                return result
        return {"customer_id": cid, "region": region or ""}

    # 2. Check for custom CNAME (.umantis.com but not recruitingapp-{ID})
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".umantis.com"):
        sub = host.removesuffix(".umantis.com").split(".")[-1]
        if sub and sub not in _IGNORE_SUBDOMAINS:
            if not client:
                return None
            html = await fetch_page_text(url, client)
            if not html:
                return None
            # Try to find recruitingapp-{ID} reference in page
            m = re.search(r"recruitingapp-(\d+)", html)
            if m:
                cid = m.group(1)
                reg_match = re.search(r"recruitingapp-\d+\.(\w+)\.umantis\.com", html)
                region = reg_match.group(1) if reg_match else ""
                count = await _probe_listing(cid, region, client)
                result = {"customer_id": cid, "region": region}
                if count is not None and count > 0:
                    result["jobs"] = count
                return result
            # No recruitingapp reference — CNAME serves directly
            has_marker = any(marker.search(html) for marker in _PAGE_MARKERS)
            if has_marker:
                parsed = urlparse(url)
                base = f"{parsed.scheme}://{parsed.hostname}"
                jobs = _parse_jobs_from_html(html, base)
                result = {"customer_id": sub, "cname": host, "region": ""}
                if jobs:
                    result["jobs"] = len(jobs)
                return result
            return None

    # 3. HTML marker scan (for career pages embedding Umantis)
    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if not html:
        return None

    has_marker = any(marker.search(html) for marker in _PAGE_MARKERS)
    if not has_marker:
        return None

    # Try to extract customer ID from the page
    m = re.search(r"recruitingapp-(\d+)", html)
    if not m:
        return None

    cid = m.group(1)
    reg_match = re.search(r"recruitingapp-\d+\.(\w+)\.umantis\.com", html)
    region = reg_match.group(1) if reg_match else ""
    count = await _probe_listing(cid, region, client)
    result = {"customer_id": cid, "region": region}
    if count is not None and count > 0:
        result["jobs"] = count
    return result


register("umantis", discover, cost=15, can_handle=can_handle)
