"""JSON-LD scraper — extracts schema.org/JobPosting data from page HTML.

Parses <script type="application/ld+json"> blocks for JobPosting structured data.
No configuration needed — handles all standard schema.org fields automatically.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from html.parser import HTMLParser

import httpx
import structlog

from src.core.enum_normalize import normalize_salary_unit
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

# A single 403 on the HTTP path is usually a soft WAF signal: the first request
# from a cold session gets rate-limited, but the same client (now holding a
# challenge cookie) passes on the next attempt. Verified on careers.rtx.com:
# 50% cold-connection failure → 10/10 after a single retry on the same client.
# Small jittered sleep avoids hammering the WAF.
_RETRY_403_MAX = 1
_RETRY_403_BACKOFF_S = 0.5

_CTRL_REPLACEMENTS = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape_control_chars_in_strings(raw: str) -> str:
    """Escape control characters that appear inside JSON string values only."""
    out: list[str] = []
    in_string = False
    escape = False
    for ch in raw:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            out.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
        if in_string and ord(ch) < 0x20:
            out.append(_CTRL_REPLACEMENTS.get(ch, ""))
            continue
        out.append(ch)
    return "".join(out)


class _JsonLdExtractor(HTMLParser):
    """Extracts JSON-LD blocks from HTML."""

    def __init__(self):
        super().__init__()
        self._in_jsonld = False
        self._data: list[str] = []
        self.results: list[dict] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "meta":
            key = attr_dict.get("name") or attr_dict.get("property")
            content = attr_dict.get("content")
            if key and content:
                self.meta[key.lower()] = content
            return

        if tag == "script":
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
                    # Some sites emit literal control chars (newlines, tabs)
                    # inside JSON string values — escape them and retry.
                    cleaned = _escape_control_chars_in_strings(raw)
                    try:
                        parsed = json.loads(cleaned)
                        self.results.append(parsed)
                    except json.JSONDecodeError:
                        pass


def _normalize_keys(data):
    """Lowercase the first character of JSON-LD keys for case-insensitive matching.

    Some ATS providers (e.g. Cornerstone OnDemand) emit PascalCase property names
    like ``Title`` instead of the schema.org-standard ``title``.  Normalising to
    camelCase (first char lower) lets the rest of the parser use canonical names.
    Keys starting with ``@`` are left unchanged.
    """
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            nk = key if key.startswith("@") else (key[0].lower() + key[1:] if key else key)
            out[nk] = _normalize_keys(value)
        return out
    if isinstance(data, list):
        return [_normalize_keys(item) for item in data]
    return data


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
            return _normalize_keys(data)
        if isinstance(type_val, list) and any("JobPosting" in t for t in type_val):
            return _normalize_keys(data)

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


def _normalize_meta_locations(raw: str | None) -> list[str] | None:
    """Normalize TalentBrew/Radancy meta location values.

    Some TalentBrew job pages omit schema.org ``jobLocation`` while exposing
    the same location in tracking meta fields, usually as
    ``City~Region~Country``.  Use this only as a fallback when JSON-LD has no
    location.
    """
    if not raw:
        return None

    locations: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"\s*;\s*", raw):
        parts = [part.strip() for part in chunk.split("~") if part.strip()]
        text = ", ".join(parts) if parts else chunk.strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s*,\s*", ", ", text).strip(" ,")
        if text and text not in seen:
            seen.add(text)
            locations.append(text)

    return locations or None


def _extract_meta_locations(meta: dict[str, str]) -> list[str] | None:
    """Extract fallback locations from common TalentBrew/Radancy meta tags."""
    for key in ("gtm_tbcn_location", "dimension7"):
        locations = _normalize_meta_locations(meta.get(key))
        if locations:
            return locations
    return None


def _extract_salary(posting: dict) -> dict | None:
    """Extract salary from baseSalary field.

    Per schema.org/MonetaryAmount, ``unitText`` can appear on the OUTER
    ``baseSalary`` object regardless of whether ``value`` is a scalar or a
    nested ``QuantitativeValue``.  When both levels carry a ``unitText`` the
    nested one wins (it is closer to the value it qualifies).  See #3226.
    """
    base_salary = posting.get("baseSalary")
    if not isinstance(base_salary, dict):
        return None

    currency = base_salary.get("currency")
    value = base_salary.get("value")
    # schema.org uses ``MONTH``/``HOUR``/``DAY``/``WEEK``/``YEAR`` —
    # the central :func:`src.core.enum_normalize.normalize_salary_unit`
    # already covers the lowercase forms (and substring fallback for
    # future schema.org extensions).  Unrecognised tokens resolve to
    # ``None`` so the outer/inner fallback degrades cleanly.
    outer_unit = normalize_salary_unit(base_salary.get("unitText"))

    if isinstance(value, dict):
        inner_unit = normalize_salary_unit(value.get("unitText"))
        return {
            "currency": currency,
            "min": value.get("minValue"),
            "max": value.get("maxValue"),
            "unit": inner_unit or outer_unit,
        }
    elif isinstance(value, (int, float)):
        return {
            "currency": currency,
            "min": value,
            "max": value,
            "unit": outer_unit,
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

    extras: dict = {}
    skills = _text_or_list(posting.get("skills"))
    if skills:
        extras["skills"] = skills
    responsibilities = _text_or_list(posting.get("responsibilities"))
    if responsibilities:
        extras["responsibilities"] = responsibilities
    qualifications = _text_or_list(
        posting.get("qualifications") or posting.get("educationRequirements")
    )
    if qualifications:
        extras["qualifications"] = qualifications
    valid_through = posting.get("validThrough")
    if valid_through:
        extras["valid_through"] = valid_through

    return JobContent(
        title=posting.get("title") or posting.get("name"),
        description=description,
        locations=_extract_locations(posting),
        employment_type=posting.get("employmentType"),
        job_location_type=posting.get("jobLocationType"),
        date_posted=posting.get("datePosted"),
        base_salary=_extract_salary(posting),
        extras=extras or None,
    )


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Extract JobPosting data from pre-fetched HTML."""
    extractor = _JsonLdExtractor()
    extractor.feed(html)

    for block in extractor.results:
        posting = _find_job_posting(block)
        if posting:
            content = _parse_posting(posting)
            if not content.locations:
                content.locations = _extract_meta_locations(extractor.meta)
            return content

    return JobContent()


