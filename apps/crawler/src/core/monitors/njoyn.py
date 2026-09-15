"""Njoyn browser monitor.

Njoyn's classic ``XWeb.asp`` listings are session-bound POST forms. The
visible ``NEXT`` control submits the current form and keeps the page URL
unchanged, so query-parameter pagination and browser-context ``fetch`` calls
only ever return the first page. This monitor keeps one browser context,
submits each exact hidden page index under a navigation expectation, and fails
closed when the advertised result count is not fully collected.
"""

from __future__ import annotations

import asyncio
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
_PAGE_TRANSITION_ATTEMPTS = 3
_PAGE_TRANSITION_RETRY_DELAY = 1.0

_RESULT_COUNT_RE = re.compile(r"\bSearch\s+Results\s*\(([\d,\s]+)\)", re.IGNORECASE)
_PAGE_SNAPSHOT_SCRIPT = """() => {
    const form = Array.from(document.forms).find(candidate => candidate.elements.namedItem('pn'));
    const pageInput = form ? form.elements.namedItem('pn') : null;
    return {
        links: Array.from(document.querySelectorAll('a[href]')).map(a => a.href),
        text: document.body ? document.body.innerText : '',
        pageNumber: pageInput && 'value' in pageInput ? pageInput.value : null,
    };
}"""

_SUBMIT_PAGE_SCRIPT = """targetPage => {
    const form = Array.from(document.forms).find(candidate => candidate.elements.namedItem('pn'));
    const pageInput = form ? form.elements.namedItem('pn') : null;
    if (!form || !pageInput || !('value' in pageInput)) return false;
    const action = new URL(form.action, window.location.href);
    const sameOrigin = action.origin === window.location.origin;
    const isXweb = action.pathname.toLowerCase().includes('/xweb/');
    if (!sameOrigin || !isXweb) {
        return false;
    }
    pageInput.value = String(targetPage);
    form.submit();
    return true;
}"""


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


async def _page_snapshot(page, board_url: str) -> tuple[set[str], str, int]:
    snapshot = await page.evaluate(_PAGE_SNAPSHOT_SCRIPT)
    links = {url for url in snapshot["links"] if _is_job_detail_url(url, board_url=board_url)}
    raw_page_number = snapshot.get("pageNumber")
    if not isinstance(raw_page_number, str) or re.fullmatch(r"[1-9]\d*", raw_page_number) is None:
        raise RuntimeError("Njoyn listing is missing its numeric page state")
    return links, snapshot["text"], int(raw_page_number)


async def _reset_listing_page(
    page,
    board_url: str,
    config: dict,
    *,
    expected: int,
) -> None:
    """Restore page one before retrying an exact page transition."""
    await navigate(page, board_url, config)
    html = await safe_content(page)
    _raise_if_bot_challenge(page.url or board_url, html)
    _, text, observed_page = await _page_snapshot(page, board_url)
    observed_total = _expected_count(text)
    if observed_page != 1 or observed_total != expected:
        raise RuntimeError(
            "Njoyn listing snapshot changed while resetting pagination; "
            f"page={observed_page}, total={observed_total}, expected={expected}"
        )


