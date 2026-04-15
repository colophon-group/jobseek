"""Configuration-driven browser executor.

Centralizes Playwright browser lifecycle, navigation, and an action pipeline
so that monitors, scrapers, and scripts share one implementation.  All
behaviour is controlled via plain config dicts that flow from the JSON columns
in boards.csv — no schema change needed.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

try:
    from src import metrics
except ImportError:
    # The slim ``jobseek-crawler-setup`` (ws CLI) wheel does not ship
    # ``src/metrics.py`` — it would pull in prometheus_client, which is
    # unnecessary for workspace/config-time commands. Fall back to a
    # no-op stub so this module stays importable from the ws install.
    class _NoopMetric:
        def labels(self, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            pass

    class _NoopMetricsModule:
        def __getattr__(self, _name):
            return _NoopMetric()

    metrics = _NoopMetricsModule()  # type: ignore[assignment]

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)
DEFAULT_WAIT = "networkidle"
# Default fallback for ``navigate()``: when the primary ``page.goto`` times out
# (typically because an SPA never reaches ``networkidle`` due to persistent
# analytics/telemetry chatter), retry once with ``domcontentloaded``. Set
# ``wait_fallback=None`` in config to explicitly disable for a given board.
# This is strictly safer than the previous behaviour: the fallback only fires
# on paths that were already failing, so there is no extra CPU cost vs the
# status quo, and sites that do settle under ``networkidle`` are untouched.
DEFAULT_WAIT_FALLBACK = "domcontentloaded"
DEFAULT_TIMEOUT = 30_000
CONTEXT_TIMEOUT = 120_000  # hard cap: no single Playwright operation exceeds 2 minutes
VALID_WAIT_STRATEGIES = frozenset({"load", "domcontentloaded", "networkidle", "commit"})
OVERLAY_SELECTORS = (
    '[class*="cookie-banner"]',
    '[class*="cookie-consent"]',
    '[class*="cookie-notice"]',
    '[id*="cookie"]',
    '[class*="consent-banner"]',
    '[class*="consent-modal"]',
    '[role="dialog"][class*="cookie"]',
    '[role="dialog"][class*="consent"]',
)

# Browser config keys recognised by open_page / navigate / run_actions.
# Used by scrapers and monitors to separate browser keys from other config.
BROWSER_KEYS = frozenset(
    {
        "wait",
        "wait_fallback",
        "timeout",
        "user_agent",
        "headless",
        "stealth",
        "actions",
        "warmup_url",
        "cookies",
        "disable_http2",
    }
)

# Narrow subset that affects only ``navigate()`` and the action pipeline — not
# browser launch (``open_page``).  Use this in call sites that historically
# only forwarded ``wait``/``timeout``/``actions`` so we can add ``wait_fallback``
# without silently activating previously-dropped launch-time keys (``stealth``,
# ``user_agent``, ``cookies``, etc.) on boards that set them.
NAVIGATE_KEYS = frozenset({"wait", "wait_fallback", "timeout", "actions"})

# ---------------------------------------------------------------------------
# Config placeholders
# ---------------------------------------------------------------------------


def _resolve_placeholders(cookies: list[dict]) -> list[dict]:
    """Replace ``{uuid}`` in cookie values with a fresh random UUID."""
    resolved = []
    for cookie in cookies:
        value = cookie.get("value")
        if isinstance(value, str) and "{uuid}" in value:
            cookie = {**cookie, "value": value.replace("{uuid}", uuid.uuid4().hex)}
        resolved.append(cookie)
    return resolved


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@asynccontextmanager
async def open_page(
    pw,  # AsyncPlaywright
    config: dict | None = None,
    *,
    use_proxy: bool = False,
) -> AsyncIterator:
    """Create browser → context → page.  Yields a Playwright *Page*.

    The caller manages the outer ``async_playwright()`` context so they can
    attach hooks (e.g. response interception) between page creation and
    navigation.

    Config keys consumed: ``user_agent``, ``headless`` (default ``True``).

    When ``use_proxy`` is True, the browser launches through the active
    proxy provider (see :mod:`src.shared.proxy`).
    """
    config = config or {}
    headless = config.get("headless", True)
    user_agent = config.get("user_agent", DEFAULT_USER_AGENT)
    warmup_url = config.get("warmup_url")
    cookies = config.get("cookies")

    launch_kwargs: dict = {"headless": headless}
    # Chromium's new headless mode (--headless=new) is less detectable by
    # anti-bot systems like Cloudflare Turnstile.  Enable via stealth: true.
    extra_args: list[str] = []
    if headless and config.get("stealth"):
        extra_args.append("--headless=new")
    if config.get("disable_http2"):
        extra_args.append("--disable-http2")
    if extra_args:
        launch_kwargs["args"] = extra_args
    if use_proxy:
        from src.shared.proxy import playwright_proxy_for

        pw_proxy = playwright_proxy_for(use_proxy=True)
        if pw_proxy:
            launch_kwargs["proxy"] = pw_proxy

    browser = await pw.chromium.launch(**launch_kwargs)
    context = None
    try:
        context = await browser.new_context(user_agent=user_agent)
        context.set_default_timeout(CONTEXT_TIMEOUT)
        if cookies:
            await context.add_cookies(_resolve_placeholders(cookies))
        page = await context.new_page()
        if warmup_url:
            log.debug("browser.warmup", url=warmup_url)
            await page.goto(warmup_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        yield page
    finally:
        if context:
            await context.close()
        await browser.close()


async def navigate(
    page,  # playwright Page
    url: str,
    config: dict | None = None,
) -> None:
    """Navigate *page* to *url* respecting wait strategy and timeout.

    Config keys:
        ``wait``           Primary wait strategy (default ``"networkidle"``).
        ``timeout``        Navigation timeout in ms (default ``30000``).
        ``wait_fallback``  Fallback wait strategy retried once when the primary
                           ``page.goto`` raises Playwright's ``TimeoutError``
                           (non-timeout errors propagate unchanged). Defaults
                           to ``DEFAULT_WAIT_FALLBACK`` ("domcontentloaded")
                           so SPA sites that never reach ``networkidle`` still
                           produce usable HTML. Set to ``None`` in config to
                           opt out; set to the same value as ``wait`` for an
                           effective no-op. The fallback reuses the original
                           timeout, so worst-case wall-clock is ``2 * timeout``.
    """
    config = config or {}
    wait_strategy = config.get("wait", DEFAULT_WAIT)
    timeout = config.get("timeout", DEFAULT_TIMEOUT)
    # Distinguish "not set" (use default) from "explicitly None" (disable).
    if "wait_fallback" in config:
        fallback_strategy = config["wait_fallback"]
    else:
        fallback_strategy = DEFAULT_WAIT_FALLBACK

    if wait_strategy not in VALID_WAIT_STRATEGIES:
        raise ValueError(
            f"Invalid wait strategy {wait_strategy!r}, "
            f"must be one of {sorted(VALID_WAIT_STRATEGIES)}"
        )
    if fallback_strategy is not None and fallback_strategy not in VALID_WAIT_STRATEGIES:
        raise ValueError(
            f"Invalid wait_fallback strategy {fallback_strategy!r}, "
            f"must be one of {sorted(VALID_WAIT_STRATEGIES)}"
        )

    try:
        await page.goto(url, wait_until=wait_strategy, timeout=timeout)
        return
    except PlaywrightTimeoutError:
        if not fallback_strategy:
            # Board opted out via wait_fallback=None. Record separately from
            # the match-primary case so operators can tell why the retry was
            # skipped.
            metrics.browser_navigate_fallback_total.labels(
                primary=wait_strategy, fallback="none", outcome="disabled"
            ).inc()
            raise
        if fallback_strategy == wait_strategy:
            # Fallback equals primary — nothing to gain from a second attempt.
            metrics.browser_navigate_fallback_total.labels(
                primary=wait_strategy, fallback=fallback_strategy, outcome="match"
            ).inc()
            raise

    log.info(
        "browser.navigate.fallback",
        url=url,
        primary=wait_strategy,
        fallback=fallback_strategy,
        timeout_ms=timeout,
    )
    try:
        await page.goto(url, wait_until=fallback_strategy, timeout=timeout)
    except Exception:
        metrics.browser_navigate_fallback_total.labels(
            primary=wait_strategy, fallback=fallback_strategy, outcome="failed"
        ).inc()
        raise
    else:
        metrics.browser_navigate_fallback_total.labels(
            primary=wait_strategy, fallback=fallback_strategy, outcome="success"
        ).inc()


ACTION_TIMEOUT = 10.0  # seconds
_REPEAT_TIMEOUT = 300.0  # seconds — repeat actions get a longer default


async def run_actions(page, actions: list[dict]) -> None:
    """Execute an action pipeline sequentially on *page*.

    Each action is wrapped in a timeout (default 10s, configurable per-action
    via a ``"timeout"`` key).  On failure or timeout an individual action logs
    a warning and execution continues with the next action.
    """
    for action in actions:
        kind = action.get("action")
        default_timeout = (
            _REPEAT_TIMEOUT if kind in ("repeat", "paginate_collect") else ACTION_TIMEOUT
        )
        timeout = action.get("timeout", default_timeout)
        try:
            await asyncio.wait_for(_execute_action(page, action, kind), timeout=timeout)
        except TimeoutError:
            log.warning("browser.action.timeout", action=kind, timeout=timeout)
        except Exception:
            log.warning("browser.action.failed", action=kind, exc_info=True)


async def _execute_action(page, action: dict, kind: str | None) -> None:
    """Dispatch a single action on *page*."""
    if kind == "remove":
        selector = action["selector"]
        await page.evaluate(
            "(sel) => document.querySelectorAll(sel).forEach(el => el.remove())",
            selector,
        )
    elif kind == "click":
        selector = action["selector"]
        loc = page.locator(selector).first
        if await loc.count() > 0:
            await loc.click()
        else:
            log.warning("browser.action.click_no_match", selector=selector)
    elif kind == "wait":
        ms = action.get("ms", 1000)
        await asyncio.sleep(ms / 1000)
    elif kind == "evaluate":
        script = action["script"]
        await page.evaluate(script)
    elif kind == "dismiss_overlays":
        await dismiss_overlays(page)
    elif kind == "repeat":
        await _execute_repeat(page, action)
    elif kind == "paginate_collect":
        await _execute_paginate_collect(page, action)
    else:
        log.warning("browser.action.unknown", action=kind)


def _resolve_frame(page, frame_selector: str | None):
    """Return the target frame (or the page itself) for actions.

    *frame_selector* is a CSS selector matching an ``<iframe>`` in the
    main page.  When provided, Playwright's ``frame_locator`` is used to
    pierce the cross-origin boundary.
    """
    if not frame_selector:
        return page
    return page.frame_locator(frame_selector)


async def _execute_repeat(page, action: dict) -> None:
    """Click an element repeatedly until no new links appear or selector is gone.

    When ``frame`` is set (CSS selector matching an ``<iframe>``), clicks
    target elements inside that frame.  After all clicks, links from the
    frame are injected as hidden ``<a>`` tags into the main page so the
    DOM monitor's link extractor can see them.
    """
    selector = action["selector"]
    max_iter = action.get("max", 50)
    wait_ms = action.get("wait_ms", 2000)
    frame_selector = action.get("frame")
    force = action.get("force", False)

    target = page.frame_locator(frame_selector) if frame_selector else page

    # For frame targets, measure link counts inside the frame.
    count_ctx = page
    if frame_selector:
        for f in page.frames:
            if f != page.main_frame and f.url and f.url != "about:blank":
                count_ctx = f
                break

    for i in range(max_iter):
        before = await count_ctx.evaluate("() => document.querySelectorAll('a[href]').length")
        if frame_selector and count_ctx != page:
            # Use JS click inside cross-origin frame to bypass overlays.
            clicked = await count_ctx.evaluate(
                "(sel) => { const el = document.querySelector(sel);"
                " if (el) { el.click(); return true; } return false; }",
                selector,
            )
            if not clicked:
                log.info("browser.repeat.selector_gone", iteration=i)
                break
        else:
            loc = target.locator(selector).first
            if await loc.count() == 0:
                log.info("browser.repeat.selector_gone", iteration=i)
                break
            await loc.click(force=force)
        await asyncio.sleep(wait_ms / 1000)
        after = await count_ctx.evaluate("() => document.querySelectorAll('a[href]').length")
        if after <= before:
            log.info("browser.repeat.no_new_links", iteration=i + 1, links=after)
            break
        log.debug("browser.repeat.click", iteration=i + 1, new=after - before, total=after)

    # Inject cross-origin iframe links into the main page.
    if frame_selector:
        frame = None
        for f in page.frames:
            if f != page.main_frame and f.url and f.url != "about:blank":
                frame = f
                break
        if frame:
            links = await frame.evaluate(
                "() => [...document.querySelectorAll('a[href]')].map(a => a.href)"
            )
            if links:
                await page.evaluate(
                    "(urls) => urls.forEach(u => {"
                    "  const a = document.createElement('a');"
                    "  a.href = u; a.style.display = 'none';"
                    "  document.body.appendChild(a);"
                    "})",
                    links,
                )
                log.info("browser.repeat.frame_links_injected", count=len(links))


async def _execute_paginate_collect(page, action: dict) -> None:
    """Click through paginated content, collecting links from every page.

    For portals that *replace* page content on navigation (rather than
    appending), the standard ``repeat`` action only sees the last page.
    This action visits every page, accumulates all ``<a href>`` URLs, and
    injects them as hidden elements so the dom monitor's link extractor
    finds the full set.

    Config keys:
        next_selector (str): CSS selector for the clickable "next page"
            element.  Pagination stops when the selector matches nothing.
        page_size_selector (str): Optional CSS selector for a ``<select>``
            dropdown that controls items-per-page.
        page_size (int|str): Value to set on the page-size dropdown.
        wait_ms (int): Delay in ms after each navigation (default 5000).
        max_pages (int): Safety cap on pagination clicks (default 50).
    """
    next_sel = action.get("next_selector", "li.next:not(.next_disabled) a")
    ps_selector = action.get("page_size_selector", "")
    page_size = action.get("page_size", "")
    wait_ms = action.get("wait_ms", 5000)
    max_pages = action.get("max_pages", 50)

    total = await page.evaluate(
        """async ([nextSel, psSel, pageSize, waitMs, maxPages]) => {
            const delay = ms => new Promise(r => setTimeout(r, ms));

            const getAllLinks = () => Array.from(document.querySelectorAll('a[href]'))
                .filter(a => a.href.startsWith('http'))
                .map(a => a.href);

            // Optionally change items-per-page.
            if (psSel && pageSize) {
                const sel = document.querySelector(psSel);
                if (sel) {
                    sel.value = String(pageSize);
                    sel.dispatchEvent(new Event('change'));
                    // SuccessFactors uses juic event bus
                    if (typeof juic !== 'undefined' && sel.id)
                        juic.fire(sel.id, '_onChange', new Event('change'));
                    await delay(waitMs);
                }
            }

            // Collect links from all pages.
            const allLinks = new Set(getAllLinks());

            for (let p = 0; p < maxPages; p++) {
                const nextEl = document.querySelector(nextSel);
                if (!nextEl) break;
                nextEl.click();
                await delay(waitMs);
                getAllLinks().forEach(l => allLinks.add(l));
            }

            // Inject collected links as hidden <a> tags for the dom extractor.
            const container = document.createElement('div');
            container.style.display = 'none';
            allLinks.forEach(href => {
                const a = document.createElement('a');
                a.href = href;
                container.appendChild(a);
            });
            document.body.appendChild(container);

            return allLinks.size;
        }""",
        [next_sel, ps_selector, str(page_size), wait_ms, max_pages],
    )
    log.info("browser.paginate_collect.done", total=total)


async def dismiss_overlays(page) -> None:
    """Remove common cookie / consent / dialog overlays from *page*."""
    selector = ", ".join(OVERLAY_SELECTORS)
    await page.evaluate(
        "(sel) => document.querySelectorAll(sel).forEach(el => el.remove())",
        selector,
    )


async def render(url: str, config: dict | None = None, pw=None) -> str:
    """All-in-one: launch browser → navigate → run actions → return HTML.

    Convenience wrapper for consumers that just need rendered page content.

    When *pw* (an ``AsyncPlaywright`` instance) is provided, it is reused
    instead of launching a new ``async_playwright()`` context.
    """
    config = config or {}

    if pw is not None:
        async with open_page(pw, config, use_proxy=bool(config.get("proxy"))) as page:
            await navigate(page, url, config)
            await run_actions(page, config.get("actions", []))
            return await page.content()

    from playwright.async_api import async_playwright

    async with (
        async_playwright() as _pw,
        open_page(_pw, config, use_proxy=bool(config.get("proxy"))) as page,
    ):
        await navigate(page, url, config)
        await run_actions(page, config.get("actions", []))
        return await page.content()
