"""Structured job scraper for JSON-LD and job-specific HTML metadata.

Parses <script type="application/ld+json"> blocks for JobPosting structured data.
When a provider omits JSON-LD, falls back to explicit ``job-*`` meta tags used
by server-rendered career sites. No field mapping is needed.
"""

from __future__ import annotations

import html as html_module
import json
import re
from html.parser import HTMLParser

import httpx
import structlog

from src.core.enum_normalize import normalize_salary_unit
from src.core.scrapers import JobContent, register
from src.shared.api_sniff import clean_headers
from src.shared.http import is_avature_job_detail_url
from src.shared.http_retry import fetch_response_with_status_retries

log = structlog.get_logger()

# A single 403 on the HTTP path is usually a soft WAF signal: the first request
# from a cold session gets rate-limited, but the same client (now holding a
# challenge cookie) passes on the next attempt. Verified on careers.rtx.com:
# 50% cold-connection failure → 10/10 after a single retry on the same client.
# Small jittered sleep avoids hammering the WAF. Avature additionally uses
# transient 406 responses as an overload/throttle signal on live JobDetail
# pages (#5710), so those receive two bounded retries.

_CTRL_REPLACEMENTS = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
_TALEMETRY_MISSING_COMMA_RE = re.compile(
    r'("datePosted"\s*:\s*"(?:\\.|[^"\\])*")(\s*)("hiringOrganization"\s*:)',
    re.DOTALL,
)
_DOUBLE_ESCAPED_WHITESPACE_ENTITY_RE = re.compile(
    r"&amp;#(?:(?:x0*(?:9|a|d))|(?:0*(?:9|10|13)));",
    re.IGNORECASE,
)
_LOCATION_PLACEHOLDERS = frozenset({"unavailable", "not available", "n/a", "none", "null", "-"})

_CDATA_WRAPPERS = (
    ("//<![CDATA[", "//]]>"),
    ("/*<![CDATA[*/", "/*]]>*/"),
    ("<![CDATA[", "]]>"),
)


def _strip_cdata_wrapper(raw: str) -> str:
    """Remove legacy CDATA guards around a JSON-LD script body.

    HTML5 does not require CDATA around inline JSON, but older Rails-style
    templates still emit JavaScript comment guards.  The guards are not JSON
    and previously made otherwise valid JobPosting data disappear silently.
    """

    stripped = raw.strip()
    for prefix, suffix in _CDATA_WRAPPERS:
        if stripped.startswith(prefix) and stripped.endswith(suffix):
            return stripped[len(prefix) : -len(suffix)].strip()
    return stripped


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


def _repair_talemetry_missing_comma(raw: str) -> str:
    """Repair Talemetry's stable missing property comma.

    Talemetry Career Sites currently emit otherwise-valid JobPosting JSON-LD
    with no comma between ``datePosted`` and ``hiringOrganization``.  Keep the
    repair deliberately narrow so arbitrary malformed structured data still
    fails rather than being guessed into shape.
    """

    return _TALEMETRY_MISSING_COMMA_RE.sub(r"\1,\2\3", raw)


def _repair_talemetry_invalid_dollar_escape(raw: str) -> str:
    r"""Remove TTC's invalid ``\$`` JSON escape inside string values only."""
    out: list[str] = []
    in_string = False
    index = 0
    while index < len(raw):
        char = raw[index]
        if not in_string:
            out.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == '"':
            out.append(char)
            in_string = False
            index += 1
            continue
        if char == "\\" and index + 1 < len(raw):
            escaped = raw[index + 1]
            if escaped == "$":
                out.append("$")
            else:
                out.extend((char, escaped))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


