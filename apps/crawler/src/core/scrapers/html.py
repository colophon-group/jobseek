"""HTML scraper — extracts job data using CSS selectors.

Config maps field names to CSS selectors:
{
    "title": "h1.job-title",
    "location": "span.location",
    "description": ".job-description",
    ...
}

Fetches the page via static HTTP (no JavaScript rendering).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()


# Simple CSS selector support for common patterns.
# For full CSS selector support, we'd use beautifulsoup4 or lxml.
# This covers the patterns agents typically use:
#   tag, .class, #id, tag.class, [attr], [attr=val]


class _SimpleSelector:
    """Minimal CSS selector matcher for common patterns."""

    def __init__(self, selector: str):
        self.selector = selector.strip()

    def matches(self, tag: str, attrs: dict[str, str]) -> bool:
        sel = self.selector

        # ID selector: #id
        if sel.startswith("#"):
            return attrs.get("id") == sel[1:]

        # Class selector: .class
        if sel.startswith("."):
            classes = attrs.get("class", "").split()
            return sel[1:] in classes

        # Attribute selector: [attr=val] or [attr]
        if sel.startswith("[") and sel.endswith("]"):
            inner = sel[1:-1]
            if "=" in inner:
                key, val = inner.split("=", 1)
                val = val.strip("'\"")
                return attrs.get(key.strip()) == val
            return inner.strip() in attrs

        # Tag.class
        if "." in sel and not sel.startswith("."):
            tag_name, class_name = sel.split(".", 1)
            if tag != tag_name:
                return False
            classes = attrs.get("class", "").split()
            return class_name in classes

        # Tag only
        return tag == sel


class _SelectorExtractor(HTMLParser):
    """Extracts text content matching CSS selectors from HTML."""

    def __init__(self, selectors: dict[str, str]):
        super().__init__()
        self._selectors = {k: _SimpleSelector(v) for k, v in selectors.items()}
        self._capturing: str | None = None
        self._depth = 0
        self._capture_depth = 0
        self._data: list[str] = []
        self.results: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        if self._capturing is not None:
            return

        attr_dict = dict(attrs)
        for field_name, selector in self._selectors.items():
            if field_name not in self.results and selector.matches(tag, attr_dict):
                self._capturing = field_name
                self._capture_depth = self._depth
                self._data = []
                break

    def handle_data(self, data):
        if self._capturing is not None:
            self._data.append(data)

    def handle_endtag(self, tag):
        if self._capturing is not None and self._depth == self._capture_depth:
            text = " ".join(self._data).strip()
            text = re.sub(r"\s+", " ", text)
            if text:
                self.results[self._capturing] = text
            self._capturing = None
        self._depth -= 1


async def scrape(url: str, config: dict, http: httpx.AsyncClient) -> JobContent:
    """Extract job data from a page using CSS selectors."""
    response = await http.get(url, follow_redirects=True)
    response.raise_for_status()

    # Build selector map from config
    field_selectors: dict[str, str] = {}
    for key, val in config.items():
        if isinstance(val, str) and val:
            field_selectors[key] = val

    if not field_selectors:
        log.warning("html.no_selectors", url=url)
        return JobContent()

    extractor = _SelectorExtractor(field_selectors)
    extractor.feed(response.text)
    results = extractor.results

    # Map common field names to JobContent fields
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

    log.debug("html.extracted", url=url, fields=list(results.keys()))
    return content


register("html", scrape)
