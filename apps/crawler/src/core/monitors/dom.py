"""DOM-based job URL discovery monitor.

Extracts job links from a career page's HTML.

By default (``render: false``), fetches via static HTTP and parses ``<a>``
tags.  Set ``render: true`` to render with Playwright for JS-heavy SPAs.

Requires playwright when ``render`` is true:
``uv run playwright install chromium``
"""

from __future__ import annotations

import asyncio
import random
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

MAX_URLS = 50_000
_MAX_PAGINATION_PAGES = 10_000

# Browser-pagination fetch budget. Playwright fetches are slower than
# httpx (the JS engine + page context add tens of ms), and the page is
# shared per-board — every retry holds the worker's browser slot. Keep
# this smaller than ``fetch_with_retry``'s default of 3.
_BROWSER_FETCH_RETRIES = 2
_BROWSER_FETCH_BASE_DELAY = 0.5
_BROWSER_FETCH_MAX_CHARS = 500_000

# JS executed inside the Playwright page. Returns ``{status, text}`` so
# HTTP-level errors (which ``fetch`` doesn't reject on in JS) are
# observable on the Python side. ``r.text()`` rejects on a body decode
# error; that surfaces as a ``page.evaluate`` exception.
_BROWSER_FETCH_JS = (
    "async (url) => { "
    "const r = await fetch(url); "
    "return { status: r.status, text: await r.text() }; "
    "}"
)

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


async def _fetch_via_page(
    page,
    url: str,
    *,
    retries: int = _BROWSER_FETCH_RETRIES,
    base_delay: float = _BROWSER_FETCH_BASE_DELAY,
) -> str | None:
    """Fetch ``url`` via Playwright ``page.evaluate(fetch(...))`` with bounded retries.

    Returns:
        - ``str`` (truncated to ``_BROWSER_FETCH_MAX_CHARS``) on HTTP 200
          with a **non-empty** body.
        - ``None`` on HTTP 404 / 410 (legitimate end-of-pagination), or
          any other non-retryable 4xx (lenient stop, mirrors the
          httpx-side ``fetch_with_retry``).

    Raises:
        :exc:`PaginationFetchError` when *retries* attempts have all
        hit a retryable failure (5xx including Cloudflare 520-526/530,
        408, 425, 429, **200-with-empty-body**, or a Playwright
        ``page.evaluate`` exception — timeout, network error, page
        closed). The caller is expected to propagate so
        ``_process_one_board_streaming`` records the run as a failure
        rather than a partial success — the fix for the silent-
        truncation bug from #2737, extended in #2739 to cover empty-200.

    Empty-200 handling (#2739). Symmetric with the static httpx path:
    a 200 with an empty body is transient (anti-bot challenge dropping
    the body, partial Cloudflare response, origin glitch) — retry,
    then raise. Returning ``""`` would cascade through
    ``_paginate_urls``'s ``if not html: break`` and tombstone the
    un-fetched tail.

    Backoff: ``base_delay × 2^attempt × (0.5 + random())`` between
    retries. Fewer retries than the static path (Playwright fetches
    are slower and share the per-board browser context).
    """
    from src.shared.http_retry import (
        END_OF_PAGINATION_STATUSES,
        PaginationFetchError,
        is_retryable_status,
    )

    last_exc: BaseException | None = None
    last_status: int | None = None

    for attempt in range(retries):
        try:
            result = await page.evaluate(_BROWSER_FETCH_JS, url)
            # ``result`` is the JS object literal we constructed above —
            # ``{status, text}``. If something upstream malformed it
            # (anti-bot script substituting a Promise rejection, page
            # navigation completing the evaluate with a non-dict value),
            # ``result["status"]`` raises ``AttributeError`` /
            # ``TypeError`` and falls through to the ``except Exception``
            # branch below — retried, then surfaced as
            # ``PaginationFetchError``. No defensive shape-check needed.
            status = result["status"]
            text = result.get("text") or ""
            last_status = status
            if status == 200:
                if text:
                    return text[:_BROWSER_FETCH_MAX_CHARS]
                # Empty-200 (#2739): transient, fall through to backoff.
                last_exc = None
                log.info(
                    "dom.pagination.browser_fetch_empty_200",
                    url=url,
                    attempt=attempt + 1,
                )
            elif status in END_OF_PAGINATION_STATUSES:
                return None
            elif is_retryable_status(status):
                last_exc = None  # status-only, no exception
            else:
                # Other 4xx (auth, forbidden, bad-request) — not
                # transient, not "end of pagination" canonically.
                # Mirror the httpx path: lenient stop, logged so
                # anomalies are observable.
                log.warning(
                    "dom.pagination.browser_fetch_non_retryable_status",
                    url=url,
                    status=status,
                )
                return None
        except Exception as exc:  # page.evaluate raised — timeout, navigation, page closed
            last_exc = exc
            last_status = None

        if attempt < retries - 1:
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "dom.pagination.browser_fetch_backoff",
                url=url,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                last_status=last_status,
                last_error=type(last_exc).__name__ if last_exc else None,
            )
            await asyncio.sleep(delay)

    raise PaginationFetchError(
        url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_exc).__name__ if last_exc else None,
    )