class _JsonLdExtractor(HTMLParser):
    """Extracts JSON-LD blocks from HTML."""

    def __init__(self):
        super().__init__()
        self._in_jsonld = False
        self._in_title = False
        self._data: list[str] = []
        self._title_data: list[str] = []
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

        if tag == "script" and attr_dict.get("type") == "application/ld+json":
            self._in_jsonld = True
            self._data = []
        elif tag == "title":
            self._in_title = True
            self._title_data = []

    def handle_data(self, data):
        if self._in_jsonld:
            self._data.append(data)
        elif self._in_title:
            self._title_data.append(data)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            return
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            raw = _strip_cdata_wrapper("".join(self._data))
            if raw:
                # Prefer the standards-compliant raw block. If that fails,
                # support providers such as Gupy that HTML-entity-encode the
                # entire JSON document inside the script element.
                parsed_block = None
                for candidate in (raw, html_module.unescape(raw)):
                    repaired_comma = _repair_talemetry_missing_comma(candidate)
                    variants = dict.fromkeys(
                        (
                            candidate,
                            repaired_comma,
                            _repair_talemetry_invalid_dollar_escape(candidate),
                            _repair_talemetry_invalid_dollar_escape(repaired_comma),
                        )
                    )
                    for variant in variants:
                        try:
                            parsed_block = json.loads(variant)
                            break
                        except json.JSONDecodeError:
                            # Some sites emit literal control chars (newlines,
                            # tabs) inside JSON strings. Escape them and retry.
                            cleaned = _escape_control_chars_in_strings(variant)
                            try:
                                parsed_block = json.loads(cleaned)
                                break
                            except json.JSONDecodeError:
                                continue
                    if parsed_block is not None:
                        self.results.append(parsed_block)
                        break

    @property
    def page_title(self) -> str | None:
        return _clean_text("".join(self._title_data))


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


def _clean_text(value: object) -> str | None:
    """Decode HTML entities and normalize whitespace in scalar text fields."""
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", html_module.unescape(value)).strip()
    return text or None


def _extract_locations(
    posting: dict,
    *,
    ignore_address_region: bool = False,
) -> list[str] | None:
    """Extract locations from jobLocation field."""
    locations: list[str] = []
    seen: set[str] = set()
    job_location = posting.get("jobLocation")
    hiring_organization = posting.get("hiringOrganization")
    organization_name = (
        _clean_text(hiring_organization.get("name"))
        if isinstance(hiring_organization, dict)
        else None
    )

    if job_location is None:
        return None

    items = job_location if isinstance(job_location, list) else [job_location]

    for loc in items:
        if not isinstance(loc, dict):
            continue
        name = _clean_text(loc.get("name"))
        if name and name.casefold() in _LOCATION_PLACEHOLDERS:
            name = None
        address_text = None
        address = loc.get("address")
        if isinstance(address, str):
            address_text = _clean_text(address)
        elif isinstance(address, dict):
            parts = []
            address_fields = ["addressLocality"]
            if not ignore_address_region:
                address_fields.append("addressRegion")
            address_fields.append("addressCountry")
            for field in address_fields:
                val = address.get(field)
                if val:
                    if isinstance(val, dict):
                        val = val.get("name", "")
                    text = _clean_text(val)
                    if text and text.casefold() not in _LOCATION_PLACEHOLDERS:
                        parts.append(text)
            if parts:
                address_text = ", ".join(parts)

        # Some providers put the employer name in Place.name while publishing
        # the real location in Place.address. Prefer that structured address
        # only when the name is demonstrably the hiring organization; retain
        # the longstanding name-first behavior for legitimate venue names.
        name_is_organization = bool(
            name and organization_name and name.casefold() == organization_name.casefold()
        )
        location = address_text if address_text and name_is_organization else name or address_text
        if location and location not in seen:
            seen.add(location)
            locations.append(location)

    return locations or None


def _normalize_meta_locations(raw: str | None) -> list[str] | None:
    """Normalize TalentBrew/Radancy meta location values.

    Some TalentBrew job pages omit schema.org ``jobLocation`` while exposing
    the same location in tracking meta fields, usually as
    ``City~Region~Country`` values separated by ``;`` or ``|``.  Use this only
    as a fallback when JSON-LD has no location.
    """
    if not raw:
        return None

    locations: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"\s*[;|]\s*", raw):
        parts = [part.strip() for part in chunk.split("~") if part.strip()]
        text = ", ".join(parts) if parts else chunk.strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s*,\s*", ", ", text).strip(" ,")
        if text and text not in seen:
            seen.add(text)
            locations.append(text)

    return locations or None