def can_handle(htmls: list[str]) -> dict | None:
    """Check if pages contain JSON-LD JobPosting. Returns ``{}`` if majority have it."""
    found = 0
    for html in htmls:
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        if any(_find_job_posting(block) for block in extractor.results):
            found += 1
    # Require at least half the pages to have JSON-LD
    if found > 0 and found >= len(htmls) / 2:
        return {}
    return None


async def _fetch_html(url: str, http: httpx.AsyncClient) -> str:
    """GET the page, retrying once on a 403 with jittered backoff.

    Some hosts (e.g. ``careers.rtx.com``) front their job pages with a soft
    WAF that rejects cold connections with 403 but accepts the retry on the
    same client — the first response sets challenge cookies that the retry
    carries. See the jsonld-retry-403 PR for the verification data.
    Any other non-2xx status still raises via ``raise_for_status``.
    """
    response = await http.get(url, follow_redirects=True)
    if response.status_code == 403 and _RETRY_403_MAX > 0:
        delay = _RETRY_403_BACKOFF_S + random.random() * _RETRY_403_BACKOFF_S
        log.info("jsonld.fetch.retry_403", url=url, delay_s=round(delay, 2))
        await asyncio.sleep(delay)
        response = await http.get(url, follow_redirects=True)
    response.raise_for_status()
    return response.text


async def scrape(url: str, config: dict, http: httpx.AsyncClient, pw=None, **kwargs) -> JobContent:
    """Extract job data from JSON-LD on a page.

    When ``render`` is true, renders the page with Playwright first.
    """
    if config.get("render"):
        from src.shared.browser import BROWSER_KEYS
        from src.shared.browser import render as browser_render

        browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}
        html = await browser_render(url, browser_config, pw=pw)
    else:
        html = await _fetch_html(url, http)

    content = parse_html(html, config)
    if content.title:
        log.debug("jsonld.extracted", url=url, title=content.title)
    else:
        log.warning("jsonld.not_found", url=url)
    return content


async def probe(url: str, http: httpx.AsyncClient) -> bool:
    """Check if a URL has JSON-LD JobPosting data. Used by validate --probe-jsonld."""
    try:
        response = await http.get(url, follow_redirects=True)
        if response.status_code != 200:
            return False
        return can_handle([response.text]) is not None
    except Exception:
        return False


register("json-ld", scrape, can_handle=can_handle, parse_html=parse_html)