async def _paginate_urls(
    board_url: str,
    pagination: dict,
    initial_urls: set[str],
    client: httpx.AsyncClient,
    page=None,
    url_matcher: re.Pattern | None = None,
) -> set[str]:
    """Fetch paginated pages and merge discovered links with *initial_urls*.

    Supports two URL modes:
    - ``param_name``: appends ``?param=value`` query parameter (default).
    - ``url_template``: formats a URL template containing ``{page}`` with the
      current page value — for path-based pagination.

    Failure semantics (#2722, #2737, #2739). Both fetch paths use
    bounded retries with exponential backoff and full jitter. Empty-200
    classification is symmetric across the two paths and treated as
    transient (retry, then raise) rather than end-of-pagination — the
    fix from #2739 closing the silent-truncation hole on empty bodies
    served as 200 (anti-bot challenge dropping body, partial CDN
    response, origin glitch).

    - Static httpx (``pagination.browser=false``) — :func:`fetch_with_retry`.
    - Browser (``pagination.browser=true``) — :func:`_fetch_via_page`, which
      runs ``fetch`` inside the Playwright page and inspects the response
      status. Smaller retry budget than the httpx path because Playwright
      fetches are slower and share the per-board browser context.

    Both fetchers:

    - Return ``None`` on 404/410 (legitimate end-of-pagination — break).
    - Return the body on 200 (continue).
    - Return ``None`` on other 4xx (e.g. 403) — lenient stop so
      misconfigured paginators don't poison the run as a failure.
    - **Raise** :exc:`PaginationFetchError` on persistent 5xx, 429,
      timeout, network error, or Playwright ``page.evaluate`` exception
      after the retry budget. The exception propagates out of
      ``dom_discover`` and lands in
      ``_process_one_board_streaming``'s generic ``except Exception``,
      which records the run as a failure (``_RECORD_FAILURE`` →
      consecutive_failures++ with exponential backoff). Critically,
      ``_MARK_GONE_BY_TIMESTAMP`` is **not** called, so a transient
      origin failure mid-pagination cannot tombstone the URLs that
      live on the unfetched pages — the fix for the 2026-04-26 NHS
      spike (#2722) and the matching ``pagination.browser=true``
      hole (#2737, ``lenovo-careers``).
    """
    from src.shared.api_sniff import set_url_param
    from src.shared.http_retry import fetch_with_retry

    url_template = pagination.get("url_template")
    param_name = pagination.get("param_name")
    start = pagination.get("start", pagination.get("start_value", 1))
    increment = pagination.get("increment", 1)
    max_pages = min(pagination.get("max_pages", _MAX_PAGINATION_PAGES), _MAX_PAGINATION_PAGES)
    use_browser = pagination.get("browser", False) and page is not None

    all_urls = set(initial_urls)
    value = start + increment

    for page_num in range(2, max_pages + 1):
        if url_template:
            page_url = url_template.format(page=value)
        else:
            page_url = set_url_param(board_url, param_name, value)

        if use_browser:
            html = await _fetch_via_page(page, page_url)
        else:
            html = await fetch_with_retry(client, page_url)

        if not html:
            # Legitimate end-of-pagination (404/410, empty body, or
            # browser fetch returned None). Caller's contract: a
            # successful run with the URLs accumulated so far.
            log.info("dom.pagination.end", page=page_num, url=page_url)
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
            async with open_page(pw, combined, use_proxy=bool(metadata.get("proxy"))) as page:
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

            async with (
                async_playwright() as p,
                open_page(p, combined, use_proxy=bool(metadata.get("proxy"))) as page,
            ):
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

    # Exclude the board URL itself — it's the listing page, not a job
    normalized_board = board_url.rstrip("/")
    urls = {u for u in urls if u.rstrip("/") != normalized_board}

    if len(urls) > MAX_URLS:
        log.warning("dom.truncated", total=len(urls), cap=MAX_URLS)
        urls = set(sorted(urls)[:MAX_URLS])

    log.info("dom.complete", board_url=board_url, urls_found=len(urls), render=render)
    return urls


register("dom", dom_discover, cost=100, can_handle=can_handle)