async def _submit_exact_page(
    page,
    board_url: str,
    config: dict,
    *,
    target_page: int,
    previous_page_urls: set[str],
    discovered_urls: set[str],
    expected: int,
    wait_ms: int,
    navigation_timeout_ms: int,
) -> set[str]:
    """Submit and verify one indexed Njoyn page, resetting before retries.

    The public ``NEXT`` anchor calls a JavaScript helper that mutates the
    hidden ``pn`` field. Clicking it and then separately waiting for load can
    race the form navigation, leaving the monitor to read an unrelated prior
    page. Submit the exact index under Playwright's navigation expectation,
    then require the returned hidden page state and result total to match.
    """
    last_observed_page: int | None = None
    last_new_urls = 0
    last_navigation_error: str | None = None

    for attempt in range(1, _PAGE_TRANSITION_ATTEMPTS + 1):
        if attempt > 1:
            await _reset_listing_page(page, board_url, config, expected=expected)

        navigation_error: Exception | None = None
        submitted = False
        try:
            async with page.expect_navigation(
                wait_until="domcontentloaded",
                timeout=navigation_timeout_ms,
            ):
                submitted = await page.evaluate(_SUBMIT_PAGE_SCRIPT, target_page)
        except Exception as exc:  # noqa: BLE001 — Playwright raises plain Error/TimeoutError
            navigation_error = exc

        if wait_ms:
            await asyncio.sleep(wait_ms / 1000)

        html = await safe_content(page)
        _raise_if_bot_challenge(page.url or board_url, html)
        candidate_urls, text, observed_page = await _page_snapshot(page, board_url)
        observed_total = _expected_count(text)
        new_urls = candidate_urls - discovered_urls
        last_observed_page = observed_page
        last_new_urls = len(new_urls)
        last_navigation_error = type(navigation_error).__name__ if navigation_error else None

        if observed_total != expected:
            raise RuntimeError(
                "Njoyn result total changed during pagination; "
                f"page={observed_page}, total={observed_total}, expected={expected}"
            )
        if observed_page == target_page and candidate_urls != previous_page_urls and new_urls:
            return candidate_urls

        log.warning(
            "njoyn.pagination.transition_retry",
            attempt=attempt,
            attempts=_PAGE_TRANSITION_ATTEMPTS,
            target_page=target_page,
            observed_page=observed_page,
            page_urls=len(candidate_urls),
            new_urls=len(new_urls),
            collected=len(discovered_urls),
            expected=expected,
            submitted=submitted,
            navigation_error=last_navigation_error,
        )
        if attempt < _PAGE_TRANSITION_ATTEMPTS:
            await asyncio.sleep(_PAGE_TRANSITION_RETRY_DELAY * attempt)

    raise RuntimeError(
        "Njoyn exact page transition did not converge; "
        f"target_page={target_page}, observed_page={last_observed_page}, "
        f"new_urls={last_new_urls}, collected={len(discovered_urls)}, expected={expected}, "
        f"navigation_error={last_navigation_error}"
    )


async def _discover_page(page, board_url: str, config: dict) -> set[str]:
    await navigate(page, board_url, config)

    html = await safe_content(page)
    _raise_if_bot_challenge(page.url or board_url, html)
    current_page_urls, text, current_page = await _page_snapshot(page, board_url)
    if current_page != 1:
        raise RuntimeError(f"Njoyn listing opened on unexpected page {current_page}")
    urls = set(current_page_urls)
    expected = _expected_count(text)
    if expected is None:
        raise RuntimeError("Njoyn listing is missing its Search Results total")
    if expected is not None and expected > MAX_JOBS:
        raise RuntimeError(f"Njoyn result count {expected} exceeds cap {MAX_JOBS}")

    max_pages = min(int(config.get("max_pages", MAX_PAGES)), MAX_PAGES)
    if max_pages < 1:
        raise ValueError("Njoyn max_pages must be at least 1")
    wait_ms = min(60_000, max(0, int(config.get("page_wait_ms", 0))))
    navigation_timeout_ms = min(
        60_000,
        max(1_000, int(config.get("page_change_timeout_ms", 15_000))),
    )

    for page_number in range(2, max_pages + 1):
        if len(urls) >= expected:
            break

        page_urls = await _submit_exact_page(
            page,
            board_url,
            config,
            target_page=page_number,
            previous_page_urls=current_page_urls,
            discovered_urls=urls,
            expected=expected,
            wait_ms=wait_ms,
            navigation_timeout_ms=navigation_timeout_ms,
        )
        current_page_urls = page_urls
        urls.update(page_urls)
    else:
        if len(urls) < expected:
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
