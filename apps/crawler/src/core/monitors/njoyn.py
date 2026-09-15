"""Njoyn browser monitor.

Njoyn's classic ``XWeb.asp`` listings are session-bound POST forms. The
visible ``NEXT`` control submits the current form and keeps the page URL
unchanged, so query-parameter pagination and browser-context ``fetch`` calls
only ever return the first page. This monitor keeps one browser context,
submits each exact hidden page index under a navigation expectation, and fails
closed when a stable, complete listing snapshot is not fully collected.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import structlog

from src.core.monitors import register
from src.core.monitors.dom import BotChallengeError, _raise_if_bot_challenge
from src.shared.browser import BROWSER_KEYS, navigate, open_page, safe_content
from src.shared.proxy import ProxyPoolExhaustedError

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_PAGES = 200
_PAGE_TRANSITION_ATTEMPTS = 3
_PAGE_TRANSITION_RETRY_DELAY = 1.0
_SNAPSHOT_ATTEMPTS = 2
_SNAPSHOT_RETRY_DELAY = 2.0
_TRANSPORT_ATTEMPTS = 5
_TRANSPORT_RETRY_DELAY = 1.0
_MAX_ORIGIN_BLOCK_RESPONSE_CHARS = 1_024

_RESULT_COUNT_RE = re.compile(r"\bSearch\s+Results\s*\(([\d,\s]+)\)", re.IGNORECASE)
_NJOYN_ORIGIN_BLOCK_RE = re.compile(r"\binvalid\s+request\s+xwp[1-9]\d*\b", re.IGNORECASE)
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


class _ListingSnapshotChanged(RuntimeError):
    """Signal that a complete listing pass must restart from page one."""

    def __init__(
        self,
        reason: str,
        *,
        page: int | None = None,
        expected: int | None = None,
        observed: int | None = None,
        collected: int | None = None,
    ) -> None:
        self.reason = reason
        self.page = page
        self.expected = expected
        self.observed = observed
        self.collected = collected
        super().__init__(reason)


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


def _raise_if_njoyn_challenge(url: str, html: str) -> None:
    """Classify Njoyn's HTTP-200 origin rejection as a proxy failure.

    Njoyn can return a tiny ``Invalid request XWP<code>`` document instead
    of its listing form. The shared challenge detector cannot safely treat
    that provider-specific text as universal, so keep the narrow response
    shape here. Raising the typed error from inside ``open_page`` lets proxy
    accounting quarantine that origin/slot pair before the next context.
    """
    if (
        len(html) <= _MAX_ORIGIN_BLOCK_RESPONSE_CHARS
        and _NJOYN_ORIGIN_BLOCK_RE.search(html) is not None
    ):
        raise BotChallengeError("Njoyn origin rejected the browser session")
    _raise_if_bot_challenge(url, html)


async def _page_snapshot(page, board_url: str) -> tuple[set[str], str, int]:
    snapshot = await page.evaluate(_PAGE_SNAPSHOT_SCRIPT)
    links = {url for url in snapshot["links"] if _is_job_detail_url(url, board_url=board_url)}
    raw_page_number = snapshot.get("pageNumber")
    if not isinstance(raw_page_number, str) or re.fullmatch(r"[1-9]\d*", raw_page_number) is None:
        raise RuntimeError("Njoyn listing is missing its numeric page state")
    return links, snapshot["text"], int(raw_page_number)


async def _load_first_page(page, board_url: str, config: dict) -> tuple[set[str], int]:
    """Navigate to and validate a fresh first-page listing snapshot."""
    await navigate(page, board_url, config)
    html = await safe_content(page)
    _raise_if_njoyn_challenge(page.url or board_url, html)
    urls, text, observed_page = await _page_snapshot(page, board_url)
    if observed_page != 1:
        raise _ListingSnapshotChanged("first_page_state_changed", page=observed_page)
    expected = _expected_count(text)
    if expected is None:
        raise RuntimeError("Njoyn listing is missing its Search Results total")
    if expected > MAX_JOBS:
        raise RuntimeError(f"Njoyn result count {expected} exceeds cap {MAX_JOBS}")
    return urls, expected


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
    """Submit and verify one indexed Njoyn page with bounded retries.

    The public ``NEXT`` anchor calls a JavaScript helper that mutates the
    hidden ``pn`` field. Clicking it and then separately waiting for load can
    race the form navigation, leaving the monitor to read an unrelated prior
    page. Submit the exact index under Playwright's navigation expectation,
    then require the returned hidden page state and result total to match. A
    non-converging transition signals the caller to discard the entire pass;
    page-level retries never mix results collected before and after a reset.
    """
    last_observed_page: int | None = None
    last_new_urls = 0
    last_navigation_error: str | None = None

    for attempt in range(1, _PAGE_TRANSITION_ATTEMPTS + 1):
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
        _raise_if_njoyn_challenge(page.url or board_url, html)
        candidate_urls, text, observed_page = await _page_snapshot(page, board_url)
        observed_total = _expected_count(text)
        new_urls = candidate_urls - discovered_urls
        last_observed_page = observed_page
        last_new_urls = len(new_urls)
        last_navigation_error = type(navigation_error).__name__ if navigation_error else None

        if observed_total != expected:
            raise _ListingSnapshotChanged(
                "result_total_changed",
                page=observed_page,
                expected=expected,
                observed=observed_total,
                collected=len(discovered_urls),
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

    raise _ListingSnapshotChanged(
        "page_transition_did_not_converge",
        page=last_observed_page or target_page,
        expected=expected,
        observed=last_new_urls,
        collected=len(discovered_urls),
    )


async def _collect_listing_snapshot(page, board_url: str, config: dict) -> set[str]:
    """Collect and verify one internally consistent complete listing pass."""
    current_page_urls, expected = await _load_first_page(page, board_url, config)
    first_page_urls = set(current_page_urls)
    page_urls_by_number = {1: first_page_urls}
    urls = set(current_page_urls)

    max_pages = min(int(config.get("max_pages", MAX_PAGES)), MAX_PAGES)
    if max_pages < 1:
        raise ValueError("Njoyn max_pages must be at least 1")
    wait_ms = min(60_000, max(0, int(config.get("page_wait_ms", 0))))
    navigation_timeout_ms = min(
        60_000,
        max(1_000, int(config.get("page_change_timeout_ms", 15_000))),
    )
    final_page = 1

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
        page_urls_by_number[page_number] = set(page_urls)
        urls.update(page_urls)
        final_page = page_number
    else:
        if len(urls) < expected:
            raise RuntimeError(f"Njoyn pagination hit max_pages={max_pages}")

    if len(urls) != expected:
        raise _ListingSnapshotChanged(
            "result_count_mismatch",
            page=final_page,
            expected=expected,
            observed=len(urls),
            collected=len(urls),
        )
    if not urls and expected:
        raise RuntimeError("Njoyn listing returned no job-detail URLs")

    # Replay every page after the complete pass. Live additions and removals
    # can replace a middle-page URL while leaving both the visible total and
    # boundary pages unchanged. Exact per-page equality makes success evidence
    # of two matching complete inventories, not merely an exact count.
    verified_first_urls, verified_total = await _load_first_page(page, board_url, config)
    if verified_total != expected or verified_first_urls != first_page_urls:
        raise _ListingSnapshotChanged(
            "first_page_fingerprint_changed",
            page=1,
            expected=expected,
            observed=verified_total,
            collected=len(urls),
        )

    verified_urls = set(verified_first_urls)
    previous_verified_urls = verified_first_urls
    for page_number in range(2, final_page + 1):
        verified_page_urls = await _submit_exact_page(
            page,
            board_url,
            config,
            target_page=page_number,
            previous_page_urls=previous_verified_urls,
            discovered_urls=verified_urls,
            expected=expected,
            wait_ms=wait_ms,
            navigation_timeout_ms=navigation_timeout_ms,
        )
        expected_page_urls = page_urls_by_number[page_number]
        if verified_page_urls != expected_page_urls:
            raise _ListingSnapshotChanged(
                "page_fingerprint_changed",
                page=page_number,
                expected=expected,
                observed=len(verified_page_urls),
                collected=len(urls),
            )
        verified_urls.update(verified_page_urls)
        previous_verified_urls = verified_page_urls

    if verified_urls != urls:
        raise _ListingSnapshotChanged(
            "inventory_fingerprint_changed",
            page=final_page,
            expected=expected,
            observed=len(verified_urls),
            collected=len(urls),
        )

    log.info(
        "njoyn.complete",
        board_url=board_url,
        urls_found=len(urls),
        expected=expected,
    )
    return urls


async def _discover_page(page, board_url: str, config: dict) -> set[str]:
    snapshot_attempts = min(3, max(1, int(config.get("snapshot_attempts", _SNAPSHOT_ATTEMPTS))))
    last_change: _ListingSnapshotChanged | None = None

    for attempt in range(1, snapshot_attempts + 1):
        try:
            return await _collect_listing_snapshot(page, board_url, config)
        except _ListingSnapshotChanged as exc:
            last_change = exc
            retrying = attempt < snapshot_attempts
            log.warning(
                "njoyn.snapshot.changed",
                attempt=attempt,
                attempts=snapshot_attempts,
                retrying=retrying,
                reason=exc.reason,
                page=exc.page,
                expected=exc.expected,
                observed=exc.observed,
                collected=exc.collected,
            )
            if retrying:
                await asyncio.sleep(_SNAPSHOT_RETRY_DELAY * attempt)

    assert last_change is not None
    raise RuntimeError(
        "Njoyn listing did not stabilize after "
        f"{snapshot_attempts} complete attempts; last_reason={last_change.reason}, "
        f"page={last_change.page}, expected={last_change.expected}, "
        f"observed={last_change.observed}, collected={last_change.collected}"
    ) from last_change


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

    async def _run_context(playwright, *, use_proxy: bool) -> set[str]:
        async with open_page(
            playwright,
            browser_config,
            use_proxy=use_proxy,
            target_url=board_url,
        ) as page:
            return await _discover_page(page, board_url, metadata | browser_config)

    async def _run(playwright) -> set[str]:
        use_proxy = bool(metadata.get("proxy"))
        if not use_proxy:
            return await _run_context(playwright, use_proxy=False)

        transport_attempts = min(
            5,
            max(1, int(metadata.get("transport_attempts", _TRANSPORT_ATTEMPTS))),
        )
        last_error: BotChallengeError | ProxyPoolExhaustedError | None = None
        for attempt in range(1, transport_attempts + 1):
            try:
                return await _run_context(playwright, use_proxy=True)
            except BotChallengeError as exc:
                last_error = exc
                retrying = attempt < transport_attempts
                log.warning(
                    "njoyn.transport.proxy_retry",
                    attempt=attempt,
                    attempts=transport_attempts,
                    retrying=retrying,
                    reason="origin_block",
                )
                if retrying:
                    await asyncio.sleep(_TRANSPORT_RETRY_DELAY * attempt)
            except ProxyPoolExhaustedError as exc:
                # Do not bypass a missing/misconfigured proxy. A direct retry
                # is eligible only after this cycle observed a typed Njoyn or
                # bot-manager rejection from a selected proxy context.
                if last_error is None:
                    raise
                last_error = exc
                log.warning(
                    "njoyn.transport.proxy_retry",
                    attempt=attempt,
                    attempts=transport_attempts,
                    retrying=False,
                    reason="pool_exhausted_after_origin_block",
                )
                break

        if metadata.get("direct_fallback_on_origin_block") is True:
            log.warning(
                "njoyn.transport.direct_fallback",
                proxy_attempts=transport_attempts,
                last_error_type=type(last_error).__name__,
            )
            return await _run_context(playwright, use_proxy=False)

        assert last_error is not None
        raise last_error

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
        "transport_attempts": 5,
        "direct_fallback_on_origin_block": True,
    }


register("njoyn", discover, cost=80, can_handle=can_handle)
