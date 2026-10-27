"""Paycor/Newton detail-page scraper.

Paycor's legacy Newton career pages are server-rendered, but their visible
page heading is a generic employer heading (for example, ``JOIN OUR TEAM``).
The actual job fields live in stable ``gnewtonJob*`` elements inside a nested
table.  Parsing those elements directly avoids both browser rendering and the
generic DOM scraper's flattened-table ambiguity.
"""

from __future__ import annotations

import re

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.salary_extract import parse_salary_text
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_LABEL_RE = re.compile(r"^(?:Position|Location|Job Id|# of Openings|Pay Range):\s*", re.IGNORECASE)


def _field_text(tree: LexborHTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    value = node.text(separator=" ", strip=True).strip()
    value = _LABEL_RE.sub("", value).strip()
    return value or None


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Parse one Paycor/Newton job detail page."""
    _ = config
    tree = LexborHTMLParser(html)

    description_node = tree.css_first("#gnewtonJobDescriptionText")
    description_html = description_node.inner_html if description_node is not None else None
    description = description_html.strip() if description_html else None

    # Newton repeats this ID for street and city/state address lines.
    # Taking only the first node can drop the actual city entirely.
    location_parts = [
        value
        for node in tree.css("#gnewtonJobLocationInfo")
        if (value := node.text(separator=" ", strip=True).strip())
    ]
    location = ", ".join(dict.fromkeys(location_parts))
    job_id = _field_text(tree, "#gnewtonJobID")
    openings = _field_text(tree, "#gnewtonJobOpening")
    pay_range = _field_text(tree, "#gnewtonJobPayRange")
    metadata = {
        key: value
        for key, value in (("job_id", job_id), ("openings", openings), ("pay_range", pay_range))
        if value is not None
    }

    return JobContent(
        title=_field_text(tree, "#gnewtonJobPosition"),
        description=description,
        locations=[location] if location else None,
        base_salary=parse_salary_text(pay_range) if pay_range else None,
        metadata=metadata or None,
    )


def can_handle(htmls: list[str]) -> dict | None:
    """Detect the stable legacy Newton markup used by Paycor career pages."""
    if not htmls:
        return None
    for html in htmls:
        if "gnewtonJobPosition" not in html or "gnewtonJobDescriptionText" not in html:
            return None
    return {}


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch and parse a Paycor/Newton job detail page."""
    _ = kwargs
    response = await http.get(url, follow_redirects=True)
    if response.status_code != 200:
        log.warning("paycor_scraper.detail_failed", url=url, status=response.status_code)
        return JobContent()
    return parse_html(response.text, config)


register("paycor", scrape, can_handle=can_handle, parse_html=parse_html)
