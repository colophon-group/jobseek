"""Configuration-driven browser executor.

Centralizes Playwright browser lifecycle, navigation, and an action pipeline
so that monitors, scrapers, and scripts share one implementation.  All
behaviour is controlled via plain config dicts that flow from the JSON columns
in boards.csv — no schema change needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import tempfile
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
        "persistent_context",
        "channel",
        "viewport",
        "locale",
        "skip_ssl",
    }
)

# Sites that fingerprint the browser (Akamai Bot Manager, PerimeterX,
# DataDome) reject vanilla ``pw.chromium.launch() + browser.new_context()``
# because that pair produces a cold Chromium profile with no plugins, no
# history, no extensions — a shape indistinguishable from automation.
# ``launch_persistent_context`` with a user-data-dir + ``channel="chrome"``
# produces a real-Chrome profile that passes most bot-manager challenges.
# Boards opt in via ``"persistent_context": true`` (and usually
# ``"channel": "chrome"``) in monitor_config / scraper_config.
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_LOCALE = "en-US"

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


def _x_server_alive(display: str) -> bool:
    """Probe the X server by running ``xdpyinfo``.

    Returns ``False`` on timeout, missing binary, or non-zero exit — i.e.
    any state that would cause Playwright's headful launch to crash with
    "XServer running" (#2431). A ``True`` result means an X server is
    actually responding to protocol requests on *display*, not merely that
    ``DISPLAY`` is set in the environment.

    ``xdpyinfo`` ships in the ``x11-utils`` Debian package (installed in
    ``apps/crawler/Dockerfile`` — see the full stage apt line). On dev
    machines without the binary, ``FileNotFoundError`` falls through to
    ``False`` and the caller coerces to headless just like in prod.

    The 2s timeout caps worst-case latency: a healthy ``xdpyinfo`` returns
    in ~20ms; the timeout fires only when the X server is hung. Called
    once per browser launch — if that becomes hot, cache the result.
    """
    try:
        result = subprocess.run(
            ["xdpyinfo", "-display", display],
            capture_output=True,
            timeout=2,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _resolve_headless(requested_headless: bool) -> tuple[bool, bool]:
    """Decide the effective headless mode given the runtime display state.

    Boards that need to pass Akamai / PerimeterX / DataDome bot managers set
    ``"headless": false`` in their monitor/scraper config. The crawler's
    ``browser-1`` container ships an xvfb entrypoint (``/usr/local/bin/with-xvfb``
    in ``apps/crawler/Dockerfile``) that starts ``Xvfb :99``, waits for the
    server to respond to ``xdpyinfo``, and exports ``DISPLAY=:99`` before
    launching — so headful Chromium has an X server to draw into.

    If that entrypoint is missing, bypassed, or Xvfb dies *after* the
    entrypoint handed off to the crawler (e.g. OOM-killed, segfault mid-run),
    Playwright crashes with:

        "launched a headed browser without having a XServer running. Set
         either headless: true or use xvfb-run <your-playwright-app>"

    Historically this produced hourly crashes per affected board (#2431).
    Instead of hard-failing, fall back to headless mode and log loudly — the
    Akamai bypass is best-effort and a degraded run (possibly blocked by the
    bot manager) is strictly better than a crash that blocks the worker slot
    every cycle.

    Probing the X server via ``xdpyinfo`` (not just ``$DISPLAY``-is-set) is
    what distinguishes this from the original #2431 fix: a dead Xvfb still
    leaves ``DISPLAY`` set in the child environment, so a bare env check
    would wave the launch through to the same crash it was meant to prevent.

    Returns ``(effective_headless, coerced)`` where ``coerced`` is True only
    when we flipped the caller's explicit ``headless=False`` to True.
    """
    if requested_headless:
        return True, False
    display = os.environ.get("DISPLAY")
    if not display:
        log.warning(
            "browser.headless_coerced",
            reason="no_display",
            detail=(
                "headless=False requested but DISPLAY is unset — falling "
                "back to headless=True with --headless=new. Expected in "
                "dev; in prod this means the xvfb entrypoint (with-xvfb) "
                "did not run. Rebuild crawler-full and ensure docker run "
                "does not override ENTRYPOINT."
            ),
        )
        metrics.browser_headless_coerced_total.labels(reason="no_display").inc()
        return True, True
    if not _x_server_alive(display):
        log.warning(
            "browser.headless_coerced",
            reason="display_unresponsive",
            display=display,
            detail=(
                "headless=False requested and DISPLAY is set, but "
                "xdpyinfo could not talk to the X server (timed out, "
                "non-zero exit, or xdpyinfo missing). Falling back to "
                "headless=True with --headless=new rather than letting "
                "Playwright crash on launch. In prod this usually means "
                "Xvfb died after with-xvfb handed off — check the "
                "browser-1 container logs for Xvfb exit traces."
            ),
        )
        metrics.browser_headless_coerced_total.labels(reason="display_unresponsive").inc()
        return True, True
    return False, False


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

    Config keys consumed: ``user_agent``, ``headless`` (default ``True``),
    ``persistent_context``, ``channel``, ``viewport``, ``locale``.

    ``headless: false`` requires an X server at runtime (DISPLAY env var
    set). In production the browser worker's Docker entrypoint
    (``/usr/local/bin/with-xvfb``) starts Xvfb :99 and exports DISPLAY
    before launching. If DISPLAY is unset at runtime we coerce back to
    ``headless=True`` with ``--headless=new`` and log a warning — see
    :func:`_resolve_headless` for rationale (#2431).

    When ``use_proxy`` is True, the browser launches through the active
    proxy provider (see :mod:`src.shared.proxy`).

    When ``persistent_context`` is True, uses
    ``pw.chromium.launch_persistent_context`` with an ephemeral
    user-data-dir — needed for Akamai / PerimeterX / DataDome sites that
    reject vanilla ``launch + new_context`` profiles. Usually combined
    with ``"channel": "chrome"`` to use the system Chrome binary (which
    is on the trusted-vendor list for most bot managers, unlike
    Playwright's bundled Chromium).
    """
    config = config or {}
    requested_headless = bool(config.get("headless", True))
    # Boards that need Akamai/PerimeterX bypass set ``headless: false`` and
    # rely on the ``browser-1`` container's xvfb entrypoint to provide an
    # X server. If DISPLAY is missing at runtime (entrypoint bypassed,
    # image predates the entrypoint), a headful launch crashes with
    # "launched a headed browser without having a XServer running".
    # Coerce to headless + ``--headless=new`` so the run degrades to
    # bot-manager-blocked rather than blocking the worker slot every
    # cycle. See _resolve_headless for the full rationale (#2431).
    headless, headless_coerced = _resolve_headless(requested_headless)
    warmup_url = config.get("warmup_url")
    cookies = config.get("cookies")
    persistent = bool(config.get("persistent_context"))
    channel = config.get("channel")
    viewport = config.get("viewport", DEFAULT_VIEWPORT)
    locale = config.get("locale", DEFAULT_LOCALE)
    # When a real-browser channel is used, the binary's own UA string
    # (e.g. ``Chrome/146.0.0.0``) matches its JS fingerprint. Overriding
    # with ``DEFAULT_USER_AGENT`` (fixed ``Chrome/133``) creates a client
    # hint mismatch that Akamai's sensor detects. Keep the default UA
    # for bundled-Chromium launches (where Playwright's pinned version
    # doesn't match any real release anyway), but opt-out when channel
    # pins a shipping Chrome.
    if "user_agent" in config:
        user_agent = config["user_agent"]
    elif channel:
        user_agent = None
    else:
        user_agent = DEFAULT_USER_AGENT

    extra_args: list[str] = []
    if headless and (config.get("stealth") or headless_coerced):
        # Chromium's new headless mode (--headless=new) is less detectable
        # by anti-bot systems (Cloudflare Turnstile etc.). Enable via
        # stealth: true, or automatically when we coerced a headful
        # request into headless (#2431 — Akamai-gated boards fall back
        # here when xvfb is missing, and --headless=new gives them the
        # best chance of not being blocked outright).
        extra_args.append("--headless=new")
    if config.get("disable_http2"):
        extra_args.append("--disable-http2")
    if persistent:
        # Real-Chrome-profile shape: mask the ``navigator.webdriver``
        # blink feature that Akamai's sensor bundle reads before the
        # stealth init-script has a chance to mask the JS property.
        extra_args.append("--disable-blink-features=AutomationControlled")

    pw_proxy = None
    if use_proxy:
        from src.shared.proxy import playwright_proxy_for

        pw_proxy = playwright_proxy_for(use_proxy=True)

    if persistent:
        async with _open_persistent_page(
            pw,
            headless=headless,
            channel=channel,
            extra_args=extra_args,
            pw_proxy=pw_proxy,
            user_agent=user_agent,
            viewport=viewport,
            locale=locale,
            cookies=cookies,
            warmup_url=warmup_url,
            skip_ssl=bool(config.get("skip_ssl")),
        ) as page:
            yield page
        return

    launch_kwargs: dict = {"headless": headless}
    if channel:
        launch_kwargs["channel"] = channel
    if extra_args:
        launch_kwargs["args"] = extra_args
    if pw_proxy:
        launch_kwargs["proxy"] = pw_proxy

    browser = await pw.chromium.launch(**launch_kwargs)
    context = None
    try:
        ctx_kwargs: dict = {}
        if user_agent:
            ctx_kwargs["user_agent"] = user_agent
        if config.get("skip_ssl"):
            ctx_kwargs["ignore_https_errors"] = True
        context = await browser.new_context(**ctx_kwargs)
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


@asynccontextmanager
async def _open_persistent_page(
    pw,
    *,
    headless: bool,
    channel: str | None,
    extra_args: list[str],
    pw_proxy: dict | None,
    user_agent: str,
    viewport: dict | None,
    locale: str | None,
    cookies: list[dict] | None,
    warmup_url: str | None,
    skip_ssl: bool = False,
) -> AsyncIterator:
    """``launch_persistent_context`` variant of :func:`open_page`.

    Kept in a separate helper so the vanilla-launch path above stays a
    straight line. The user-data-dir is an ephemeral tmpdir, cleaned up
    after the context closes — we don't persist Akamai cookies between
    runs because (a) ``_abck`` tokens are short-lived and (b) leaking a
    profile between concurrent board jobs would cause cross-board
    interference under the browser worker pool.
    """
    user_data_dir = tempfile.mkdtemp(prefix="pw_persist_")
    launch_kwargs: dict = {"headless": headless}
    if channel:
        launch_kwargs["channel"] = channel
    if extra_args:
        launch_kwargs["args"] = extra_args
    if pw_proxy:
        launch_kwargs["proxy"] = pw_proxy
    # persistent_context takes the context-level knobs directly; there's
    # no separate ``new_context`` call.
    if user_agent:
        launch_kwargs["user_agent"] = user_agent
    if viewport:
        launch_kwargs["viewport"] = viewport
    if locale:
        launch_kwargs["locale"] = locale
    if skip_ssl:
        launch_kwargs["ignore_https_errors"] = True

    context = await pw.chromium.launch_persistent_context(user_data_dir, **launch_kwargs)
    try:
        context.set_default_timeout(CONTEXT_TIMEOUT)
        if cookies:
            await context.add_cookies(_resolve_placeholders(cookies))
        # launch_persistent_context always opens one blank page; reuse
        # it rather than open a second (which would look more like a
        # user typing into a new tab, but also doubles the startup cost).
        page = context.pages[0] if context.pages else await context.new_page()
        if warmup_url:
            log.debug("browser.warmup", url=warmup_url, persistent=True)
            await page.goto(warmup_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        yield page
    finally:
        with contextlib.suppress(Exception):
            await context.close()
        shutil.rmtree(user_data_dir, ignore_errors=True)


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


# Playwright raises a plain ``Error`` when ``page.content()`` is called while
# the page is in the middle of a navigation (SPA route change, client redirect,
# delayed meta-refresh, late-firing analytics that triggers a reload). The
# error message is stable across versions — substring match is sufficient.
# Observed on post.ch (issue #2188); same race can occur on any board whose
# final actions trigger navigation or whose SPA settles after the configured
# wait strategy fires.
_CONTENT_NAVIGATING_MARKER = "page is navigating and changing the content"
_SAFE_CONTENT_RETRIES = 2
_SAFE_CONTENT_SETTLE_MS = 500


async def safe_content(page) -> str:
    """Return ``page.content()`` with retry on the navigation-race error.

    Playwright refuses to serialize the DOM when the page is mid-navigation
    and raises ``Error("... page is navigating and changing the content")``.
    The race is almost always transient: waiting for ``domcontentloaded``
    after the error lets the new document settle, and a retry succeeds.
    Non-matching errors propagate so real failures are not swallowed.
    """
    last_exc: Exception | None = None
    for attempt in range(_SAFE_CONTENT_RETRIES + 1):
        try:
            html = await page.content()
        except Exception as exc:  # noqa: BLE001 — Playwright raises plain Error
            if _CONTENT_NAVIGATING_MARKER not in str(exc):
                raise
            last_exc = exc
            if attempt == _SAFE_CONTENT_RETRIES:
                break
            metrics.browser_content_retry_total.labels(outcome="retry").inc()
            log.info("browser.content.navigating_retry", attempt=attempt + 1)
            # Tolerate wait failure — we retry page.content() either way.
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=CONTEXT_TIMEOUT)
            await asyncio.sleep(_SAFE_CONTENT_SETTLE_MS / 1000)
            continue
        else:
            if attempt > 0:
                metrics.browser_content_retry_total.labels(outcome="recovered").inc()
            return html
    metrics.browser_content_retry_total.labels(outcome="failed").inc()
    assert last_exc is not None
    raise last_exc


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
            return await safe_content(page)

    from playwright.async_api import async_playwright

    async with (
        async_playwright() as _pw,
        open_page(_pw, config, use_proxy=bool(config.get("proxy"))) as page,
    ):
        await navigate(page, url, config)
        await run_actions(page, config.get("actions", []))
        return await safe_content(page)
