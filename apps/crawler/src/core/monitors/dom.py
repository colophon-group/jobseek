"""DOM-based job URL discovery monitor.

Extracts job links from a career page's HTML.

By default (``render: false``), fetches via static HTTP and parses ``<a>``
tags.  Set ``render: true`` to render with Playwright for JS-heavy SPAs.

Requires playwright when ``render`` is true:
``uv run playwright install chromium``
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import structlog

from src.core.monitors import register
from src.shared.browser import BROWSER_KEYS, navigate, open_page, run_actions

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_URLS = 10_000
_MAX_PAGINATION_PAGES = 10_000

_JOB_KEYWORDS = frozenset({"job", "career", "position", "posting", "opening", "role", "vacancy"})


def _build_url_matcher(url_filter) -> re.Pattern | None:
    """Compile *url_filter* config into a regex, or ``None`` to use keywords."""
    if not url_filter:
        return None
    if isinstance(url_filter, str):
        return re.compile(url_filter)
    include = url_filter.get("include")
    return re.compile(include) if include else None


# ---------------------------------------------------------------------------
# Static link extraction (no browser)
# ---------------------------------------------------------------------------


class _LinkExtractor(HTMLParser):
    """Extract href values from ``<a>`` tags."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    self.hrefs.append(value)


def _extract_links_static(
    html: str,
    base_url: str,
    url_matcher: re.Pattern | None = None,
) -> set[str]:
    """Parse ``<a href>`` links from raw HTML and filter for job URLs.

    When *url_matcher* is provided it is used instead of the default keyword
    filter, allowing non-English career pages to work.
    """
    parser = _LinkExtractor()
    parser.feed(html)

    urls: set[str] = set()
    for href in parser.hrefs:
        absolute = urljoin(base_url, href)
        if not absolute.startswith("http"):
            continue
        if url_matcher is not None:
            if url_matcher.search(absolute):
                urls.add(absolute)
        elif any(kw in absolute.lower() for kw in _JOB_KEYWORDS):
            urls.add(absolute)
    return urls


# ---------------------------------------------------------------------------
# Playwright link extraction
# ---------------------------------------------------------------------------


async def _extract_links_rendered(
    page,
    metadata: dict,
    url_matcher: re.Pattern | None = None,
) -> set[str]:
    """Navigate, run actions, and extract job links from a Playwright page."""
    board_url = metadata["_board_url"]
    browser_config = {k: v for k, v in metadata.items() if k in BROWSER_KEYS}
    await navigate(page, board_url, browser_config)
    await run_actions(page, browser_config.get("actions", []))

    links = await page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(h => h.startsWith('http'))
    """)
    urls: set[str] = set()
    for link in links:
        if url_matcher is not None:
            if url_matcher.search(link):
                urls.add(link)
        elif any(kw in link.lower() for kw in _JOB_KEYWORDS):
            urls.add(link)
    return urls


# ---------------------------------------------------------------------------
# Pagination — fetch additional pages and merge links
# ---------------------------------------------------------------------------


async def _fetch_via_page(page, url: str) -> str | None:
    """Fetch HTML via ``page.evaluate(fetch(...))`` inside a Playwright context."""
    try:
        return await page.evaluate(
            "async (url) => { const r = await fetch(url); return await r.text(); }",
            url,
        )
    except Exception:
        log.warning("dom.pagination.browser_fetch_failed", url=url)
        return None


async def _paginate_urls(
    board_url: str,
    pagination: dict,
    initial_urls: set[str],
    client: httpx.AsyncClient,
    page=None,
    url_matcher: re.Pattern | None = None,
) -> set[str]:
    """Fetch paginated pages and merge discovered links with *initial_urls*."""
    from src.core.monitors import fetch_page_text
    from src.shared.api_sniff import set_url_param

    param_name = pagination["param_name"]
    start = pagination.get("start", 1)
    increment = pagination.get("increment", 1)
    max_pages = min(pagination.get("max_pages", _MAX_PAGINATION_PAGES), _MAX_PAGINATION_PAGES)
    use_browser = pagination.get("browser", False) and page is not None

    all_urls = set(initial_urls)
    value = start + increment

    for page_num in range(2, max_pages + 1):
        page_url = set_url_param(board_url, param_name, value)

        if use_browser:
            html = await _fetch_via_page(page, page_url)
        else:
            html = await fetch_page_text(page_url, client)

        if not html:
            log.info("dom.pagination.fetch_failed", page=page_num, url=page_url)
            break

        new_urls = _extract_links_static(html, page_url, url_matcher)
        added = new_urls - all_urls
        if not added:
            log.info("dom.pagination.no_new_urls", page=page_num)
            break

        all_urls |= new_urls
        log.debug("dom.pagination.page", page=page_num, new=len(added), total=len(all_urls))
        value += increment

    return all_urls


# ---------------------------------------------------------------------------
# can_handle — static probe for link discovery
# ---------------------------------------------------------------------------


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Probe whether *url* has discoverable job links via static fetch.

    Returns metadata dict when job links are found, None otherwise.
    """
    from src.core.monitors import fetch_page_text

    html = await fetch_page_text(url, client)
    if not html:
        return None

    urls = _extract_links_static(html, url)
    if urls:
        return {"urls": len(urls)}
    return None


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


async def dom_discover(board: dict, client: httpx.AsyncClient = None, pw=None) -> set[str]:
    """Discover job URLs from a career page."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    render = metadata.get("render", False)
    actions = metadata.get("actions")
    pagination = metadata.get("pagination")
    url_matcher = _build_url_matcher(metadata.get("url_filter"))

    if not render and actions:
        log.warning(
            "dom.misconfiguration",
            board_url=board_url,
            detail="actions require render=true; overriding render to true",
        )
        render = True

    if render:
        combined = {**metadata, "_board_url": board_url}

        if pw is not None:
            async with open_page(pw, combined) as page:
                urls = await _extract_links_rendered(page, combined, url_matcher)
                if pagination:
                    browser_page = page if pagination.get("browser") else None
                    urls = await _paginate_urls(
                        board_url,
                        pagination,
                        urls,
                        client,
                        browser_page,
                        url_matcher,
                    )
        else:
            try:
                from playwright.async_api import async_playwright
            except ImportError as err:
                raise RuntimeError(
                    "playwright is required for the dom monitor with render=true. "
                    "Install with: uv sync --group dev && uv run playwright install chromium"
                ) from err

            async with async_playwright() as p, open_page(p, combined) as page:
                urls = await _extract_links_rendered(page, combined, url_matcher)
                if pagination:
                    browser_page = page if pagination.get("browser") else None
                    urls = await _paginate_urls(
                        board_url,
                        pagination,
                        urls,
                        client,
                        browser_page,
                        url_matcher,
                    )
    else:
        from src.core.monitors import fetch_page_text

        html = await fetch_page_text(board_url, client)
        if not html:
            log.warning("dom.fetch_failed", board_url=board_url)
            return set()
        urls = _extract_links_static(html, board_url, url_matcher)
        if pagination:
            urls = await _paginate_urls(
                board_url,
                pagination,
                urls,
                client,
                url_matcher=url_matcher,
            )

    if len(urls) > MAX_URLS:
        log.warning("dom.truncated", total=len(urls), cap=MAX_URLS)
        urls = set(sorted(urls)[:MAX_URLS])

    log.info("dom.complete", board_url=board_url, urls_found=len(urls), render=render)
    return urls


register("dom", dom_discover, cost=100, can_handle=can_handle)
