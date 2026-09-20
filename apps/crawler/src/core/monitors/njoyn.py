"""Njoyn browser monitor.

Njoyn's classic ``XWeb.asp`` listings are session-bound POST forms. The
visible ``NEXT`` control submits the current form and keeps the page URL
unchanged, so query-parameter pagination and browser-context ``fetch`` calls
only ever return the first page. This monitor keeps one browser context,
submits each exact hidden page index under a navigation expectation, and
reconciles two structurally complete passes within a code-owned live-churn
budget. Larger drift, missing pages, and inconsistent page metadata fail
closed.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from math import ceil
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
_DEFAULT_PAGE_WAIT_MS = 4_000
_MAX_ORIGIN_BLOCK_RESPONSE_CHARS = 1_024
_MAX_LIVE_CHURN_JOBS = 25
_LIVE_CHURN_RATIO = 0.005
_DEFAULT_DELIST_THRESHOLD = 4

_RESULT_COUNT_RE = re.compile(r"\bSearch\s+Results\s*\(([\d,\s]+)\)", re.IGNORECASE)
_PAGE_COUNT_RE = re.compile(r"\bPage\s+([\d,\s]+)\s+of\s+([\d,\s]+)\b", re.IGNORECASE)
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


@dataclass(frozen=True, slots=True)
class _PageSnapshot:
    urls: frozenset[str]
    result_total: int
    page_number: int
    page_count: int


@dataclass(frozen=True, slots=True)
class _ListingPass:
    urls: frozenset[str]
    observed_totals: tuple[int, ...]
    pages_visited: int


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


def _pagination_state(text: str) -> tuple[int, int] | None:
    match = _PAGE_COUNT_RE.search(text)
    if not match:
        return None
    current, total = (int(re.sub(r"\D", "", value)) for value in match.groups())
    return current, total


def _live_churn_allowance(total: int) -> int:
    """Bound reconciliation independently of operator-controlled config."""
    return min(_MAX_LIVE_CHURN_JOBS, max(1, ceil(total * _LIVE_CHURN_RATIO)))


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
    try:
        _raise_if_bot_challenge(url, html)
    except BotChallengeError:
        # The shared detector includes the challenge URL in its operator
        # message. Radware URLs carry opaque request/session material, so
        # replace it at this provider boundary before worker traceback logs.
        raise BotChallengeError("Njoyn origin rejected the browser session") from None


async def _page_snapshot(page, board_url: str) -> _PageSnapshot:
    snapshot = await page.evaluate(_PAGE_SNAPSHOT_SCRIPT)
    links = frozenset(
        url for url in snapshot["links"] if _is_job_detail_url(url, board_url=board_url)
    )
    text = snapshot.get("text")
    if not isinstance(text, str):
        raise RuntimeError("Njoyn listing is missing its text snapshot")
    raw_page_number = snapshot.get("pageNumber")
    if not isinstance(raw_page_number, str) or re.fullmatch(r"[1-9]\d*", raw_page_number) is None:
        raise _ListingSnapshotChanged("missing_numeric_page_state")
    hidden_page = int(raw_page_number)
    result_total = _expected_count(text)
    if result_total is None:
        raise RuntimeError("Njoyn listing is missing its Search Results total")
    if result_total > MAX_JOBS:
        raise RuntimeError(f"Njoyn result count {result_total} exceeds cap {MAX_JOBS}")
    pagination = _pagination_state(text)
    if pagination is None:
        raise RuntimeError("Njoyn listing is missing its Page N of M state")
    text_page, page_count = pagination
    if text_page != hidden_page or page_count < 1 or hidden_page > page_count:
        raise _ListingSnapshotChanged(
            "pagination_state_mismatch",
            page=hidden_page,
            expected=page_count,
            observed=text_page,
            collected=len(links),
        )
    return _PageSnapshot(links, result_total, hidden_page, page_count)


async def _load_first_page(page, board_url: str, config: dict) -> _PageSnapshot:
    """Navigate to and validate a fresh first-page listing snapshot."""
    await navigate(page, board_url, config)
    html = await safe_content(page)
    try:
        _raise_if_njoyn_challenge(page.url or board_url, html)
    except BotChallengeError:
        log.warning(
            "njoyn.transport.origin_block",
            phase="first_page",
            target_page=1,
        )
        raise
    snapshot = await _page_snapshot(page, board_url)
    if snapshot.page_number != 1:
        raise _ListingSnapshotChanged("first_page_state_changed", page=snapshot.page_number)
    return snapshot


async def _submit_exact_page(
    page,
    board_url: str,
    config: dict,
    *,
    target_page: int,
    previous_page_urls: frozenset[str],
    discovered_urls: set[str],
    expected_hint: int,
    wait_ms: int,
    navigation_timeout_ms: int,
) -> _PageSnapshot:
    """Submit and verify one indexed Njoyn page with bounded retries.

    The public ``NEXT`` anchor calls a JavaScript helper that mutates the
    hidden ``pn`` field. Clicking it and then separately waiting for load can
    race the form navigation, leaving the monitor to read an unrelated prior
    page. Submit the exact index under Playwright's navigation expectation,
    then require the returned hidden and visible page states to match. The
    advertised result total may move within the separately bounded two-pass
    reconciliation; page-level retries still never mix a repeated or
    unrelated page into one pass.
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
        try:
            _raise_if_njoyn_challenge(page.url or board_url, html)
        except BotChallengeError:
            log.warning(
                "njoyn.transport.origin_block",
                phase="pagination",
                target_page=target_page,
                collected=len(discovered_urls),
                expected=expected_hint,
            )
            raise
        candidate = await _page_snapshot(page, board_url)
        new_urls = candidate.urls - discovered_urls
        last_observed_page = candidate.page_number
        last_new_urls = len(new_urls)
        last_navigation_error = type(navigation_error).__name__ if navigation_error else None

        if (
            candidate.page_number == target_page
            and candidate.urls != previous_page_urls
            and new_urls
        ):
            return candidate

        log.warning(
            "njoyn.pagination.transition_retry",
            attempt=attempt,
            attempts=_PAGE_TRANSITION_ATTEMPTS,
            target_page=target_page,
            observed_page=candidate.page_number,
            page_urls=len(candidate.urls),
            new_urls=len(new_urls),
            collected=len(discovered_urls),
            expected=expected_hint,
            submitted=submitted,
            navigation_error=last_navigation_error,
        )
        if attempt < _PAGE_TRANSITION_ATTEMPTS:
            await asyncio.sleep(_PAGE_TRANSITION_RETRY_DELAY * attempt)

    raise _ListingSnapshotChanged(
        "page_transition_did_not_converge",
        page=last_observed_page or target_page,
        expected=expected_hint,
        observed=last_new_urls,
        collected=len(discovered_urls),
    )


