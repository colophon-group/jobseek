"""Configuration-driven browser executor.

Centralizes Playwright browser lifecycle, navigation, and an action pipeline
so that monitors, scrapers, and scripts share one implementation.  All
behaviour is controlled via plain config dicts that flow from the JSON columns
in boards.csv — no schema change needed.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_WAIT = "networkidle"
DEFAULT_TIMEOUT = 30_000
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
BROWSER_KEYS = frozenset({"wait", "timeout", "user_agent", "headless", "actions"})

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@asynccontextmanager
async def open_page(
    pw,  # AsyncPlaywright
    config: dict | None = None,
) -> AsyncIterator:
    """Create browser → context → page.  Yields a Playwright *Page*.

    The caller manages the outer ``async_playwright()`` context so they can
    attach hooks (e.g. response interception) between page creation and
    navigation.

    Config keys consumed: ``user_agent``, ``headless`` (default ``True``).
    """
    config = config or {}
    headless = config.get("headless", True)
    user_agent = config.get("user_agent", DEFAULT_USER_AGENT)

    browser = await pw.chromium.launch(headless=headless)
    context = None
    try:
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()
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

    Config keys: ``wait`` (default ``"networkidle"``), ``timeout`` (default
    ``30000``).
    """
    config = config or {}
    wait_strategy = config.get("wait", DEFAULT_WAIT)
    timeout = config.get("timeout", DEFAULT_TIMEOUT)

    if wait_strategy not in VALID_WAIT_STRATEGIES:
        raise ValueError(
            f"Invalid wait strategy {wait_strategy!r}, "
            f"must be one of {sorted(VALID_WAIT_STRATEGIES)}"
        )

    await page.goto(url, wait_until=wait_strategy, timeout=timeout)


ACTION_TIMEOUT = 10.0  # seconds


async def run_actions(page, actions: list[dict]) -> None:
    """Execute an action pipeline sequentially on *page*.

    Each action is wrapped in a timeout (default 10s, configurable per-action
    via a ``"timeout"`` key).  On failure or timeout an individual action logs
    a warning and execution continues with the next action.
    """
    for action in actions:
        timeout = action.get("timeout", ACTION_TIMEOUT)
        kind = action.get("action")
        try:
            await asyncio.wait_for(_execute_action(page, action, kind), timeout=timeout)
        except asyncio.TimeoutError:
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
    else:
        log.warning("browser.action.unknown", action=kind)


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
        async with open_page(pw, config) as page:
            await navigate(page, url, config)
            await run_actions(page, config.get("actions", []))
            return await page.content()

    from playwright.async_api import async_playwright

    async with async_playwright() as _pw:
        async with open_page(_pw, config) as page:
            await navigate(page, url, config)
            await run_actions(page, config.get("actions", []))
            return await page.content()
