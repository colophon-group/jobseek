"""Skip scraper — placeholder for monitors that provide full job data.

Registered as scraper type ``skip``. Should never actually be called —
if it is, something went wrong in the batch processor's board classification.
"""

from __future__ import annotations

from src.core.scrapers import JobContent, register


async def scrape(url: str, config: dict, http=None, **kwargs) -> JobContent:
    """This scraper should never be invoked."""
    raise RuntimeError(
        f"skip scraper called for {url!r} — this monitor provides full data "
        "and should not trigger scraping. Check board configuration."
    )


register("skip", scrape)