def _validate_page_shape(
    snapshot: _PageSnapshot,
    *,
    page_size: int,
    max_pages: int,
) -> None:
    """Prove that the visible page metadata and row count agree."""
    if snapshot.page_count > max_pages:
        raise RuntimeError(f"Njoyn pagination hit max_pages={max_pages}")
    if snapshot.result_total == 0:
        if snapshot.page_count != 1 or snapshot.urls:
            raise _ListingSnapshotChanged(
                "empty_page_shape_mismatch",
                page=snapshot.page_number,
                expected=0,
                observed=len(snapshot.urls),
                collected=len(snapshot.urls),
            )
        return
    if page_size < 1:
        raise RuntimeError("Njoyn listing returned no job-detail URLs")

    expected_page_count = ceil(snapshot.result_total / page_size)
    if snapshot.page_count != expected_page_count:
        raise _ListingSnapshotChanged(
            "page_count_mismatch",
            page=snapshot.page_number,
            expected=expected_page_count,
            observed=snapshot.page_count,
            collected=len(snapshot.urls),
        )
    expected_rows = (
        page_size
        if snapshot.page_number < snapshot.page_count
        else snapshot.result_total - page_size * (snapshot.page_count - 1)
    )
    if len(snapshot.urls) != expected_rows:
        raise _ListingSnapshotChanged(
            "page_row_count_mismatch",
            page=snapshot.page_number,
            expected=expected_rows,
            observed=len(snapshot.urls),
            collected=len(snapshot.urls),
        )


