"""JSON-LD scraper — extracts schema.org/JobPosting data from page HTML.

Parses <script type="application/ld+json"> blocks for JobPosting structured data.
No configuration needed — handles all standard schema.org fields automatically.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()


class _JsonLdExtractor(HTMLParser):
    """Extracts JSON-LD blocks from HTML."""

    def __init__(self):
        super().__init__()
        self._in_jsonld = False
        self._data: list[str] = []
        self.results: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attr_dict = dict(attrs)
            if attr_dict.get("type") == "application/ld+json":
                self._in_jsonld = True
                self._data = []

    def handle_data(self, data):
        if self._in_jsonld:
            self._data.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            raw = "".join(self._data).strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    self.results.append(parsed)
                except json.JSONDecodeError:
                    pass


def _find_job_posting(data: dict | list) -> dict | None:
    """Recursively find a JobPosting object in JSON-LD data."""
    if isinstance(data, list):
        for item in data:
            result = _find_job_posting(item)
            if result:
                return result
        return None

    if isinstance(data, dict):
        type_val = data.get("@type", "")
        if isinstance(type_val, str) and "JobPosting" in type_val:
            return data
        if isinstance(type_val, list) and any("JobPosting" in t for t in type_val):
            return data

        # Check @graph
        graph = data.get("@graph")
        if isinstance(graph, list):
            return _find_job_posting(graph)

    return None


def _extract_locations(posting: dict) -> list[str] | None:
    """Extract locations from jobLocation field."""
    locations: list[str] = []
    job_location = posting.get("jobLocation")

    if job_location is None:
        return None

    items = job_location if isinstance(job_location, list) else [job_location]

    for loc in items:
        if not isinstance(loc, dict):
            continue
        # Try name first
        name = loc.get("name")
        if name:
            locations.append(name)
            continue
        # Build from address
        address = loc.get("address")
        if isinstance(address, dict):
            parts = []
            for field in ("addressLocality", "addressRegion", "addressCountry"):
                val = address.get(field)
                if val:
                    if isinstance(val, dict):
                        val = val.get("name", "")
                    parts.append(str(val))
            if parts:
                locations.append(", ".join(parts))

    return locations or None


def _extract_salary(posting: dict) -> dict | None:
    """Extract salary from baseSalary field."""
    base_salary = posting.get("baseSalary")
    if not isinstance(base_salary, dict):
        return None

    currency = base_salary.get("currency")
    value = base_salary.get("value")

    if isinstance(value, dict):
        return {
            "currency": currency,
            "min": value.get("minValue"),
            "max": value.get("maxValue"),
            "unit": value.get("unitText", "").lower() or None,
        }
    elif isinstance(value, (int, float)):
        return {
            "currency": currency,
            "min": value,
            "max": value,
            "unit": None,
        }

    return None


def _text_or_list(val) -> list[str] | None:
    """Convert a string or list of strings to a list."""
    if isinstance(val, str):
        return [val] if val.strip() else None
    if isinstance(val, list):
        result = [str(v).strip() for v in val if v]
        return result or None
    return None


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_posting(posting: dict) -> JobContent:
    """Convert a schema.org JobPosting dict to JobContent."""
    description = posting.get("description")
    if isinstance(description, str) and "<" in description:
        # Keep HTML description as-is (same as greenhouse/lever)
        pass

    return JobContent(
        title=posting.get("title") or posting.get("name"),
        description=description,
        locations=_extract_locations(posting),
        employment_type=posting.get("employmentType"),
        job_location_type=posting.get("jobLocationType"),
        date_posted=posting.get("datePosted"),
        valid_through=posting.get("validThrough"),
        base_salary=_extract_salary(posting),
        skills=_text_or_list(posting.get("skills")),
        responsibilities=_text_or_list(posting.get("responsibilities")),
        qualifications=_text_or_list(
            posting.get("qualifications") or posting.get("educationRequirements")
        ),
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, pw=None, **kwargs) -> JobContent:
    """Extract job data from JSON-LD on a page."""
    response = await http.get(url, follow_redirects=True)
    response.raise_for_status()

    extractor = _JsonLdExtractor()
    extractor.feed(response.text)

    for block in extractor.results:
        posting = _find_job_posting(block)
        if posting:
            content = _parse_posting(posting)
            log.debug("jsonld.extracted", url=url, title=content.title)
            return content

    log.warning("jsonld.not_found", url=url)
    return JobContent()


async def probe(url: str, http: httpx.AsyncClient) -> bool:
    """Check if a URL has JSON-LD JobPosting data. Used by validate --probe-jsonld."""
    try:
        response = await http.get(url, follow_redirects=True)
        if response.status_code != 200:
            return False
        extractor = _JsonLdExtractor()
        extractor.feed(response.text)
        return any(_find_job_posting(block) for block in extractor.results)
    except Exception:
        return False


register("json-ld", scrape)