def _extract_meta_locations(meta: dict[str, str]) -> list[str] | None:
    """Extract fallback locations from common career-site meta tags."""
    for key in ("gtm_tbcn_location", "dimension7"):
        locations = _normalize_meta_locations(meta.get(key))
        if locations:
            return locations

    primary_parts = [
        _clean_text(meta.get(key)) for key in ("job-city", "job-region", "job-country")
    ]
    primary = ", ".join(part for part in primary_parts if part)
    secondary = _normalize_meta_locations(meta.get("job-secondarylocations")) or []

    locations: list[str] = []
    for location in ([primary] if primary else []) + secondary:
        if location not in locations:
            locations.append(location)
    if locations:
        return locations
    return None


def _parse_meta_job(meta: dict[str, str]) -> JobContent | None:
    """Parse an explicit ``job-*`` metadata payload when JSON-LD is absent.

    Requiring both a title and a full description keeps this fallback scoped to
    real detail pages instead of accepting generic SEO tags that happen to use
    a similar name.
    """
    title = _clean_text(meta.get("job-title"))
    description = meta.get("job-description")
    if not title or not description or not _strip_html(description):
        return None

    metadata = {
        key: value
        for key, value in {
            "requisition_id": _clean_text(meta.get("job-id")),
            "job_function": _clean_text(meta.get("job-function")),
            "experience_level": _clean_text(meta.get("job-experiencelevel")),
        }.items()
        if value
    }
    return JobContent(
        title=title,
        description=description,
        locations=_extract_meta_locations(meta),
        job_location_type=_clean_text(meta.get("job-workingmode")),
        date_posted=_clean_text(meta.get("job-posteddate")),
        metadata=metadata or None,
    )


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


def _extract_employment_type(value: object) -> str | None:
    """Collapse schema.org's scalar-or-list employment type deterministically.

    Schedule values such as ``FULL_TIME`` are less specific than job-nature
    values such as ``INTERN`` or ``CONTRACTOR``. Prefer the latter when a
    provider publishes both; preserve the supported full/part combination for
    the central enum normalizer.
    """
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, list):
        return None

    values = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if not values:
        return None
    by_token = {re.sub(r"[\s-]+", "_", item).upper(): item for item in values}
    for token in ("INTERN", "TEMPORARY", "CONTRACTOR", "VOLUNTEER", "PER_DIEM"):
        if token in by_token:
            return by_token[token]
    if {"FULL_TIME", "PART_TIME"} <= by_token.keys():
        return "FULL_TIME, PART_TIME"
    for token in ("PART_TIME", "FULL_TIME", "OTHER"):
        if token in by_token:
            return by_token[token]
    return values[0]


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _normalize_description_entities(value: object) -> str | None:
    """Decode double-escaped whitespace references without altering HTML.

    Some TalentBrew tenants serialize line breaks in JSON-LD descriptions as
    ``&amp;#xa;``.  Those references survive JSON parsing and show up as noisy
    literal text.  Restrict the second decoding pass to tab/newline/carriage
    return references so ordinary encoded HTML and ampersands remain intact.
    """
    if not isinstance(value, str):
        return None
    return _DOUBLE_ESCAPED_WHITESPACE_ENTITY_RE.sub(
        lambda match: html_module.unescape(html_module.unescape(match.group(0))),
        value,
    )


