"""Playwright-based job URL discovery monitor.

This is the most expensive monitor type — it launches a headless browser,
intercepts network requests, and discovers job listing URLs from JS-rendered pages.

Requires playwright to be installed: `uv run playwright install chromium`
"""

from __future__ import annotations

import structlog

from src.core.monitors import register

log = structlog.get_logger()

MAX_URLS = 10_000


async def discover(board: dict, client=None) -> set[str]:
    """Discover job URLs from a JS-rendered career page using Playwright.

    This monitor:
    1. Loads the page in a headless browser
    2. Intercepts XHR/fetch JSON responses
    3. Scores response arrays to find the job list API
    4. Falls back to DOM link scraping if no API is found
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as err:
        raise RuntimeError(
            "playwright is required for the discover monitor. "
            "Install with: uv sync --group dev && uv run playwright install chromium"
        ) from err

    metadata = board.get("metadata") or {}
    board_url = board["board_url"]
    wait_strategy = metadata.get("wait", "networkidle")

    urls: set[str] = set()
    api_responses: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        async def handle_response(response):
            try:
                if "json" in (response.headers.get("content-type") or ""):
                    body = await response.json()
                    if isinstance(body, list) and len(body) > 0:
                        api_responses.append(
                            {
                                "url": response.url,
                                "data": body,
                            }
                        )
                    elif isinstance(body, dict):
                        for value in body.values():
                            if isinstance(value, list) and len(value) > 5:
                                api_responses.append(
                                    {
                                        "url": response.url,
                                        "data": value,
                                    }
                                )
                                break
            except Exception:
                pass

        page.on("response", handle_response)

        await page.goto(board_url, wait_until=wait_strategy, timeout=30000)

        # Try to extract URLs from intercepted API responses
        for resp in api_responses:
            for item in resp["data"]:
                if not isinstance(item, dict):
                    continue
                # Look for URL-like fields
                for key in ("url", "hostedUrl", "absolute_url", "apply_url", "href", "link"):
                    val = item.get(key)
                    if isinstance(val, str) and val.startswith("http"):
                        urls.add(val)
                        break

        # Fall back to DOM link scraping if no API URLs found
        if not urls:
            links = await page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => h.startsWith('http'))
            """)
            # Filter for likely job links (heuristic)
            job_keywords = {"job", "career", "position", "posting", "opening", "role", "vacancy"}
            for link in links:
                lower = link.lower()
                if any(kw in lower for kw in job_keywords):
                    urls.add(link)

        await browser.close()

    if len(urls) > MAX_URLS:
        log.warning("discover.truncated", total=len(urls), cap=MAX_URLS)
        urls = set(sorted(urls)[:MAX_URLS])

    log.info("discover.complete", board_url=board_url, urls_found=len(urls))
    return urls


# discover monitor is not auto-detectable (no can_handle).
# Agents assign it manually when no other monitor type works.
register("discover", discover, cost=100)
