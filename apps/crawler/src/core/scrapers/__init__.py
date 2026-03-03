"""Scraper registry and shared types.

Scrapers extract structured job details from individual pages. Only needed
when the monitor returns URL-only results (sitemap, dom). API monitors
(greenhouse, lever) return full data and skip the scraper step.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields

import httpx
import structlog

log = structlog.get_logger()


@dataclass(slots=True)
class JobContent:
    """Structured job data extracted from a single page.

    Text fields use **HTML** to preserve document structure (headings,
    paragraphs, lists).  ``description`` is an HTML fragment — the same
    format that API monitors (Greenhouse, Lever) already produce.
    ``responsibilities`` and ``qualifications`` are arrays of plain-text
    strings (one item per bullet point).
    """

    title: str | None = None
    #: HTML fragment preserving the original page structure
    #: (``<p>``, ``<ul><li>``, ``<h3>``, etc.).
    description: str | None = None
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    valid_through: str | None = None
    base_salary: dict | None = None
    skills: list[str] | None = None
    #: Plain-text strings, one per bullet point.
    responsibilities: list[str] | None = None
    #: Plain-text strings, one per bullet point.
    qualifications: list[str] | None = None
    metadata: dict | None = None


ScrapeFunc = Callable[..., Awaitable[JobContent]]
CanHandleFunc = Callable[[list[str]], dict | None]
ParseHtmlFunc = Callable[[str, dict], JobContent]


@dataclass
class ScraperType:
    name: str
    scrape: ScrapeFunc
    can_handle: CanHandleFunc | None = None
    parse_html: ParseHtmlFunc | None = None


_REGISTRY: dict[str, ScraperType] = {}

# Display order for probe results
_PROBE_ORDER = ["json-ld", "nextdata", "dom"]


def register(
    name: str,
    scrape: ScrapeFunc,
    *,
    can_handle: CanHandleFunc | None = None,
    parse_html: ParseHtmlFunc | None = None,
) -> None:
    """Register a scraper type."""
    _REGISTRY[name] = ScraperType(
        name=name, scrape=scrape, can_handle=can_handle, parse_html=parse_html,
    )


def get_scraper(name: str) -> ScrapeFunc:
    """Look up a scrape function by scraper type name."""
    if name in _REGISTRY:
        return _REGISTRY[name].scrape
    available = list(_REGISTRY.keys())
    raise ValueError(f"Unknown scraper type: {name!r}. Available: {available}")


# Quality fields checked in probe results
_QUALITY_FIELDS = [
    "title", "description", "locations", "employment_type",
    "job_location_type", "date_posted", "valid_through", "base_salary",
    "skills", "responsibilities", "qualifications",
]


async def probe_scrapers(
    urls: list[str],
    http: httpx.AsyncClient,
    timeout: float = 30.0,
) -> list[tuple[str, dict | None, str]]:
    """Probe all registered scrapers against sample URLs.

    Fetches all URLs once (static HTTP), then runs each scraper's
    ``can_handle`` + ``parse_html`` against the fetched pages.

    Returns ``[(name, metadata_or_none, comment), ...]`` sorted by
    display order (json-ld, nextdata, dom).
    """
    # 1. Fetch all URLs in parallel (static HTTP)
    pages: list[tuple[str, str | None]] = []  # (url, html_or_none)

    async def _fetch(url: str) -> tuple[str, str | None]:
        try:
            resp = await asyncio.wait_for(
                http.get(url, follow_redirects=True),
                timeout=timeout,
            )
            if resp.status_code == 200:
                return url, resp.text
            log.debug("probe_scrapers.fetch_non_200", url=url, status=resp.status_code)
            return url, None
        except Exception as exc:
            log.debug("probe_scrapers.fetch_error", url=url, error=str(exc))
            return url, None

    pages = await asyncio.gather(*[_fetch(u) for u in urls])

    fetched = [(url, html) for url, html in pages if html is not None]
    if not fetched:
        return [
            (name, None, "Fetch failed \u2014 no pages retrieved")
            for name in _PROBE_ORDER
            if name in _REGISTRY
        ]

    all_htmls = [html for _, html in fetched]

    # 2. Probe each scraper
    results: list[tuple[str, dict | None, str]] = []

    for name in _PROBE_ORDER:
        if name not in _REGISTRY:
            continue
        scraper = _REGISTRY[name]

        if scraper.can_handle is None or scraper.parse_html is None:
            results.append((name, None, "No auto-detection"))
            continue

        # Pass all fetched HTMLs to can_handle for collective analysis
        config = scraper.can_handle(all_htmls)
        if config is None:
            results.append((name, None, "Not detected"))
            continue

        # Run parse_html on all fetched pages
        total = len(fetched)
        field_counts: dict[str, int] = {f: 0 for f in _QUALITY_FIELDS}
        for _url, html in fetched:
            try:
                content = scraper.parse_html(html, config)
            except Exception:
                log.debug("probe_scrapers.parse_error", scraper=name, url=_url, exc_info=True)
                continue
            for f in _QUALITY_FIELDS:
                if getattr(content, f, None):
                    field_counts[f] += 1

        # Build comment
        core_parts = [
            f"{field_counts['title']}/{total} titles",
            f"{field_counts['description']}/{total} desc",
            f"{field_counts['locations']}/{total} locations",
        ]
        comment = ", ".join(core_parts)

        # Build metadata
        metadata: dict = {
            "config": config,
            "total": total,
            "titles": field_counts["title"],
            "descriptions": field_counts["description"],
            "locations": field_counts["locations"],
            "fields": {f: c for f, c in field_counts.items() if c > 0},
        }

        results.append((name, metadata, comment))

    return results


# Import modules to trigger registration
from src.core.scrapers import (  # noqa: E402
    dom,  # noqa: F401
    jsonld,  # noqa: F401
    nextdata,  # noqa: F401
)