def _parse_posting(
    posting: dict,
    *,
    ignore_address_region: bool = False,
) -> JobContent:
    """Convert a schema.org JobPosting dict to JobContent."""
    description = _normalize_description_entities(posting.get("description"))

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

    employment_type = _extract_employment_type(posting.get("employmentType"))

    return JobContent(
        title=_clean_text(posting.get("title") or posting.get("name")),
        description=description,
        locations=_extract_locations(
            posting,
            ignore_address_region=ignore_address_region,
        ),
        employment_type=employment_type,
        job_location_type=posting.get("jobLocationType"),
        date_posted=posting.get("datePosted"),
        base_salary=_extract_salary(posting),
        extras=extras or None,
    )


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Extract structured job data from pre-fetched HTML."""
    extractor = _JsonLdExtractor()
    extractor.feed(html)
    config = config or {}
    ignore_locations = config.get("ignore_locations") is True
    ignore_address_region = config.get("ignore_address_region") is True

    for block in extractor.results:
        posting = _find_job_posting(block)
        if posting:
            content = _parse_posting(
                posting,
                ignore_address_region=ignore_address_region,
            )
            # Some providers synthesize plausible-looking timestamps rather
            # than publishing real posting dates or expiry dates.  iCIMS, for
            # example, can emit the request time minus exactly two years as
            # datePosted and plus one year as validThrough for every job.  Keep
            # these opt-ins board-scoped: valid schema.org dates remain
            # authoritative everywhere else.
            if config.get("ignore_date_posted") is True:
                content.date_posted = None
            if config.get("ignore_valid_through") is True and content.extras:
                content.extras.pop("valid_through", None)
                if not content.extras:
                    content.extras = None
            if ignore_locations:
                content.locations = None
            if not content.title and extractor.page_title:
                organization = posting.get("hiringOrganization")
                organization_name = (
                    _clean_text(organization.get("name"))
                    if isinstance(organization, dict)
                    else None
                )
                if organization_name:
                    for separator in (" - ", " | ", " – "):
                        suffix = f"{separator}{organization_name}"
                        if extractor.page_title.casefold().endswith(suffix.casefold()):
                            content.title = extractor.page_title[: -len(suffix)].strip() or None
                            break
            if not content.locations and not ignore_locations:
                content.locations = _extract_meta_locations(extractor.meta)
            return content

    meta_content = _parse_meta_job(extractor.meta)
    if meta_content:
        if ignore_locations:
            meta_content.locations = None
        return meta_content
    return JobContent()


def contains_job_posting(
    html: str,
    *,
    hiring_organization_pattern: re.Pattern[str] | None = None,
) -> bool:
    """Return whether *html* contains an allowed schema.org ``JobPosting``.

    When *hiring_organization_pattern* is provided, the posting is retained
    only when ``hiringOrganization.name`` fully matches it. Missing or
    malformed organization data fails closed.
    """
    extractor = _JsonLdExtractor()
    extractor.feed(html)
    for block in extractor.results:
        posting = _find_job_posting(block)
        if posting is None:
            continue
        if hiring_organization_pattern is None:
            return True
        organization = posting.get("hiringOrganization")
        organization_name = (
            _clean_text(organization.get("name")) if isinstance(organization, dict) else None
        )
        if organization_name and hiring_organization_pattern.fullmatch(organization_name):
            return True
    return False


def can_handle(htmls: list[str]) -> dict | None:
    """Return ``{}`` when most pages contain supported structured job data."""
    found = 0
    for html in htmls:
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        if any(_find_job_posting(block) for block in extractor.results) or _parse_meta_job(
            extractor.meta
        ):
            found += 1
    # Require at least half the pages to expose supported structured job data.
    if found > 0 and found >= len(htmls) / 2:
        return {}
    return None


async def _fetch_html(
    url: str,
    http: httpx.AsyncClient,
    *,
    headers: dict[str, str] | None = None,
) -> str:
    """GET the page with bounded provider/status-aware retries.

    Some hosts (e.g. ``careers.rtx.com``) front their job pages with a soft
    WAF that rejects cold connections with 403 but accepts the retry on the
    same client — the first response sets challenge cookies that the retry
    carries. See the jsonld-retry-403 PR for the verification data.
    Avature JobDetail 406s receive two retries because production evidence
    shows the exact URLs recover without a config/header change. Any other
    non-2xx status still raises via ``raise_for_status``.
    """
    retry_limits = {403: 1}
    if is_avature_job_detail_url(url):
        retry_limits[406] = 2
    response = await fetch_response_with_status_retries(
        http,
        url,
        retry_limits=retry_limits,
        headers=headers,
        log_event="jsonld.fetch.retry_status",
    )
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
        request_headers = config.get("request_headers") or {}
        headers = clean_headers(request_headers)
        html = await _fetch_html(url, http, headers=headers or None)

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
