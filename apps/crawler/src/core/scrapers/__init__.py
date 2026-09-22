"""Scraper registry and shared types.

Scrapers extract structured job details from individual pages. Only needed
when the monitor returns URL-only results (sitemap, dom). API monitors
(greenhouse, lever) return full data and skip the scraper step.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx
import structlog

from src.core.job_content import JobContent as JobContent
from src.core.job_content import enrich_description as enrich_description

log = structlog.get_logger()


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
    needs_browser: bool = False


_REGISTRY: dict[str, ScraperType] = {}

# Display order for probe results
_PROBE_ORDER = [
    "json-ld",
    "nextdata",
    "embedded",
    "phuketall",
    "veryeast",
    "tupu360",
    "recruiterbox",
    "onlyfy",
    "paycor",
    "pdf",
    "taleo",
    "dom",
    "seek",
    "api_sniffer",
]


def register(
    name: str,
    scrape: ScrapeFunc,
    *,
    can_handle: CanHandleFunc | None = None,
    parse_html: ParseHtmlFunc | None = None,
    probe_pw: ProbePwFunc | None = None,
    needs_browser: bool = False,
) -> None:
    """Register a scraper type."""
    _REGISTRY[name] = ScraperType(
        name=name,
        scrape=scrape,
        can_handle=can_handle,
        parse_html=parse_html,
        probe_pw=probe_pw,
        needs_browser=needs_browser,
    )


def get_scraper(name: str) -> ScrapeFunc:
    """Look up a scrape function by scraper type name."""
    if name in _REGISTRY:
        return _REGISTRY[name].scrape
    available = list(_REGISTRY.keys())
    raise ValueError(f"Unknown scraper type: {name!r}. Available: {available}")


def get_scraper_type(name: str) -> ScraperType | None:
    """Look up a full ScraperType object by name."""
    return _REGISTRY.get(name)


def all_scraper_types() -> frozenset[str]:
    """Return the set of all registered scraper type names."""
    return frozenset(_REGISTRY)


def scraper_needs_browser(name: str, config: dict | None = None) -> bool:
    """Return True if the scraper requires a Playwright browser.

    When *config* is provided, api_sniffer in HTTP mode (``api_url`` set)
    does not need a browser despite being registered with ``needs_browser=True``.
    The scrapers listed in ``_RENDER_AWARE_SCRAPERS`` use Playwright when
    ``render`` is true in config.
    """
    entry = _REGISTRY.get(name)
    if not entry:
        return False
    if entry.needs_browser and config and config.get("api_url"):
        return False
    if entry.needs_browser:
        return True
    return name in _RENDER_AWARE_SCRAPERS and bool(config and config.get("render"))


# Scrapers that call ``shared.browser.render`` when ``render: true`` is set
# in their config. Any board routing to one of these with ``render: true``
# must be dispatched to a browser worker — otherwise Playwright fails with
# "Executable doesn't exist" on slim workers that don't ship Chromium.
#
# nextdata is a thin wrapper around embedded and goes through the same render
# path, so both belong here.
_RENDER_AWARE_SCRAPERS = frozenset({"dom", "json-ld", "embedded", "nextdata"})


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

_LEGACY_WORD_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


def _static_document_type(url: str, content: bytes) -> str | None:
    """Identify bounded document fallbacks without treating arbitrary ZIPs as DOCX."""
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    stripped = content.lstrip()
    if stripped.startswith(b"%PDF-"):
        return "pdf"
    if suffix == ".docx" and content.startswith(b"PK\x03\x04"):
        return "docx"
    if suffix == ".doc" and content.startswith(_LEGACY_WORD_MAGIC):
        return "doc"
    return None


def _document_fallback_probe_config(
    pages: list[tuple[str, str, bytes]],
) -> tuple[dict, dict[str, int]] | None:
    """Build a DOM document-fallback config when every fetched sample is a document."""
    if not pages:
        return None
    document_types = [_static_document_type(url, content) for url, _html, content in pages]
    if any(document_type is None for document_type in document_types):
        return None

    counts = {
        document_type: document_types.count(document_type)
        for document_type in ("doc", "pdf", "docx")
        if document_type in document_types
    }
    return (
        {
            "render": False,
            # DOM requires at least one HTML step even though document responses
            # return through document_fallback before HTML extraction.
            "steps": [{"tag": "h1", "field": "title"}],
            "document_fallback": {
                document_type: {"title_source": "text"} for document_type in counts
            },
        },
        counts,
    )


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
    pages: list[tuple[str, str | None, bytes | None]] = []

    async def _fetch(url: str) -> tuple[str, str | None, bytes | None]:
        try:
            resp = await asyncio.wait_for(
                http.get(url, follow_redirects=True),
                timeout=timeout,
            )
            if resp.status_code == 200:
                text = resp.text
                content = getattr(resp, "content", None)
                if not isinstance(content, bytes):
                    content = text.encode("utf-8", errors="replace")
                return url, text, content
            log.debug("probe_scrapers.fetch_non_200", url=url, status=resp.status_code)
            return url, None, None
        except Exception as exc:
            log.debug("probe_scrapers.fetch_error", url=url, error=str(exc))
            return url, None, None

    pages = await asyncio.gather(*[_fetch(u) for u in urls])

    fetched = [
        (url, html, content)
        for url, html, content in pages
        if html is not None and content is not None
    ]
    all_htmls = [html for _, html, _content in fetched]
    static_failed = len(fetched) == 0
    document_probe = _document_fallback_probe_config(fetched)

    # Detect SPA: check if any page has very little text content
    spa_suspect = False
    if not static_failed and document_probe is None:
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

        if name == "dom" and document_probe is not None:
            config, document_counts = document_probe
            count_text = ", ".join(
                f"{count} {document_type.upper()}"
                for document_type, count in document_counts.items()
            )
            results.append(
                (
                    name,
                    {
                        "config": config,
                        "total": len(fetched),
                        "titles": 0,
                        "descriptions": 0,
                        "locations": 0,
                        "fields": {},
                        "document_types": document_counts,
                    },
                    f"Detected static documents ({count_text}); run scraper to verify fields",
                )
            )
            continue

        # Playwright-based probe path — needs more time (browser per URL).
        # Hybrid scrapers such as DOM use it only when static requests failed
        # or returned a shell-only SPA/challenge document; otherwise preserve
        # the cheaper static can_handle + parse_html path below.
        use_pw_probe = scraper.probe_pw is not None and (
            scraper.can_handle is None or scraper.parse_html is None or static_failed or spa_suspect
        )
        if use_pw_probe:
            if pw is None:
                results.append((name, None, "Skipped \u2014 Playwright not available"))
                continue
            try:
                pw_timeout = max(timeout, 90.0)
                probe_pw = scraper.probe_pw
                assert probe_pw is not None
                probe_metadata, comment = await asyncio.wait_for(
                    probe_pw(urls, pw),
                    timeout=pw_timeout,
                )
                results.append((name, probe_metadata, comment))
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
        for _url, html, _content in fetched:
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
    adp,  # noqa: F401
    api_sniffer,  # noqa: F401
    bite,  # noqa: F401
    dom,  # noqa: F401
    eightfold,  # noqa: F401
    embedded,  # noqa: F401
    headhunter,  # noqa: F401
    infor,  # noqa: F401
    jazzhr,  # noqa: F401
    jobconvo,  # noqa: F401
    jobstreet,  # noqa: F401
    johdi,  # noqa: F401
    jsonld,  # noqa: F401
    linkedin,  # noqa: F401
    mokahr,  # noqa: F401
    nextdata,  # noqa: F401
    notion,  # noqa: F401
    onlyfy,  # noqa: F401
    oracle_hcm,  # noqa: F401
    paycom,  # noqa: F401
    paycor,  # noqa: F401
    paylocity,  # noqa: F401
    pdf,  # noqa: F401
    peoplesoft,  # noqa: F401
    phuketall,  # noqa: F401
    recruiterbox,  # noqa: F401
    rippling,  # noqa: F401
    seek,  # noqa: F401
    skip,  # noqa: F401
    smartrecruiters,  # noqa: F401
    taleo,  # noqa: F401
    tupu360,  # noqa: F401
    veryeast,  # noqa: F401
    workable,  # noqa: F401
    workday,  # noqa: F401
)
