"""Paylocity Recruiting detail-page scraper.

Paylocity job details are server-rendered even when the surrounding page
shows an unsupported-browser warning.  This scraper extracts the stable job
preview markup without requiring Playwright.
"""

from __future__ import annotations

import re

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser, LexborNode

from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_PAYLOCITY_MARKER_RE = re.compile(r"\b(?:ATSPublicBaseUrl|job-preview-details)\b")


def _next_element(node: LexborNode | None) -> LexborNode | None:
    current = node.next if node is not None else None
    while current is not None and current.tag == "-text":
        current = current.next
    return current


def _field_after_header(tree: LexborHTMLParser, label: str) -> LexborNode | None:
    for header in tree.css(".job-listing-header"):
        if header.text(strip=True).casefold() == label.casefold():
            return _next_element(header)
    return None


def _parse_location(tree: LexborHTMLParser) -> tuple[list[str] | None, str | None]:
    preview = tree.css_first(".preview-location")
    if preview is None:
        return None, None

    map_link = preview.css_first('a[href*="maps.google"]')
    if map_link is not None:
        location = map_link.text(strip=True)
        return ([location] if location else None), "onsite"

    parts = [part.strip() for part in preview.text(separator="|", strip=True).split("|")]
    parts = [part for part in parts if part and part != "•"]
    if not parts:
        return None, None

    marker = parts[0].casefold()
    if marker in {"fully remote", "hybrid remote", "on-site", "onsite"} and len(parts) > 1:
        location = parts[1]
    else:
        location = parts[0]

    combined = " ".join(parts).casefold()
    if "hybrid" in combined:
        location_type = "hybrid"
    elif "remote" in combined:
        location_type = "remote"
    else:
        location_type = "onsite"

    return ([location] if location else None), location_type


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Parse one Paylocity detail page."""
    _ = config
    tree = LexborHTMLParser(html)

    title_node = tree.css_first(".job-preview-title span")
    title = title_node.text(strip=True) if title_node is not None else None

    description_node = _field_after_header(tree, "Description")
    description_html = description_node.inner_html if description_node is not None else None
    description = description_html.strip() if description_html else None

    job_type_node = _field_after_header(tree, "Job Type")
    employment_type = job_type_node.text(strip=True) if job_type_node is not None else None

    locations, job_location_type = _parse_location(tree)

    return JobContent(
        title=title or None,
        description=description or None,
        locations=locations,
        employment_type=employment_type or None,
        job_location_type=job_location_type,
    )


def can_handle(htmls: list[str]) -> dict | None:
    """Detect Paylocity detail markup during scraper probing."""
    if htmls and all(_PAYLOCITY_MARKER_RE.search(html) for html in htmls):
        return {}
    return None


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch and parse a Paylocity job detail page."""
    _ = kwargs
    response = await http.get(url, follow_redirects=True)
    if response.status_code != 200:
        log.warning("paylocity_scraper.detail_failed", url=url, status=response.status_code)
        return JobContent()
    return parse_html(response.text, config)


register("paylocity", scrape, can_handle=can_handle, parse_html=parse_html)
