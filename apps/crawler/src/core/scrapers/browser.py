"""Browser scraper — extracts job data from JS-rendered pages using Playwright.

Same as the HTML scraper but renders JavaScript first. Config maps field names
to CSS selectors, plus a "wait" strategy for page load.

Requires playwright: `uv sync --group dev && uv run playwright install chromium`
"""

from __future__ import annotations

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()


async def scrape(url: str, config: dict, http: httpx.AsyncClient) -> JobContent:
    """Extract job data from a JS-rendered page using Playwright + CSS selectors."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "playwright is required for the browser scraper. "
            "Install with: uv sync --group dev && uv run playwright install chromium"
        )

    wait_strategy = config.get("wait", "networkidle")

    # Extract selector config (everything except "wait")
    field_selectors: dict[str, str] = {}
    for key, val in config.items():
        if key != "wait" and isinstance(val, str) and val:
            field_selectors[key] = val

    results: dict[str, str] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(url, wait_until=wait_strategy, timeout=30000)

        for field_name, selector in field_selectors.items():
            try:
                element = await page.query_selector(selector)
                if element:
                    text = await element.inner_text()
                    text = text.strip()
                    if text:
                        results[field_name] = text
            except Exception:
                log.debug("browser.selector_failed", url=url, field=field_name, selector=selector)

        await browser.close()

    def get_list(key: str) -> list[str] | None:
        val = results.get(key)
        return [val] if val else None

    content = JobContent(
        title=results.get("title"),
        description=results.get("description"),
        locations=get_list("location"),
        employment_type=results.get("employment_type"),
        job_location_type=results.get("job_location_type"),
        qualifications=get_list("qualifications"),
        responsibilities=get_list("responsibilities"),
    )

    log.debug("browser.extracted", url=url, fields=list(results.keys()))
    return content


register("browser", scrape)
