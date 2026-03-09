"""Breezy HR monitor.

Public endpoints observed across Breezy portals:
  List:   GET https://{portal}/json
  Detail: GET https://{portal}/p/{friendly_id}

The listing endpoint returns structured job metadata (title, URL, type,
locations, published date, company, salary text). Detail pages provide full
descriptions, usually via JSON-LD JobPosting and otherwise via the rendered
HTML description block.

This monitor supports:
- Direct Breezy portals ({slug}.breezy.hr)
- Custom pages that embed/link to a Breezy portal (Powered by Breezy widgets)
- Optional explicit override via monitor config: {"portal_url": "..."}
"""

from __future__ import annotations

import asyncio
import json
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register

log = structlog.get_logger()

MAX_JOBS = 10_000
CONCURRENCY = 10

_BREEZY_DOMAIN_RE = re.compile(r"^([\w-]+)\.breezy\.hr$")
_PORTAL_HOST_RE = re.compile(r"(?:https?:)?//([\w-]+\.breezy\.hr)", re.IGNORECASE)

_IGNORE_SLUGS = frozenset(
    {
        "www",
        "api",
        "app",
        "developer",
        "marketing",
        "assets-cdn",
        "attachments-cdn",
        "gallery-cdn",
    }
)

_PORTAL_MARKERS = (
    "breezy-portal",
    "powered by breezy",
    "assets-cdn.breezy.hr/breezy-portal",
    "app.breezy.hr/api/apply",
    ".breezy.hr",
)

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "fulltime": "Full-time",
    "full_time": "Full-time",
    "full-time": "Full-time",
    "parttime": "Part-time",
    "part_time": "Part-time",
    "part-time": "Part-time",
    "contract": "Contract",
    "contractor": "Contract",
    "temporary": "Temporary",
    "intern": "Intern",
    "internship": "Intern",
    "volunteer": "Volunteer",
}

_CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
}

_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?k?)", re.IGNORECASE)
_ISO_CURRENCY_RE = re.compile(r"\b(USD|EUR|GBP|AUD|CAD|CHF|JPY|SEK|NOK|DKK|INR)\b")


def _origin(url: str) -> str | None:
    """Normalize URL to scheme+host origin."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return None
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host.lower()}"


def _breezy_portal_from_host(host: str, scheme: str = "https") -> str | None:
    """Return Breezy portal origin for a valid *.breezy.hr host."""
    host_l = host.lower().strip(".")
    match = _BREEZY_DOMAIN_RE.match(host_l)
    if not match:
        return None
    slug = match.group(1)
    if slug in _IGNORE_SLUGS:
        return None
    return f"{scheme}://{host_l}"


def _breezy_portal_from_url(url: str) -> str | None:
    """Extract Breezy portal origin from URL host."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    scheme = parsed.scheme or "https"
    return _breezy_portal_from_host(host, scheme=scheme)


def _slug_from_portal(portal_url: str) -> str | None:
    """Extract Breezy slug from a portal origin."""
    host = (urlparse(portal_url).hostname or "").lower()
    match = _BREEZY_DOMAIN_RE.match(host)
    if not match:
        return None
    return match.group(1)


def _api_url(portal_url: str) -> str:
    return f"{portal_url.rstrip('/')}/json"


def _has_breezy_signal(url: str, html: str | None) -> bool:
    """Return True when URL or page HTML indicates Breezy portal usage."""
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".breezy.hr"):
        return True
    if not html:
        return False
    lowered = html.lower()
    return any(marker in lowered for marker in _PORTAL_MARKERS)


def _portal_candidates_from_html(html: str) -> list[str]:
    """Extract candidate Breezy portal origins from raw HTML."""
    seen: set[str] = set()
    candidates: list[str] = []
    for match in _PORTAL_HOST_RE.finditer(html):
        portal = _breezy_portal_from_host(match.group(1))
        if portal and portal not in seen:
            seen.add(portal)
            candidates.append(portal)
    return candidates


