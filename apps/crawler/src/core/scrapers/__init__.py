"""Scraper registry and shared types.

Scrapers extract structured job details from individual pages. Only needed
when the monitor returns URL-only results (sitemap, dom). API monitors
(greenhouse, lever) return full data and skip the scraper step.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


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


@dataclass
class ScraperType:
    name: str
    scrape: ScrapeFunc


_REGISTRY: dict[str, ScraperType] = {}


def register(name: str, scrape: ScrapeFunc) -> None:
    """Register a scraper type."""
    _REGISTRY[name] = ScraperType(name=name, scrape=scrape)


def get_scraper(name: str) -> ScrapeFunc:
    """Look up a scrape function by scraper type name."""
    if name in _REGISTRY:
        return _REGISTRY[name].scrape
    available = list(_REGISTRY.keys())
    raise ValueError(f"Unknown scraper type: {name!r}. Available: {available}")


# Import modules to trigger registration
from src.core.scrapers import (  # noqa: E402
    dom,  # noqa: F401
    jsonld,  # noqa: F401
    nextdata,  # noqa: F401
)
