"""Recruiterbox / Trakstar Hire detail-page scraper.

Current Trakstar Hire pages are server-rendered but do not publish JSON-LD.
The provider exposes stable classes for the title, opening metadata, and job
description, so parsing those nodes directly is cheaper and more reliable
than a rendered generic DOM configuration.
"""

from __future__ import annotations

import re

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.enum_normalize import normalize_job_location_type
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_MARKERS = ("js-job-title", "opening-info", "jobdesciption")
_LOCATION_SELECTORS = (
    ".meta-job-location-city",
    ".meta-job-location-state",
    ".meta-job-location-country",
)


def _node_text(tree: LexborHTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    value = re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip()
    return value or None


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Parse one server-rendered Recruiterbox / Trakstar job page."""
    _ = config
    tree = LexborHTMLParser(html)

    location_parts = [
        value
        for selector in _LOCATION_SELECTORS
        if (value := _node_text(tree, selector)) is not None
    ]
    location = ", ".join(location_parts) or None

    opening = tree.css_first(".opening-info")
    metadata_values: list[str] = []
    if opening is not None:
        location_classes = {selector.removeprefix(".") for selector in _LOCATION_SELECTORS}
        for node in opening.css("span"):
            classes = set((node.attributes.get("class") or "").split())
            if classes & location_classes:
                continue
            value = re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip(" |")
            if value:
                metadata_values.append(value)

    employment_type = metadata_values[0] if metadata_values else None
    location_type_text = next(
        (
            value
            for value in metadata_values[1:]
            if re.search(r"remote|hybrid|on[ -]?site", value, re.I)
        ),
        None,
    )

    description_node = tree.css_first(".jobdesciption")
    description_html = description_node.inner_html if description_node is not None else None
    description = description_html.strip() if description_html else None

    return JobContent(
        title=_node_text(tree, ".js-job-title"),
        description=description or None,
        locations=[location] if location else None,
        employment_type=employment_type,
        job_location_type=normalize_job_location_type(location_type_text, default=None),
    )


def can_handle(htmls: list[str]) -> dict | None:
    """Detect current server-rendered Recruiterbox / Trakstar markup."""
    if htmls and all(all(marker in html for marker in _MARKERS) for html in htmls):
        return {}
    return None


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch and parse one Recruiterbox / Trakstar posting."""
    _ = kwargs
    response = await http.get(url, follow_redirects=True)
    if response.status_code != 200:
        log.warning("recruiterbox_scraper.detail_failed", url=url, status=response.status_code)
        return JobContent()
    return parse_html(response.text, config)


register("recruiterbox", scrape, can_handle=can_handle, parse_html=parse_html)