def _extract_location_name(raw: dict | str | None) -> str | None:
    """Extract a human-readable location name from Breezy location objects."""
    if isinstance(raw, str):
        value = raw.strip()
        return value or None
    if not isinstance(raw, dict):
        return None

    name = raw.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()

    city = raw.get("city")
    state = raw.get("state")
    country = raw.get("country")

    state_name = state.get("name") if isinstance(state, dict) else state
    country_name = None
    if isinstance(country, dict):
        country_name = country.get("name") or country.get("id")
    elif isinstance(country, str):
        country_name = country

    parts = [p for p in (city, state_name, country_name) if isinstance(p, str) and p]
    if parts:
        return ", ".join(parts)
    return None


def _parse_locations(opening: dict) -> list[str] | None:
    """Extract and deduplicate locations from Breezy listing payload."""
    locations: list[str] = []
    seen: set[str] = set()

    raw_locations = opening.get("locations")
    if isinstance(raw_locations, list):
        for loc in raw_locations:
            parsed = _extract_location_name(loc)
            if parsed and parsed not in seen:
                seen.add(parsed)
                locations.append(parsed)

    if not locations:
        fallback = _extract_location_name(opening.get("location"))
        if fallback and fallback not in seen:
            locations.append(fallback)

    return locations or None


def _parse_job_location_type(opening: dict) -> str | None:
    """Infer job_location_type from Breezy location fields."""

    def _is_remote(loc: dict) -> bool:
        return bool(loc.get("is_remote"))

    locations = opening.get("locations")
    if isinstance(locations, list) and any(
        isinstance(loc, dict) and _is_remote(loc) for loc in locations
    ):
        return "remote"

    location = opening.get("location")
    if isinstance(location, dict) and _is_remote(location):
        return "remote"

    return None


def _normalize_employment_type(value: str | None) -> str | None:
    """Normalize employment type labels/codes."""
    if not value:
        return None
    key = value.strip().lower().replace(" ", "_")
    return _EMPLOYMENT_TYPE_MAP.get(key, value)


def _to_amount(token: str) -> float | int | None:
    """Parse salary token into numeric value (supports k-suffix)."""
    cleaned = token.strip().lower().replace(",", "")
    multiplier = 1
    if cleaned.endswith("k"):
        cleaned = cleaned[:-1]
        multiplier = 1000
    try:
        value = float(cleaned) * multiplier
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _parse_salary_text(raw: str | None) -> dict | None:
    """Parse salary text like '$75 - $95 / hr' into structured salary."""
    if not raw or not isinstance(raw, str):
        return None

    numbers = [_to_amount(t) for t in _AMOUNT_RE.findall(raw)]
    amounts = [n for n in numbers if n is not None]
    if not amounts:
        return None

    minimum = amounts[0]
    maximum = amounts[1] if len(amounts) > 1 else None

    currency = None
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in raw:
            currency = code
            break
    if currency is None:
        iso = _ISO_CURRENCY_RE.search(raw.upper())
        if iso:
            currency = iso.group(1)

    lowered = raw.lower()
    unit = "year"
    if re.search(r"/\s*(hr|hour)\b", lowered):
        unit = "hour"
    elif re.search(r"/\s*(month|mo)\b", lowered):
        unit = "month"
    elif re.search(r"/\s*(week|wk)\b", lowered):
        unit = "week"

    return {"currency": currency, "min": minimum, "max": maximum, "unit": unit}


def _opening_url(opening: dict, portal_url: str) -> str | None:
    """Build absolute opening URL from listing item."""
    raw = opening.get("url")
    if isinstance(raw, str) and raw.strip():
        return urljoin(f"{portal_url.rstrip('/')}/", raw.strip())
    friendly_id = opening.get("friendly_id")
    if isinstance(friendly_id, str) and friendly_id.strip():
        return f"{portal_url.rstrip('/')}/p/{friendly_id.strip()}"
    return None


