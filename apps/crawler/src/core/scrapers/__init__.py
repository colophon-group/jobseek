"""Scraper registry and shared types.

Scrapers extract structured job details from individual pages. Only needed
when the monitor returns URL-only results (sitemap, dom). API monitors
(greenhouse, lever) return full data and skip the scraper step.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger()


@dataclass(slots=True)
class JobContent:
    """Structured job data extracted from a single page.

    Text fields use **HTML** to preserve document structure (headings,
    paragraphs, lists).  ``description`` is an HTML fragment — the same
    format that API monitors (Greenhouse, Lever) already produce.
    """

    title: str | None = None
    #: HTML fragment preserving the original page structure
    #: (``<p>``, ``<ul><li>``, ``<h3>``, etc.).
    description: str | None = None
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    #: ISO 639-1 language code (e.g. "en", "de"). Detected or scraper-provided.
    language: str | None = None
    #: Optional structured data (skills, responsibilities, qualifications,
    #: validThrough, etc.)
    extras: dict | None = None
    metadata: dict | None = None


_TAG_RE = re.compile(r"<[^>]+>")


def _plain(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    return _TAG_RE.sub(" ", html).strip()


def enrich_description(obj: object) -> None:
    """Append structured extras (responsibilities, qualifications, skills) to description.

    When scrapers extract these as separate structured data, they should also
    appear in the description HTML so it remains self-contained.  Skips any
    section whose text content already appears in the existing description.
    Mutates *obj* in place.
    """
    if not obj.extras:
        return

    desc_plain = _plain(obj.description).lower() if obj.description else ""

    sections: list[str] = []
    for key, heading in [
        ("responsibilities", "Responsibilities"),
        ("qualifications", "Qualifications"),
        ("skills", "Skills"),
    ]:
        items = obj.extras.get(key)
        if not items:
            continue

        if isinstance(items, str):
            # Check if the string content is already in the description
            snippet = _plain(items).lower()
            if desc_plain and snippet and snippet[:80] in desc_plain:
                continue
            sections.append(f"<h3>{heading}</h3>\n{items}")
        elif isinstance(items, list):
            # Check if the first non-trivial item is already in the description
            for item in items:
                snippet = _plain(str(item)).lower()
                if len(snippet) >= 10:
                    if desc_plain and snippet[:80] in desc_plain:
                        break  # already present — skip whole section
                    else:
                        li = "".join(f"<li>{it}</li>" for it in items)
                        sections.append(f"<h3>{heading}</h3>\n<ul>{li}</ul>")
                    break
            else:
                # All items too short to check — append anyway
                li = "".join(f"<li>{it}</li>" for it in items)
                sections.append(f"<h3>{heading}</h3>\n<ul>{li}</ul>")

    if not sections:
        return

    extra_html = "\n".join(sections)
    if obj.description:
        obj.description = obj.description + "\n" + extra_html
    else:
        obj.description = extra_html


ScrapeFunc = Callable[..., Awaitable[JobContent]]
CanHandleFunc = Callable[[list[str]], dict | None]
ParseHtmlFunc = Callable[[str, dict], JobContent]
ProbePwFunc = Callable[[list[str], object], Awaitable[tuple[dict | None, str]]]


@dataclass
class ScraperType:
    name: str
    scrape: ScrapeFunc
    can_handle: CanHandleFunc | None = None
    parse_html: ParseHtmlFunc | None = None
    probe_pw: ProbePwFunc | None = None


_REGISTRY: dict[str, ScraperType] = {}

# Display order for probe results
_PROBE_ORDER = ["json-ld", "nextdata", "embedded", "dom", "api_sniffer"]


def register(
    name: str,
    scrape: ScrapeFunc,
    *,
    can_handle: CanHandleFunc | None = None,
    parse_html: ParseHtmlFunc | None = None,
    probe_pw: ProbePwFunc | None = None,
) -> None:
    """Register a scraper type."""
    _REGISTRY[name] = ScraperType(
        name=name,
        scrape=scrape,
        can_handle=can_handle,
        parse_html=parse_html,
        probe_pw=probe_pw,
    )


def get_scraper(name: str) -> ScrapeFunc:
    """Look up a scrape function by scraper type name."""
    if name in _REGISTRY:
        return _REGISTRY[name].scrape
    available = list(_REGISTRY.keys())
    raise ValueError(f"Unknown scraper type: {name!r}. Available: {available}")


# Quality fields checked in probe results
_QUALITY_FIELDS = [
    "title",
    "description",
    "locations",
    "employment_type",
    "job_location_type",
    "date_posted",
    "base_salary",
]


async def probe_scrapers(
    urls: list[str],
    http: httpx.AsyncClient,
    timeout: float = 30.0,
    pw=None,
) -> tuple[list[tuple[str, dict | None, str]], bool]:
    """Probe all registered scrapers against sample URLs.

    Fetches all URLs once (static HTTP), then runs each scraper's
    ``can_handle`` + ``parse_html`` against the fetched pages.

    Returns ``([(name, metadata_or_none, comment), ...], spa_suspect)``
    where results are sorted by display order (json-ld, nextdata, dom)
    and ``spa_suspect`` is True if any page has very little static text
    content (likely a JS-rendered SPA).
    """
    from src.shared.extract import flatten

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
    all_htmls = [html for _, html in fetched]
    static_failed = len(fetched) == 0

    # Detect SPA: check if any page has very little text content
    spa_suspect = False
    if not static_failed:
        for html in all_htmls:
            elements = flatten(html)
            text_len = sum(len(el.get("text", "")) for el in elements)
            if text_len < 200:
                spa_suspect = True
                break

    # 2. Probe each scraper
    results: list[tuple[str, dict | None, str]] = []

    for name in _PROBE_ORDER:
        if name not in _REGISTRY:
            continue
        scraper = _REGISTRY[name]

        # Playwright-based probe path — needs more time (browser per URL)
        if scraper.probe_pw is not None:
            if pw is None:
                results.append((name, None, "Skipped \u2014 Playwright not available"))
                continue
            try:
                pw_timeout = max(timeout, 90.0)
                metadata, comment = await asyncio.wait_for(
                    scraper.probe_pw(urls, pw),
                    timeout=pw_timeout,
                )
                results.append((name, metadata, comment))
            except TimeoutError:
                results.append((name, None, "Timeout"))
            except Exception as exc:
                log.debug("probe_scrapers.probe_pw_error", scraper=name, exc_info=True)
                results.append((name, None, f"Error: {exc}"))
            continue

        if scraper.can_handle is None or scraper.parse_html is None:
            results.append((name, None, "No auto-detection"))
            continue

        # Static scrapers need fetched HTML
        if static_failed:
            results.append((name, None, "Fetch failed \u2014 no pages retrieved"))
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

    return results, spa_suspect


# Import modules to trigger registration
from src.core.scrapers import (  # noqa: E402
    api_sniffer,  # noqa: F401
    dom,  # noqa: F401
    embedded,  # noqa: F401
    jsonld,  # noqa: F401
    nextdata,  # noqa: F401
)
