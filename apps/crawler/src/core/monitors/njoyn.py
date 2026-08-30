"""Njoyn browser monitor.

Njoyn's classic ``XWeb.asp`` listings are session-bound POST forms. The
visible ``NEXT`` control submits the current form and keeps the page URL
unchanged, so query-parameter pagination and browser-context ``fetch`` calls
only ever return the first page. This monitor keeps one browser context,
clicks the real pagination control, and fails closed when the advertised
result count is not fully collected.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import structlog

from src.core.monitors import register
from src.core.monitors.dom import _raise_if_bot_challenge
from src.shared.browser import BROWSER_KEYS, navigate, open_page, safe_content

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_PAGES = 200
_PAGE_CHANGE_POLL_MS = 500

_RESULT_COUNT_RE = re.compile(r"\bSearch\s+Results\s*\(([\d,\s]+)\)", re.IGNORECASE)
_NEXT_SELECTORS = (
    'input[type="submit"][value="NEXT" i]:not([disabled])',
    'input[type="button"][value="NEXT" i]:not([disabled])',
    'input[type="image"][alt*="next" i]:not([disabled])',
    'button:has-text("NEXT"):not([disabled])',
    'a:has-text("NEXT")',
)


def _is_njoyn_board(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme.lower() == "https"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and host.endswith(".njoyn.com")
        and "/xweb/" in parsed.path.lower()
    )


def _query_values(url: str) -> dict[str, list[str]]:
    return {
        key.casefold(): values
        for key, values in parse_qs(urlsplit(url).query, keep_blank_values=True).items()
    }


def _single_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if values is None or len(values) != 1:
        return None
    value = values[0].strip()
    return value or None


def _board_identity(url: str) -> tuple[str, int, str] | None:
    if not _is_njoyn_board(url):
        return None
    parsed = urlsplit(url)
    clid = _single_query_value(_query_values(url), "clid")
    if clid is None:
        return None
    return (parsed.hostname or "").lower(), parsed.port or 443, clid.casefold()


def _is_njoyn_listing_url(url: str) -> bool:
    if _board_identity(url) is None:
        return False
    page = _single_query_value(_query_values(url), "page")
    return page is not None and page.casefold() == "joblisting"


def _is_job_detail_url(url: str, *, board_url: str | None = None) -> bool:
    identity = _board_identity(url)
    if identity is None:
        return False
    if board_url is not None and identity != _board_identity(board_url):
        return False
    query = _query_values(url)
    page = _single_query_value(query, "page")
    return (
        page is not None
        and page.casefold() == "jobdetails"
        and _single_query_value(query, "jobid") is not None
        and _single_query_value(query, "brid") is not None
    )


def _expected_count(text: str) -> int | None:
    match = _RESULT_COUNT_RE.search(text)
    if not match:
        return None
    return int(re.sub(r"\D", "", match.group(1)))


async def _page_snapshot(page, board_url: str) -> tuple[set[str], str]:
    snapshot = await page.evaluate(
        """() => ({
            links: Array.from(document.querySelectorAll('a[href]')).map(a => a.href),
            text: document.body ? document.body.innerText : ''
        })"""
    )
    links = {url for url in snapshot["links"] if _is_job_detail_url(url, board_url=board_url)}
    return links, snapshot["text"]


async def _next_control(page):
    for selector in _NEXT_SELECTORS:
        locator = page.locator(selector).first
        if await locator.count() > 0:
            return locator
    return None


async def _discover_page(page, board_url: str, config: dict) -> set[str]:
    await navigate(page, board_url, config)

    html = await safe_content(page)
    _raise_if_bot_challenge(page.url or board_url, html)
    current_page_urls, text = await _page_snapshot(page, board_url)
    urls = set(current_page_urls)
    expected = _expected_count(text)
    if expected is None:
        raise RuntimeError("Njoyn listing is missing its Search Results total")
    if expected is not None and expected > MAX_JOBS:
        raise RuntimeError(f"Njoyn result count {expected} exceeds cap {MAX_JOBS}")

    max_pages = min(int(config.get("max_pages", MAX_PAGES)), MAX_PAGES)
    if max_pages < 1:
        raise ValueError("Njoyn max_pages must be at least 1")
    wait_ms = max(0, int(config.get("page_wait_ms", 1000)))
    page_change_timeout_ms = min(
        60_000,
        max(_PAGE_CHANGE_POLL_MS, int(config.get("page_change_timeout_ms", 15_000))),
    )
    page_change_polls = (page_change_timeout_ms + _PAGE_CHANGE_POLL_MS - 1) // _PAGE_CHANGE_POLL_MS

    for page_number in range(2, max_pages + 1):
        if len(urls) >= expected:
            break

        next_control = await _next_control(page)
        if next_control is None:
            break

        before = len(urls)
        await next_control.click()
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
        if wait_ms:
            await asyncio.sleep(wait_ms / 1000)

        page_urls: set[str] | None = None
        for poll in range(page_change_polls):
            html = await safe_content(page)
            _raise_if_bot_challenge(page.url or board_url, html)
            candidate_urls, _ = await _page_snapshot(page, board_url)
            if candidate_urls != current_page_urls:
                page_urls = candidate_urls
                break
            if poll + 1 < page_change_polls:
                await asyncio.sleep(_PAGE_CHANGE_POLL_MS / 1000)
        if page_urls is None:
            raise RuntimeError(
                f"Njoyn pagination repeated page {page_number - 1}; "
                f"collected {len(urls)} of {expected} jobs"
            )

        current_page_urls = page_urls
        urls.update(page_urls)
        if len(urls) == before:
            raise RuntimeError(
                f"Njoyn pagination added no jobs after page {page_number - 1}; "
                f"collected {len(urls)} of {expected} jobs"
            )
    else:
        if await _next_control(page) is not None:
            raise RuntimeError(f"Njoyn pagination hit max_pages={max_pages}")

    if len(urls) != expected:
        raise RuntimeError(f"Njoyn count mismatch: collected {len(urls)} of {expected} jobs")
    if not urls and expected:
        raise RuntimeError("Njoyn listing returned no job-detail URLs")

    log.info(
        "njoyn.complete",
        board_url=board_url,
        urls_found=len(urls),
        expected=expected,
    )
    return urls


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Collect every Njoyn job URL through the listing's POST pagination."""
    _ = client
    board_url = board["board_url"]
    if not _is_njoyn_listing_url(board_url):
        raise ValueError(f"Unsupported Njoyn board URL: {board_url!r}")

    metadata = board.get("metadata") or {}
    browser_config = {key: value for key, value in metadata.items() if key in BROWSER_KEYS}
    browser_config.setdefault("wait", "domcontentloaded")
    browser_config.setdefault("timeout", 60_000)
    # The browser image installs system Chrome plus Chromium's headless shell,
    # but not Playwright's regular Chromium executable. Njoyn needs a headful
    # persistent context for its session-bound form, so pin the installed
    # Chrome channel even when an older board config omits it.
    browser_config.setdefault("channel", "chrome")

    async def _run(playwright) -> set[str]:
        async with open_page(
            playwright,
            browser_config,
            use_proxy=bool(metadata.get("proxy")),
            target_url=board_url,
        ) as page:
            return await _discover_page(page, board_url, metadata | browser_config)

    if pw is not None:
        return await _run(pw)

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is required for the Njoyn monitor") from exc

    async with async_playwright() as playwright:
        return await _run(playwright)


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Recognize Njoyn's stable public XWeb listing URL shape."""
    _ = client, pw
    if not _is_njoyn_listing_url(url):
        return None
    return {
        "wait": "domcontentloaded",
        "timeout": 60_000,
        "persistent_context": True,
        "channel": "chrome",
        "headless": False,
        "stealth": True,
        "proxy": True,
    }


register("njoyn", discover, cost=80, can_handle=can_handle)