def _parse_opening(opening: dict, portal_url: str) -> DiscoveredJob | None:
    """Map Breezy listing item to DiscoveredJob base fields."""
    url = _opening_url(opening, portal_url)
    if not url:
        return None

    raw_type = opening.get("type")
    employment_type = None
    if isinstance(raw_type, dict):
        employment_type = _normalize_employment_type(
            raw_type.get("name")
        ) or _normalize_employment_type(raw_type.get("id"))
    elif isinstance(raw_type, str):
        employment_type = _normalize_employment_type(raw_type)

    salary_text = opening.get("salary") if isinstance(opening.get("salary"), str) else None

    metadata: dict = {}
    department = opening.get("department")
    if isinstance(department, str) and department:
        metadata["department"] = department
    company = opening.get("company")
    if isinstance(company, dict):
        company_name = company.get("name")
        company_slug = company.get("friendly_id")
        if company_name:
            metadata["company"] = company_name
        if company_slug:
            metadata["company_slug"] = company_slug
    opening_id = opening.get("id")
    if opening_id:
        metadata["id"] = opening_id
    if salary_text:
        metadata["salary_text"] = salary_text

    return DiscoveredJob(
        url=url,
        title=opening.get("name"),
        locations=_parse_locations(opening),
        employment_type=employment_type,
        job_location_type=_parse_job_location_type(opening),
        date_posted=opening.get("published_date"),
        base_salary=_parse_salary_text(salary_text),
        metadata=metadata or None,
    )


def _render_start_tag(
    tag: str,
    attrs: list[tuple[str, str | None]],
    self_close: bool = False,
) -> str:
    """Render a start tag back to HTML while capturing description blocks."""
    bits = [tag]
    for key, value in attrs:
        if value is None:
            bits.append(key)
        else:
            bits.append(f'{key}="{escape(value, quote=True)}"')
    if self_close:
        return "<" + " ".join(bits) + " />"
    return "<" + " ".join(bits) + ">"


def _is_description_div(attrs: list[tuple[str, str | None]]) -> bool:
    """Return True when attrs correspond to a description content block."""
    attr_map = {k: (v or "") for k, v in attrs}
    classes = {cls for cls in attr_map.get("class", "").split() if cls}
    return "description" in classes


