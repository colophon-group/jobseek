"""Single-job scraper dispatcher.

Pure function — takes URL, scraper config, and HTTP client, returns job content.
No database awareness, no side effects beyond HTTP requests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.scrapers import JobContent, get_scraper

if TYPE_CHECKING:
    import httpx


async def scrape_one(
    url: str,
    scraper_type: str,
    scraper_config: dict | None,
    http: "httpx.AsyncClient",
) -> JobContent:
    """Extract structured job data from one URL.

    This is the single-job layer — a pure function with no DB awareness.
    """
    scraper = get_scraper(scraper_type)
    return await scraper(url, scraper_config or {}, http)