async def _collect_listing_pass(page, board_url: str, config: dict) -> _ListingPass:
    """Visit every sequential page in one structurally complete pass."""
    max_pages = min(int(config.get("max_pages", MAX_PAGES)), MAX_PAGES)
    if max_pages < 1:
        raise ValueError("Njoyn max_pages must be at least 1")
    wait_ms = min(60_000, max(0, int(config.get("page_wait_ms", _DEFAULT_PAGE_WAIT_MS))))
    navigation_timeout_ms = min(
        60_000,
        max(1_000, int(config.get("page_change_timeout_ms", 15_000))),
    )

    first = await _load_first_page(page, board_url, config)
    page_size = len(first.urls)
    _validate_page_shape(first, page_size=page_size, max_pages=max_pages)
    urls = set(first.urls)
    totals = [first.result_total]
    current = first
    target_page = 2

    while target_page <= current.page_count:
        candidate = await _submit_exact_page(
            page,
            board_url,
            config,
            target_page=target_page,
            previous_page_urls=current.urls,
            discovered_urls=urls,
            expected_hint=current.result_total,
            wait_ms=wait_ms,
            navigation_timeout_ms=navigation_timeout_ms,
        )
        _validate_page_shape(candidate, page_size=page_size, max_pages=max_pages)
        urls.update(candidate.urls)
        totals.append(candidate.result_total)
        current = candidate
        target_page += 1

    max_total = max(totals)
    allowance = _live_churn_allowance(max_total)
    if max_total - min(totals) > allowance:
        raise _ListingSnapshotChanged(
            "pass_total_drift_exceeded",
            page=current.page_number,
            expected=max_total,
            observed=min(totals),
            collected=len(urls),
        )
    if abs(len(urls) - max_total) > allowance:
        raise _ListingSnapshotChanged(
            "pass_inventory_gap_exceeded",
            page=current.page_number,
            expected=max_total,
            observed=len(urls),
            collected=len(urls),
        )
    return _ListingPass(frozenset(urls), tuple(totals), current.page_number)


def _reconcile_listing_passes(first: _ListingPass, second: _ListingPass) -> set[str]:
    """Accept only a small bounded difference and retain the conservative union."""
    totals = first.observed_totals + second.observed_totals
    max_total = max(totals)
    min_total = min(totals)
    allowance = _live_churn_allowance(max_total)
    if max_total - min_total > allowance:
        raise _ListingSnapshotChanged(
            "reconciliation_total_drift_exceeded",
            expected=max_total,
            observed=min_total,
            collected=len(first.urls | second.urls),
        )

    symmetric_difference = first.urls ^ second.urls
    if len(symmetric_difference) > allowance * 2:
        raise _ListingSnapshotChanged(
            "reconciliation_fingerprint_drift_exceeded",
            expected=allowance * 2,
            observed=len(symmetric_difference),
            collected=len(first.urls | second.urls),
        )

    urls = set(first.urls | second.urls)
    if len(urls) > MAX_JOBS:
        raise _ListingSnapshotChanged(
            "reconciliation_job_cap_exceeded",
            expected=MAX_JOBS,
            observed=len(urls),
            collected=len(urls),
        )
    if abs(len(urls) - max_total) > allowance:
        raise _ListingSnapshotChanged(
            "reconciliation_inventory_gap_exceeded",
            expected=max_total,
            observed=len(urls),
            collected=len(urls),
        )

    consistency = "exact"
    if first.urls != second.urls or min_total != max_total:
        consistency = "bounded_churn"
        log.info(
            "njoyn.snapshot.reconciled",
            first_urls=len(first.urls),
            second_urls=len(second.urls),
            union_urls=len(urls),
            overlap_urls=len(first.urls & second.urls),
            total_min=min_total,
            total_max=max_total,
            allowance=allowance,
        )
    log.info(
        "njoyn.complete",
        urls_found=len(urls),
        expected=max_total,
        consistency=consistency,
        first_pages=first.pages_visited,
        second_pages=second.pages_visited,
    )
    return urls


async def _collect_listing_snapshot(page, board_url: str, config: dict) -> set[str]:
    """Reconcile two complete passes without requiring a frozen live listing."""
    first = await _collect_listing_pass(page, board_url, config)
    second = await _collect_listing_pass(page, board_url, config)
    return _reconcile_listing_passes(first, second)


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
        "page_wait_ms": _DEFAULT_PAGE_WAIT_MS,
        "transport_attempts": 5,
        "direct_fallback_on_origin_block": True,
        # Reconciled passes can conservatively miss a boundary row when jobs
        # are inserted at the head of the newest-first listing. Require four
        # independently reconciled absences before that row may be delisted.
        "delist_threshold": _DEFAULT_DELIST_THRESHOLD,
    }


register("njoyn", discover, cost=80, can_handle=can_handle)