class _DetailExtractor(HTMLParser):
    """Extract JSON-LD blocks and HTML description blocks from detail pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []
        self.jsonld_blocks: list[str] = []

        self._capture_depth = 0
        self._capture_buf: list[str] = []
        self.description_blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        if tag_l == "script":
            attr_map = {k: (v or "") for k, v in attrs}
            if attr_map.get("type", "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = []

        if self._capture_depth > 0:
            self._capture_buf.append(_render_start_tag(tag, attrs))
            self._capture_depth += 1
            return

        if tag_l == "div" and _is_description_div(attrs):
            self._capture_depth = 1
            self._capture_buf = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._capture_depth > 0:
            self._capture_buf.append(_render_start_tag(tag, attrs, self_close=True))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld_blocks.append("".join(self._jsonld_buf))
            self._jsonld_buf = []

        if self._capture_depth == 0:
            return

        self._capture_depth -= 1
        if self._capture_depth == 0:
            block = "".join(self._capture_buf).strip()
            if block:
                self.description_blocks.append(block)
            self._capture_buf = []
        else:
            self._capture_buf.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        if self._capture_depth > 0:
            self._capture_buf.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._capture_depth > 0:
            self._capture_buf.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._capture_depth > 0:
            self._capture_buf.append(f"&#{name};")


def _is_jobposting_type(value) -> bool:
    """Return True for @type JobPosting (string or list)."""
    if isinstance(value, str):
        return value.lower() == "jobposting"
    if isinstance(value, list):
        return any(isinstance(v, str) and v.lower() == "jobposting" for v in value)
    return False


def _find_job_posting(jsonld_blocks: list[str]) -> dict | None:
    """Find JobPosting object in JSON-LD blocks, including @graph and lists."""
    for raw in jsonld_blocks:
        try:
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            continue

        stack = [data]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if _is_jobposting_type(current.get("@type")):
                    return current
                graph = current.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(current, list):
                stack.extend(current)

    return None


def _jsonld_locations(posting: dict) -> list[str] | None:
    """Extract locations from JobPosting JSON-LD."""
    raw = posting.get("jobLocation")
    if not raw:
        return None
    candidates = raw if isinstance(raw, list) else [raw]
    locations: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        address = candidate.get("address")
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ]
            text = ", ".join(str(p) for p in parts if p)
            if text and text not in seen:
                seen.add(text)
                locations.append(text)
                continue
        name = candidate.get("name")
        if isinstance(name, str) and name and name not in seen:
            seen.add(name)
            locations.append(name)
    return locations or None


def _jsonld_job_location_type(posting: dict) -> str | None:
    """Extract job_location_type from JobPosting JSON-LD."""
    raw = posting.get("jobLocationType")
    if raw is None:
        return None

    values: list[str] = []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [v for v in raw if isinstance(v, str)]

    normalized = " ".join(v.upper() for v in values)
    if "TELECOMMUTE" in normalized or "REMOTE" in normalized:
        return "remote"
    if "HYBRID" in normalized:
        return "hybrid"
    if "ONSITE" in normalized or "ON_SITE" in normalized or "ON-SITE" in normalized:
        return "onsite"
    return None


def _jsonld_salary(posting: dict) -> dict | None:
    """Extract base_salary from JobPosting JSON-LD."""
    base_salary = posting.get("baseSalary")
    if not isinstance(base_salary, dict):
        return None
    value = base_salary.get("value")
    if not isinstance(value, dict):
        return None

    minimum = value.get("minValue")
    maximum = value.get("maxValue")
    if minimum is None and maximum is None:
        return None

    unit_raw = str(value.get("unitText") or "").upper()
    unit = "year"
    if "HOUR" in unit_raw:
        unit = "hour"
    elif "MONTH" in unit_raw:
        unit = "month"
    elif "WEEK" in unit_raw:
        unit = "week"

    return {
        "currency": base_salary.get("currency"),
        "min": minimum,
        "max": maximum,
        "unit": unit,
    }


def _parse_detail(html: str) -> dict:
    """Parse Breezy detail page and return discovered detail fields."""
    extractor = _DetailExtractor()
    extractor.feed(html)

    detail: dict = {}
    posting = _find_job_posting(extractor.jsonld_blocks)
    if posting:
        description = posting.get("description")
        if isinstance(description, str) and description.strip():
            detail["description"] = description
        locations = _jsonld_locations(posting)
        if locations:
            detail["locations"] = locations
        employment = posting.get("employmentType")
        if isinstance(employment, list):
            for item in employment:
                normalized = _normalize_employment_type(item if isinstance(item, str) else None)
                if normalized:
                    detail["employment_type"] = normalized
                    break
        elif isinstance(employment, str):
            normalized = _normalize_employment_type(employment)
            if normalized:
                detail["employment_type"] = normalized
        job_loc_type = _jsonld_job_location_type(posting)
        if job_loc_type:
            detail["job_location_type"] = job_loc_type
        date_posted = posting.get("datePosted")
        if isinstance(date_posted, str) and date_posted:
            detail["date_posted"] = date_posted
        salary = _jsonld_salary(posting)
        if salary:
            detail["base_salary"] = salary

    if "description" not in detail:
        blocks = [blk.strip() for blk in extractor.description_blocks if blk and blk.strip()]
        if blocks:
            detail["description"] = max(blocks, key=len)

    return detail


async def _fetch_detail(
    url: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> tuple[str, dict]:
    """Fetch and parse a single detail page."""
    async with semaphore:
        try:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                log.warning("breezy.detail_failed", url=url, status=resp.status_code)
                return url, {}
            return url, _parse_detail(resp.text)
        except Exception as exc:
            log.warning("breezy.detail_error", url=url, error=str(exc))
            return url, {}


async def _probe_portal(portal_url: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Validate Breezy listing endpoint for a candidate portal."""
    try:
        resp = await client.get(_api_url(portal_url), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        if not isinstance(data, list):
            return False, None
        if data:
            first = data[0]
            if not isinstance(first, dict):
                return False, None
            if "id" not in first or "url" not in first:
                return False, None
        return True, len(data)
    except Exception:
        return False, None


async def _fetch_openings(portal_url: str, client: httpx.AsyncClient) -> list[dict]:
    """Fetch openings list from Breezy listing endpoint."""
    resp = await client.get(_api_url(portal_url), follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Breezy endpoint {_api_url(portal_url)!r} did not return a JSON list")
    return [item for item in data if isinstance(item, dict)]


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from Breezy listing endpoint plus detail pages."""
    metadata = board.get("metadata") or {}

    portal_url = None
    if isinstance(metadata.get("portal_url"), str):
        portal_url = _origin(metadata["portal_url"])
    if portal_url is None:
        portal_url = _breezy_portal_from_url(board["board_url"])
    if portal_url is None and isinstance(metadata.get("slug"), str):
        slug = metadata["slug"].strip()
        if slug:
            portal_url = f"https://{slug}.breezy.hr"

    if not portal_url:
        raise ValueError(
            f"Cannot derive Breezy portal URL from board URL {board['board_url']!r} "
            "and no portal_url/slug in metadata"
        )

    openings = await _fetch_openings(portal_url, client)
    if len(openings) > MAX_JOBS:
        log.warning("breezy.truncated", portal=portal_url, total=len(openings), cap=MAX_JOBS)
        openings = openings[:MAX_JOBS]

    base_jobs: list[DiscoveredJob] = []
    for opening in openings:
        parsed = _parse_opening(opening, portal_url)
        if parsed:
            base_jobs.append(parsed)

    if not base_jobs:
        return []

    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [_fetch_detail(job.url, client, semaphore) for job in base_jobs]
    details = dict(await asyncio.gather(*tasks))

    for job in base_jobs:
        detail = details.get(job.url) or {}
        description = detail.get("description")
        if isinstance(description, str) and description.strip():
            job.description = description
        if detail.get("locations"):
            job.locations = detail["locations"]
        if detail.get("employment_type"):
            job.employment_type = detail["employment_type"]
        if detail.get("job_location_type"):
            job.job_location_type = detail["job_location_type"]
        if detail.get("date_posted"):
            job.date_posted = detail["date_posted"]
        if detail.get("base_salary"):
            job.base_salary = detail["base_salary"]

    return base_jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Breezy boards via URL, redirect target, embedded links, and /json validation."""
    portal = _breezy_portal_from_url(url)
    if portal:
        slug = _slug_from_portal(portal)
        if client is None:
            result: dict = {"portal_url": portal}
            if slug:
                result["slug"] = slug
            return result
        found, count = await _probe_portal(portal, client)
        if found:
            result = {"portal_url": portal}
            if slug:
                result["slug"] = slug
            if count is not None:
                result["jobs"] = count
            return result
        return None

    if client is None:
        return None

    final_url = url
    html: str | None = None
    try:
        resp = await client.get(url, follow_redirects=True)
        final_url = str(resp.url)
        if resp.status_code == 200:
            html = resp.text
    except Exception:
        pass

    # 1) Redirect target is a Breezy portal
    redirected_portal = _breezy_portal_from_url(final_url)
    if redirected_portal:
        found, count = await _probe_portal(redirected_portal, client)
        if found:
            result = {"portal_url": redirected_portal}
            slug = _slug_from_portal(redirected_portal)
            if slug:
                result["slug"] = slug
            if count is not None:
                result["jobs"] = count
            return result

    # 2) Page embeds/links a Breezy portal
    if html:
        for candidate in _portal_candidates_from_html(html):
            found, count = await _probe_portal(candidate, client)
            if found:
                log.info("breezy.detected_in_page", url=url, portal_url=candidate)
                result = {"portal_url": candidate}
                slug = _slug_from_portal(candidate)
                if slug:
                    result["slug"] = slug
                if count is not None:
                    result["jobs"] = count
                return result

    # 3) CNAME-style custom domain Breezy portal (same-origin /json)
    if _has_breezy_signal(final_url, html):
        custom_origin = _origin(final_url)
        if custom_origin:
            found, count = await _probe_portal(custom_origin, client)
            if found:
                result = {"portal_url": custom_origin}
                if count is not None:
                    result["jobs"] = count
                return result

    return None


register("breezy", discover, cost=10, can_handle=can_handle, rich=True)
