"""Generic RSS 2.0 feed monitor with ATS presets.

Supports multiple ATS platforms that expose job listings via RSS/XML-style transports:
- **successfactors**: SAP SuccessFactors CSB ``/googlefeed.xml`` (Google Base namespace)
  plus native static DWR pagination for legacy ``/career?company=...`` tenants
- **teamtailor**: Teamtailor ``/jobs.rss`` (offset-paginated, ``tt:`` namespace)
- **wp_job_manager**: WordPress WP Job Manager ``?feed=job_feed`` (page-paginated)
- **generic**: Standard RSS 2.0 (manual config, not auto-detected)

Config: ``{"preset": "<name>", "feed_url": "..."}``. Legacy SuccessFactors
uses ``{"preset": "successfactors", "variant": "legacy", "host": "...",
"company": "..."}`` and still runs through this monitor type.
"""

from __future__ import annotations

import asyncio
import html
import random
import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.core.monitors.dom import BotChallengeError, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import (
    PaginationFetchError,
    fetch_text_page_with_retry,
    is_retryable_status,
)
from src.shared.successfactors import (
    SuccessFactorsLegacyBoard,
    normalize_successfactors_company,
    successfactors_legacy_board_from_url,
)
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

MAX_JOBS = 50_000
_STREAM_BATCH = 200
_HTTP_CHUNK_BYTES = 64 * 1024
_SNIFF_BYTES = 512
_SF_DETAIL_CONCURRENCY = 16
_SF_DETAIL_ATTEMPTS = 3
_SF_DETAIL_MAX_CHARS = 5_000_000
_DETECTION_MAX_CHARS = 2_000_000
_SF_DETAIL_MAX_REDIRECTS = 3
_SF_DETAIL_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SF_DETAIL_RETRYABLE_STATUSES = frozenset({401, 403, 429})
_SF_JOB_IDENTITY_MAX_JOBS = 2_000
_SF_JOB_IDENTITY_RE = re.compile(
    r"/job/[^/?#]+/(?P<job_id>[1-9]\d{0,11})-"
    r"(?P<locale>[a-z]{2}_[A-Z]{2})/"
)
_SF_JOB_SELECTOR = '[data-careersite-propertyid="title"]'
_SF_DETAIL_FIELD_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SF_METADATA_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SF_WRAPPER_QUERY_KEYS = frozenset(
    {
        "_s.crb",
        "career_company",
        "career_ns",
        "company",
        "lang",
        "loginFlowRequired",
        "navBarLevel",
        "rcm_site_locale",
        "site",
    }
)


async def _sleep(delay: float) -> None:
    """Patchable retry sleep used by RSS stream tests."""
    await asyncio.sleep(delay)


class RssFeedNotXml(ValueError):
    """Feed endpoint returned a non-XML body (e.g. publisher disabled the feed)."""


class SuccessFactorsDetailPageError(RuntimeError):
    """A detail endpoint repeatedly returned a non-job HTTP-200 document."""

    def __init__(self, url: str, *, classification: str, attempts: int) -> None:
        self.url = url
        self.classification = classification
        self.attempts = attempts
        super().__init__(
            "SuccessFactors company enrichment got invalid page "
            f"(classification={classification}, attempts={attempts}): {url}"
        )


class SuccessFactorsJobIdentityError(RuntimeError):
    """A configured SuccessFactors feed could not prove stable job identity."""


def _parse_feed(text: str, feed_url: str) -> ET.Element:
    """Parse an RSS response body, with a clear error for non-XML content.

    Some publishers retire their feed endpoint but keep the URL live, serving
    an HTML landing page or a plain-text "feed disabled" message with 200 OK.
    ``ET.fromstring`` then surfaces a cryptic ``not well-formed`` error that
    gives no clue about the actual cause. Sniff the leading bytes and raise a
    named error up-front so the monitor's ``last_error`` identifies the root
    cause instead of a column offset in a JavaScript blob.
    """
    head = text.lstrip()[:512].lower()
    if not head.startswith(("<?xml", "<rss", "<feed")):
        raise RssFeedNotXml(f"feed returned non-XML content: {feed_url}")
    return ET.fromstring(text)


# ── Preset definitions ──────────────────────────────────────────────────


@dataclass(frozen=True)
class _Preset:
    feed_paths: list[str]
    page_patterns: list[re.Pattern]
    feed_ns: dict[str, str]
    paginated: bool = False
    page_size: int = 100
    page_query_param: str | None = None
    retryable_statuses: frozenset[int] = frozenset()


_PRESETS: dict[str, _Preset] = {
    "successfactors": _Preset(
        feed_paths=["/googlefeed.xml"],
        page_patterns=[
            re.compile(r"successfactors\.(?:eu|com)"),
            re.compile(r"sapsf\.(?:cn|eu|com)"),
            re.compile(r"jobs\.hr\.cloud\.sap"),
            re.compile(r"rmkcdn\.successfactors\.com"),
            re.compile(r"jobs2web\.com"),
        ],
        feed_ns={"g": "http://base.google.com/ns/1.0"},
    ),
    "teamtailor": _Preset(
        feed_paths=["/jobs.rss"],
        page_patterns=[
            re.compile(r"teamtailor-cdn\.com"),
        ],
        feed_ns={"tt": "https://teamtailor.com/locations"},
        paginated=True,
        page_size=100,
        # Teamtailor occasionally emits a transient 400 from an otherwise
        # healthy feed. Keep this provider-specific: a generic HTTP 400 is a
        # permanent request error and must still fail fast.
        retryable_statuses=frozenset({400}),
    ),
    "wp_job_manager": _Preset(
        feed_paths=["/?feed=job_feed"],
        page_patterns=[
            re.compile(r"/wp-content/plugins/wp-job-manager/", re.IGNORECASE),
            re.compile(r"\bjob_manager_ajax_filters\b", re.IGNORECASE),
        ],
        feed_ns={},
        paginated=True,
        page_size=10,
        page_query_param="paged",
    ),
}

# Teamtailor namespace — used in item parsing
_TT_NS = "https://teamtailor.com/locations"
# Google Base namespace — used in item parsing
_G_NS = "http://base.google.com/ns/1.0"

# Location suffix in title, e.g. " (Tempe, AZ, US, 85288)"
_TITLE_LOCATION_RE = re.compile(r"\s*\([^)]+,\s*[^)]+\)\s*$")
_TITLE_LOCATION_VALUE_RE = re.compile(r"\s*\((?P<location>[^()]+,\s*[^()]+)\)\s*$")
_DESCRIPTION_LOCATION_RE = re.compile(
    r"<(?:strong|b)\b[^>]*>\s*Location\s*:?\s*</(?:strong|b)>\s*([^<]+)",
    re.IGNORECASE,
)


# ── Item parsers ────────────────────────────────────────────────────────


def _text(item: ET.Element, tag: str) -> str | None:
    """Get text content of a child element."""
    child = item.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _g(item: ET.Element, tag: str) -> str | None:
    """Get text from a Google Base namespace child element."""
    child = item.find(f"{{{_G_NS}}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _tt(item: ET.Element, tag: str) -> str | None:
    """Get text from a Teamtailor namespace child element."""
    child = item.find(f"{{{_TT_NS}}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _tt_all(item: ET.Element, tag: str) -> list[str]:
    """Get all text values from repeated Teamtailor namespace elements."""
    results = []
    for child in item.findall(f"{{{_TT_NS}}}{tag}"):
        if child.text and child.text.strip():
            results.append(child.text.strip())
    return results


def _sf_description_location(description: str) -> str | None:
    """Read a labelled location from a SuccessFactors HTML description."""
    match = _DESCRIPTION_LOCATION_RE.search(description)
    if not match:
        return None
    location = " ".join(html.unescape(match.group(1)).split())
    return location or None


def _parse_sf_item(item: ET.Element) -> DiscoveredJob | None:
    """Parse a SuccessFactors RSS item (Google Base namespace)."""
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = html.unescape(raw_desc) if raw_desc else None
    # Some SuccessFactors tenants populate the Google feed's description
    # with the job title only. Treat that placeholder as missing so the
    # configuration tooling can require detail-page enrichment instead of
    # incorrectly classifying the feed as self-contained rich data.
    if description and title:
        plain_description = re.sub(r"<[^>]+>", " ", description)
        plain_description = " ".join(plain_description.split()).casefold()
        plain_title = " ".join(html.unescape(title).split()).casefold()
        if plain_description == plain_title:
            description = None

    location = _g(item, "location")
    strip_title_location = location is not None
    if not location and description:
        description_location = _sf_description_location(description)
        if description_location:
            location = description_location
            title_location = _TITLE_LOCATION_VALUE_RE.search(title or "")
            if title_location:
                candidate = " ".join(title_location.group("location").split())
                description_key = description_location.casefold()
                candidate_key = candidate.casefold()
                if candidate_key == description_key or candidate_key.startswith(
                    f"{description_key},"
                ):
                    location = candidate
                    strip_title_location = True
    locations = [location] if location else None

    if title and strip_title_location:
        cleaned = _TITLE_LOCATION_RE.sub("", title)
        if cleaned:
            title = cleaned

    job_id = _text(item, "guid")
    date_posted = _text(item, "pubDate")
    expiration_date = _g(item, "expiration_date")
    employer = _g(item, "employer")
    job_function = _g(item, "job_function")

    metadata: dict = {}
    if job_id:
        metadata["id"] = job_id
    if employer:
        metadata["employer"] = employer
    if job_function and job_function != "ATS_WEBFORM":
        metadata["job_function"] = job_function
    if expiration_date:
        metadata["expiration_date"] = expiration_date

    return DiscoveredJob(
        url=link,
        title=title,
        description=description,
        locations=locations,
        date_posted=date_posted,
        metadata=metadata or None,
    )


def _tt_location_string(loc_el: ET.Element) -> str | None:
    """Build a location string from a tt:location element.

    Prefers tt:name when populated, falls back to "city, country".
    """
    name_el = loc_el.find(f"{{{_TT_NS}}}name")
    name = name_el.text.strip() if name_el is not None and name_el.text else ""
    if name:
        return name

    city_el = loc_el.find(f"{{{_TT_NS}}}city")
    country_el = loc_el.find(f"{{{_TT_NS}}}country")
    city = city_el.text.strip() if city_el is not None and city_el.text else ""
    country = country_el.text.strip() if country_el is not None and country_el.text else ""

    if city and country:
        return f"{city}, {country}"
    return city or country or None


def _parse_tt_item(item: ET.Element) -> DiscoveredJob | None:
    """Parse a Teamtailor RSS item (tt: namespace)."""
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = html.unescape(raw_desc) if raw_desc else None

    # Structured locations: tt:locations > tt:location > (tt:name | tt:city, tt:country)
    locations: list[str] = []
    locations_el = item.find(f"{{{_TT_NS}}}locations")
    if locations_el is not None:
        for loc_el in locations_el.findall(f"{{{_TT_NS}}}location"):
            loc_str = _tt_location_string(loc_el)
            if loc_str:
                locations.append(loc_str)

    # remoteStatus is a plain element (not namespaced)
    remote_status = _text(item, "remoteStatus")
    job_location_type: str | None = None
    if remote_status:
        lower = remote_status.lower()
        if "fully" in lower or lower == "remote":
            job_location_type = "remote"
        elif "hybrid" in lower:
            job_location_type = "hybrid"
        elif lower in ("none", "onsite", "on-site"):
            job_location_type = "onsite"

    # Teamtailor permits fully remote postings without assigning a physical
    # location. Preserve explicit structured locations when present, but use
    # the provider's remote status as the location fallback so these jobs do
    # not lose the required location field.
    if not locations and job_location_type == "remote":
        locations.append("Remote")

    date_posted = _text(item, "pubDate")
    department = _tt(item, "department")
    role = _tt(item, "role")
    guid = _text(item, "guid")

    metadata: dict = {}
    if guid:
        metadata["id"] = guid
    if department:
        metadata["department"] = department
    if role:
        metadata["role"] = role

    return DiscoveredJob(
        url=link,
        title=title,
        description=description,
        locations=locations or None,
        job_location_type=job_location_type,
        date_posted=date_posted,
        metadata=metadata or None,
    )


def _parse_generic_item(item: ET.Element) -> DiscoveredJob | None:
    """Parse a standard RSS 2.0 item."""
    link = _text(item, "link")
    title = _text(item, "title")
    if not link:
        return None

    raw_desc = _text(item, "description")
    description = html.unescape(raw_desc) if raw_desc else None
    date_posted = _text(item, "pubDate")
    guid = _text(item, "guid") or _text(item, "JobID")
    location = _text(item, "location") or _text(item, "Location")

    metadata: dict = {}
    if guid:
        metadata["id"] = guid

    return DiscoveredJob(
        url=link,
        title=title,
        description=description,
        locations=[location] if location else None,
        date_posted=date_posted,
        metadata=metadata or None,
    )


_PARSERS: dict[str, Callable[[ET.Element], DiscoveredJob | None]] = {
    "successfactors": _parse_sf_item,
    "teamtailor": _parse_tt_item,
    "generic": _parse_generic_item,
}


def _sf_detail_candidates(jobs: list[DiscoveredJob], raw_url_filter: object) -> list[DiscoveredJob]:
    """Apply dispatcher URL-filter semantics before making detail requests.

    The dispatcher still performs the authoritative filtering after discovery.
    This early pass only prevents each regional board from fetching every job
    in a shared tenant before irrelevant URLs are removed.
    """
    if not raw_url_filter:
        return jobs
    if isinstance(raw_url_filter, str):
        include, exclude = raw_url_filter, None
    elif isinstance(raw_url_filter, Mapping):
        include = raw_url_filter.get("include")
        exclude = raw_url_filter.get("exclude")
    else:
        return jobs
    try:
        include_re = re.compile(include) if include else None
        exclude_re = re.compile(exclude) if exclude else None
    except re.error:
        return jobs
    return [
        job
        for job in jobs
        if (include_re is None or include_re.search(job.url))
        and (exclude_re is None or not exclude_re.search(job.url))
    ]


def _same_https_origin(url: str, origin: str) -> bool:
    """Restrict optional detail enrichment to the configured feed origin."""
    try:
        candidate = urlparse(url)
        source = urlparse(origin)
        candidate_port = candidate.port
        source_port = source.port
    except (TypeError, ValueError):
        return False
    return (
        candidate.scheme.casefold() == source.scheme.casefold() == "https"
        and candidate.hostname is not None
        and source.hostname is not None
        and candidate.hostname.casefold().rstrip(".") == source.hostname.casefold().rstrip(".")
        and candidate_port in {None, 443}
        and source_port in {None, 443}
        and candidate.username is None
        and candidate.password is None
    )


def _classify_sf_detail_page(url: str, page: str) -> tuple[LexborHTMLParser, str | None]:
    document = LexborHTMLParser(page)
    if document.css_first(_SF_JOB_SELECTOR) is not None:
        return document, None
    try:
        _raise_if_bot_challenge(url, page)
    except BotChallengeError:
        return document, "bot_challenge"
    if not page.lstrip().startswith("<"):
        return document, "non_html"
    return document, "missing_job_marker"


async def _fetch_sf_detail_document(
    url: str,
    client: httpx.AsyncClient,
) -> LexborHTMLParser:
    """Fetch and validate a SuccessFactors detail page with bounded retries."""
    classification = "missing_job_marker"
    current_url = url
    redirects = 0
    visited = {url}
    for attempt in range(_SF_DETAIL_ATTEMPTS):
        while True:
            try:
                page = await fetch_text_page_with_retry(
                    client,
                    current_url,
                    follow_redirects=False,
                    retryable_statuses=_SF_DETAIL_RETRYABLE_STATUSES,
                    end_of_pagination_statuses=(),
                    require_nonempty=True,
                    max_chars=_SF_DETAIL_MAX_CHARS + 1,
                    max_bytes=_SF_DETAIL_MAX_CHARS,
                    log_event="rss.successfactors_detail_backoff",
                    sleep=_sleep,
                )
            except PaginationFetchError as exc:
                if exc.last_status not in _SF_DETAIL_REDIRECT_STATUSES or not exc.last_location:
                    raise
                redirected = urljoin(current_url, exc.last_location)
                if not _same_https_origin(redirected, url):
                    raise RuntimeError(
                        f"SuccessFactors company enrichment rejected redirect: {redirected}"
                    ) from exc
                if redirects >= _SF_DETAIL_MAX_REDIRECTS or redirected in visited:
                    raise RuntimeError(
                        f"SuccessFactors company enrichment exceeded redirect cap: {url}"
                    ) from exc
                redirects += 1
                visited.add(redirected)
                current_url = redirected
                continue
            break
        if page is None:  # Strict terminal status handling makes this unreachable.
            raise RuntimeError(f"SuccessFactors detail fetch returned no page: {current_url}")

        document, invalid = _classify_sf_detail_page(current_url, page)
        if invalid is None:
            return document
        classification = invalid
        if attempt < _SF_DETAIL_ATTEMPTS - 1:
            delay = 0.5 * (2**attempt) * (0.5 + random.random())
            log.info(
                "rss.successfactors_detail_invalid_backoff",
                url=url,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                classification=classification,
            )
            await _sleep(delay)

    raise SuccessFactorsDetailPageError(
        url,
        classification=classification,
        attempts=_SF_DETAIL_ATTEMPTS,
    )


def _sf_detail_fields(metadata: Mapping) -> tuple[dict[str, str], frozenset[str]]:
    """Return validated detail-property mappings and required metadata keys."""
    fields: dict[str, str] = {}
    required: set[str] = set()
    if metadata.get("fetch_company"):
        # Backward compatibility: the historic customfield1 enrichment was
        # optional when a tenant omitted that property.
        fields["company"] = "customfield1"

    configured = metadata.get("detail_fields")
    if configured is None:
        return fields, frozenset(required)
    if not isinstance(configured, Mapping) or len(configured) > 16:
        raise ValueError("SuccessFactors detail_fields must be a mapping of at most 16 fields")
    for metadata_key, property_id in configured.items():
        if not isinstance(metadata_key, str) or not _SF_METADATA_KEY_RE.fullmatch(metadata_key):
            raise ValueError(f"Invalid SuccessFactors metadata key: {metadata_key!r}")
        if not isinstance(property_id, str) or not _SF_DETAIL_FIELD_RE.fullmatch(property_id):
            raise ValueError(f"Invalid SuccessFactors detail property: {property_id!r}")
        fields[metadata_key] = property_id
        required.add(metadata_key)
    return fields, frozenset(required)


async def _enrich_sf_detail_fields(
    jobs: list[DiscoveredJob],
    client: httpx.AsyncClient,
    *,
    feed_url: str,
    fields: Mapping[str, str],
    required_fields: frozenset[str],
    url_filter: object = None,
) -> None:
    """Populate configured SuccessFactors properties from detail pages.

    The Google feed's ``g:employer`` is commonly the generic value
    ``Careers`` even when one tenant publishes jobs for multiple legal
    employers or services. Career-site detail pages expose tenant-specific
    properties such as ``customfield1``, ``dept``, and ``adcode``. Configured
    ``detail_fields`` are required and fail closed so a tenant markup change
    cannot silently admit or re-identify another employer's posting.
    """
    semaphore = asyncio.Semaphore(_SF_DETAIL_CONCURRENCY)

    async def _one(job: DiscoveredJob) -> None:
        if not _same_https_origin(job.url, feed_url):
            raise RuntimeError(f"SuccessFactors detail enrichment rejected URL: {job.url}")
        async with semaphore:
            document = await _fetch_sf_detail_document(job.url, client)
        metadata = dict(job.metadata or {})
        for metadata_key, property_id in fields.items():
            selector = f'[data-careersite-propertyid="{property_id}"]'
            node = document.css_first(selector)
            value = node.text(strip=True) if node is not None else None
            if value:
                metadata[metadata_key] = value
            elif metadata_key in required_fields:
                raise RuntimeError(
                    "SuccessFactors detail enrichment omitted required property "
                    f"{property_id!r}: {job.url}"
                )
        if metadata:
            job.metadata = metadata

    candidates = _sf_detail_candidates(jobs, url_filter)
    await asyncio.gather(*(_one(job) for job in candidates))


async def _resolve_sf_job_invite_identities(
    jobs: list[DiscoveredJob],
    client: httpx.AsyncClient,
    *,
    feed_url: str,
) -> None:
    """Replace feed aliases with authenticated SuccessFactors detail URLs.

    A SuccessFactors Google feed commonly exposes a title-derived URL and a
    feed-specific numeric ID.  A no-follow request redirects that alias to a
    detail URL containing the tenant's stable requisition ID and locale.  The
    dispatcher can then collapse title and locale variants to ``job-invite``
    URLs while retaining a deterministic preferred locale's rich feed data.

    This is an explicit, bounded opt-in because it adds one provider request
    per discovered job.  Any malformed, cross-origin, or non-redirecting
    response fails the board run rather than silently changing identities.
    """

    semaphore = asyncio.Semaphore(_SF_DETAIL_CONCURRENCY)

    async def _one(job: DiscoveredJob) -> None:
        if not _same_https_origin(job.url, feed_url):
            raise SuccessFactorsJobIdentityError(
                f"SuccessFactors job identity rejected source URL: {job.url}"
            )

        response: httpx.Response | None = None
        async with semaphore:
            for attempt in range(_SF_DETAIL_ATTEMPTS):
                try:
                    response = await client.head(job.url, follow_redirects=False)
                except httpx.HTTPError as exc:
                    if attempt >= _SF_DETAIL_ATTEMPTS - 1:
                        raise SuccessFactorsJobIdentityError(
                            f"SuccessFactors job identity request failed: {job.url}"
                        ) from exc
                else:
                    if response.status_code in _SF_DETAIL_REDIRECT_STATUSES:
                        break
                    if (
                        response.status_code not in _SF_DETAIL_RETRYABLE_STATUSES
                        and not is_retryable_status(response.status_code)
                    ):
                        raise SuccessFactorsJobIdentityError(
                            "SuccessFactors job identity expected a redirect "
                            f"but got HTTP {response.status_code}: {job.url}"
                        )
                    if attempt >= _SF_DETAIL_ATTEMPTS - 1:
                        raise SuccessFactorsJobIdentityError(
                            "SuccessFactors job identity exhausted retries "
                            f"after HTTP {response.status_code}: {job.url}"
                        )
                delay = 0.5 * (2**attempt) * (0.5 + random.random())
                await _sleep(delay)

        if response is None:
            raise SuccessFactorsJobIdentityError(
                f"SuccessFactors job identity returned no response: {job.url}"
            )
        location = response.headers.get("location")
        if not location:
            raise SuccessFactorsJobIdentityError(
                f"SuccessFactors job identity redirect omitted Location: {job.url}"
            )
        redirected = urljoin(job.url, location)
        if not _same_https_origin(redirected, feed_url):
            raise SuccessFactorsJobIdentityError(
                f"SuccessFactors job identity rejected redirect: {redirected}"
            )
        try:
            parsed = urlparse(redirected)
            port = parsed.port
        except ValueError as exc:
            raise SuccessFactorsJobIdentityError(
                f"SuccessFactors job identity redirect was invalid: {redirected}"
            ) from exc
        match = _SF_JOB_IDENTITY_RE.fullmatch(parsed.path)
        if (
            match is None
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
        ):
            raise SuccessFactorsJobIdentityError(
                f"SuccessFactors job identity redirect had unexpected shape: {redirected}"
            )

        metadata = dict(job.metadata or {})
        feed_id = metadata.get("id")
        if feed_id is not None:
            metadata["feed_id"] = feed_id
        metadata["job_invite_id"] = match.group("job_id")
        metadata["job_locale"] = match.group("locale")
        job.metadata = metadata
        job.url = urlunparse(parsed._replace(query="", fragment=""))

    tasks = [asyncio.create_task(_one(job)) for job in jobs]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


# ── Feed URL helpers ────────────────────────────────────────────────────


def _build_feed_url(board_url: str, path: str) -> str:
    """Build a feed URL from a board URL and feed path."""
    parsed = urlparse(board_url)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _add_pagination(url: str, offset: int, per_page: int) -> str:
    """Add pagination query parameters to a URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    params["offset"] = [str(offset)]
    params["per_page"] = [str(per_page)]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def _add_page_number(url: str, page: int, query_param: str) -> str:
    """Add a one-based page-number query parameter to a URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    params[query_param] = [str(page)]
    new_query = urlencode({key: values[0] for key, values in params.items()})
    return urlunparse(parsed._replace(query=new_query))


# ── Feed fetching ───────────────────────────────────────────────────────


def _feed_head_is_xml(head: bytes, encoding: str | None) -> bool:
    """Return whether the bounded response prefix looks like XML/RSS."""
    text = head.decode(encoding or "utf-8", errors="ignore").lstrip().lower()
    return text.startswith(("<?xml", "<rss", "<feed"))


def _feed_parser_items(parser: ET.XMLPullParser, chunk: bytes) -> Iterator[ET.Element]:
    """Feed one bounded byte chunk and yield completed RSS items."""
    parser.feed(chunk)
    events = cast(Iterator[tuple[str, ET.Element]], parser.read_events())
    for _event, element in events:
        if element.tag == "item" or element.tag.endswith("}item"):
            yield element


async def _stream_feed_items(
    feed_url: str,
    preset: _Preset,
    client: httpx.AsyncClient,
    *,
    retries: int = 3,
    base_delay: float = 0.5,
) -> AsyncIterator[ET.Element]:
    """Yield feed items without buffering the HTTP body or XML tree.

    ``httpx.get`` buffers the complete decoded response, and ``ET.fromstring``
    then builds a second full-size representation. SuccessFactors feeds can
    exceed hundreds of MiB, so that pair can exhaust a 1 GiB worker before the
    generic monitor wrapper gets a chance to split the result into batches.

    Status and transport failures retain the crawler's bounded retry policy.
    Once an item has been yielded, a later transport failure is propagated
    immediately: the board run remains failed (and therefore cannot tombstone
    an unseen tail) rather than replaying already-processed batches.
    """
    from src.metrics import http_retry_attempts_total, http_retry_host
    from src.shared.http_retry import PaginationFetchError, is_retryable_status
    from src.shared.tdm import TDMReservedError
    from src.shared.tdm import check_response as _tdm_check

    host = http_retry_host(feed_url)
    last_error: BaseException | None = None
    last_status: int | None = None
    retried = False

    for attempt in range(retries):
        emitted = 0
        try:
            async with client.stream("GET", feed_url, follow_redirects=True) as response:
                last_status = response.status_code
                if response.status_code != 200:
                    if (
                        is_retryable_status(response.status_code)
                        or response.status_code in preset.retryable_statuses
                    ):
                        last_error = None
                    else:
                        raise PaginationFetchError(
                            feed_url,
                            attempts=attempt + 1,
                            last_status=response.status_code,
                        )
                else:
                    # RSS/XML cannot carry an HTML meta policy declaration at
                    # document level; the canonical HTTP header still applies.
                    _tdm_check(response)
                    parser = ET.XMLPullParser(events=("end",))
                    prefix = bytearray()
                    sniffed = False

                    async for chunk in response.aiter_bytes(chunk_size=_HTTP_CHUNK_BYTES):
                        if not sniffed:
                            prefix.extend(chunk)
                            if len(prefix) < _SNIFF_BYTES:
                                continue
                            head = bytes(prefix[:_SNIFF_BYTES])
                            if not _feed_head_is_xml(head, response.encoding):
                                raise RssFeedNotXml(f"feed returned non-XML content: {feed_url}")
                            chunk = bytes(prefix)
                            prefix.clear()
                            sniffed = True

                        for item in _feed_parser_items(parser, chunk):
                            emitted += 1
                            yield item
                            # The pull parser's root retains the element shell;
                            # clear its potentially huge description children.
                            item.clear()

                    if not sniffed:
                        if not _feed_head_is_xml(bytes(prefix), response.encoding):
                            raise RssFeedNotXml(f"feed returned non-XML content: {feed_url}")
                        for item in _feed_parser_items(parser, bytes(prefix)):
                            emitted += 1
                            yield item
                            item.clear()

                    parser.close()  # validate that the streamed XML completed
                    if retried:
                        http_retry_attempts_total.labels(host=host, outcome="recovered").inc()
                    return
        except (PaginationFetchError, RssFeedNotXml, ET.ParseError, TDMReservedError):
            raise
        except httpx.HTTPError as exc:
            last_error = exc
            last_status = None
            if emitted:
                raise PaginationFetchError(
                    feed_url,
                    attempts=attempt + 1,
                    last_error=type(exc).__name__,
                ) from exc

        retried = True
        http_retry_attempts_total.labels(host=host, outcome="retry").inc()
        if attempt < retries - 1:
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "rss.feed_backoff",
                url=feed_url,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                last_status=last_status,
                last_error=type(last_error).__name__ if last_error else None,
            )
            await _sleep(delay)

    http_retry_attempts_total.labels(host=host, outcome="exhausted").inc()
    raise PaginationFetchError(
        feed_url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_error).__name__ if last_error else None,
    )


async def _probe_feed(
    feed_url: str,
    client: httpx.AsyncClient,
    preset_name: str | None = None,
) -> tuple[bool, int | None]:
    """Probe an RSS feed URL. Returns (found, job_count).

    For paginated presets, only the first page is fetched — count may be
    approximate (capped at page_size).
    """
    try:
        preset = _PRESETS.get(preset_name or "") or _Preset([], [], {})
        count = 0
        async for _item in _stream_feed_items(feed_url, preset, client):
            count += 1
        return True, count
    except Exception as exc:
        from src.shared.tdm import TDMReservedError

        if isinstance(exc, TDMReservedError):
            raise
        return False, None


def _advertised_rss_feed_url(page_url: str, html_text: str) -> str | None:
    """Return an RSS feed explicitly advertised by a careers page.

    Generic career portals often publish a valid RSS endpoint via
    ``<link rel="alternate" type="application/rss+xml">`` without using one
    of the ATS-specific paths known to this monitor. Prefer that first-party
    declaration over guessing provider routes. When an HTTPS page advertises
    an HTTP URL on the same host, upgrade it to HTTPS to avoid mixed-content
    legacy links such as older recruitment portals still emit.
    """

    if not html_text:
        return None
    try:
        links = LexborHTMLParser(html_text).css("link")
    except (TypeError, ValueError):
        return None

    try:
        page = urlparse(page_url)
        page_port = page.port or ({"http": 80, "https": 443}.get(page.scheme))
    except ValueError:
        return None
    if page.scheme not in {"http", "https"} or not page.hostname or page_port is None:
        return None
    for link in links:
        attrs = link.attributes
        rel = {token.casefold() for token in (attrs.get("rel") or "").split()}
        media_type = (attrs.get("type") or "").split(";", 1)[0].strip().casefold()
        href = (attrs.get("href") or "").strip()
        if "alternate" not in rel or media_type != "application/rss+xml" or not href:
            continue

        try:
            candidate = urlparse(urljoin(page_url, href))
            candidate_port = candidate.port or ({"http": 80, "https": 443}.get(candidate.scheme))
        except ValueError:
            continue
        if (
            candidate.scheme not in {"http", "https"}
            or not candidate.hostname
            or candidate_port is None
            or candidate.username is not None
            or candidate.password is not None
            or candidate.hostname.casefold() != (page.hostname or "").casefold()
        ):
            continue
        if page.scheme == "https" and candidate.scheme == "http":
            # Keep the trusted page origin rather than carrying an explicitly
            # advertised port across the scheme upgrade.
            if candidate.port not in {None, 80}:
                continue
            candidate = candidate._replace(scheme="https", netloc=page.netloc)
            candidate_port = page_port
        if candidate.scheme != page.scheme or candidate_port != page_port:
            continue
        return urlunparse(candidate)
    return None


# ── Discover ────────────────────────────────────────────────────────────


def _feed_config(board: dict) -> tuple[str, str, _Preset] | None:
    """Resolve a board into ``(preset_name, feed_url, preset)``."""
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}
    preset_name = metadata.get("preset", "generic")
    preset = _PRESETS.get(preset_name)

    # Determine feed URL: explicit config > derive from preset > fallback
    feed_url = metadata.get("feed_url")
    if not feed_url and preset:
        feed_url = _build_feed_url(board_url, preset.feed_paths[0])
    if not feed_url:
        log.error("rss.no_feed_url", board_url=board_url, preset=preset_name)
        return None

    if preset is None:
        # Generic fallback — non-paginated, standard parser
        preset = _Preset(
            feed_paths=[],
            page_patterns=[],
            feed_ns={},
        )
    return preset_name, feed_url, preset


async def discover_stream(
    board: dict, client: httpx.AsyncClient, pw=None
) -> AsyncIterator[list[DiscoveredJob] | MonitorResult]:
    """Yield bounded parsed-job batches across streamed RSS pages."""
    metadata = board.get("metadata") or {}
    if metadata.get("preset") == "successfactors" and metadata.get("variant") == "legacy":
        from src.core.monitors._successfactors_legacy import discover_legacy_stream

        async for batch in discover_legacy_stream(board, client):
            yield batch
        return

    config = _feed_config(board)
    if config is None:
        return
    preset_name, feed_url, preset = config
    detail_fields, required_detail_fields = (
        _sf_detail_fields(metadata) if preset_name == "successfactors" else ({}, frozenset())
    )
    parser = _PARSERS.get(preset_name, _parse_generic_item)
    resolve_job_invite_identity = metadata.get("resolve_job_invite_identity", False)
    if not isinstance(resolve_job_invite_identity, bool):
        raise ValueError("RSS resolve_job_invite_identity must be a boolean")
    if resolve_job_invite_identity and preset_name != "successfactors":
        raise ValueError("RSS resolve_job_invite_identity requires the successfactors preset")
    jobs: list[DiscoveredJob] = []
    total_jobs = 0
    offset = 0
    page_number = 1
    seen_page_urls: set[str] = set()

    while True:
        if preset.paginated and preset.page_query_param:
            page_url = _add_page_number(feed_url, page_number, preset.page_query_param)
        elif preset.paginated:
            page_url = _add_pagination(feed_url, offset, preset.page_size)
        else:
            page_url = feed_url
        page_items = 0
        page_urls: set[str] = set()
        async for item in _stream_feed_items(page_url, preset, client):
            page_items += 1
            parsed = parser(item)
            if parsed is None:
                continue
            page_urls.add(parsed.url)
            jobs.append(parsed)
            total_jobs += 1

            if resolve_job_invite_identity and total_jobs > _SF_JOB_IDENTITY_MAX_JOBS:
                raise SuccessFactorsJobIdentityError(
                    "SuccessFactors job identity exceeded bounded feed cap "
                    f"({_SF_JOB_IDENTITY_MAX_JOBS})"
                )

            if total_jobs >= MAX_JOBS:
                log.warning("rss.truncated", feed=feed_url, total=total_jobs, cap=MAX_JOBS)
                if detail_fields:
                    await _enrich_sf_detail_fields(
                        jobs,
                        client,
                        feed_url=feed_url,
                        fields=detail_fields,
                        required_fields=required_detail_fields,
                        url_filter=metadata.get("url_filter"),
                    )
                if resolve_job_invite_identity:
                    await _resolve_sf_job_invite_identities(
                        jobs,
                        client,
                        feed_url=feed_url,
                    )
                yield truncated_rich_result(jobs)
                return
            if len(jobs) >= _STREAM_BATCH:
                if detail_fields:
                    await _enrich_sf_detail_fields(
                        jobs,
                        client,
                        feed_url=feed_url,
                        fields=detail_fields,
                        required_fields=required_detail_fields,
                        url_filter=metadata.get("url_filter"),
                    )
                if resolve_job_invite_identity:
                    await _resolve_sf_job_invite_identities(
                        jobs,
                        client,
                        feed_url=feed_url,
                    )
                yield jobs
                jobs = []

        if preset.paginated and preset.page_query_param:
            if page_items >= preset.page_size and not (page_urls - seen_page_urls):
                raise PaginationFetchError(
                    page_url,
                    attempts=1,
                    last_error="RepeatedPaginatedFeedPage",
                )
            seen_page_urls.update(page_urls)

        if not preset.paginated or page_items < preset.page_size:
            break
        offset += preset.page_size
        page_number += 1

    if jobs:
        if detail_fields:
            await _enrich_sf_detail_fields(
                jobs,
                client,
                feed_url=feed_url,
                fields=detail_fields,
                required_fields=required_detail_fields,
                url_filter=metadata.get("url_filter"),
            )
        if resolve_job_invite_identity:
            await _resolve_sf_job_invite_identities(
                jobs,
                client,
                feed_url=feed_url,
            )
        yield jobs


async def discover(
    board: dict, client: httpx.AsyncClient, pw=None
) -> list[DiscoveredJob] | MonitorResult:
    """Fetch job listings while retaining the non-streaming public API."""
    from src.core.monitor import MonitorResult

    metadata = board.get("metadata") or {}
    if metadata.get("preset") == "successfactors" and metadata.get("variant") == "legacy":
        from src.core.monitors._successfactors_legacy import discover_legacy

        return await discover_legacy(board, client)

    jobs: list[DiscoveredJob] = []
    was_truncated = False
    async for batch in discover_stream(board, client, pw=pw):
        if isinstance(batch, MonitorResult):
            jobs.extend((batch.jobs_by_url or {}).values())
            was_truncated = was_truncated or bool(batch.truncated)
        else:
            jobs.extend(batch)

    if was_truncated:
        return truncated_rich_result(jobs)
    return jobs


# ── Can Handle (auto-detection) ─────────────────────────────────────────


def _embedded_legacy_boards(page: str) -> list[SuccessFactorsLegacyBoard]:
    """Return unfiltered legacy SuccessFactors boards linked by a wrapper page.

    Some companies render their own job-search shell while linking applications
    to a legacy SuccessFactors tenant.  Those links include login/session query
    parameters that the strict public board parser intentionally rejects.  Parse
    only anchor destinations, allow the known wrapper parameters, and reduce the
    result back to the strict host + company identity before probing it.
    """

    if not page:
        return []
    document = LexborHTMLParser(page)
    boards: list[SuccessFactorsLegacyBoard] = []
    seen: set[SuccessFactorsLegacyBoard] = set()
    for anchor in document.css("a[href]"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        try:
            parsed = urlparse(html.unescape(href))
            port = parsed.port
            pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=16)
        except (TypeError, ValueError):
            continue
        if (
            parsed.scheme.casefold() != "https"
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.fragment
            or parsed.path.rstrip("/").casefold() != "/career"
        ):
            continue
        if not pairs or any(name not in _SF_WRAPPER_QUERY_KEYS for name, _value in pairs):
            continue
        params: dict[str, str] = {}
        duplicate = False
        for name, value in pairs:
            if name in params:
                duplicate = True
                break
            params[name] = value
        if duplicate:
            continue
        company = normalize_successfactors_company(params.get("company"))
        career_company = normalize_successfactors_company(params.get("career_company"))
        if (
            company is None
            or ("career_company" in params and career_company is None)
            or (career_company is not None and career_company != company)
            or params.get("career_ns") not in {None, "", "job_listing_summary"}
            or params.get("navBarLevel") not in {None, "", "JOB_SEARCH"}
        ):
            continue
        canonical = urlunparse(
            (
                "https",
                parsed.netloc,
                "/career",
                "",
                urlencode({"company": company}),
                "",
            )
        )
        board = successfactors_legacy_board_from_url(canonical)
        if board is not None and board not in seen:
            boards.append(board)
            seen.add(board)
    return boards


async def _probe_legacy_board(
    legacy_board: SuccessFactorsLegacyBoard,
    client: httpx.AsyncClient,
) -> dict | None:
    """Probe a legacy board, following safe same-company migrations."""

    from src.core.monitors._successfactors_legacy import (
        SuccessFactorsLegacyRedirect,
        probe_legacy,
    )
    from src.shared.tdm import TDMReservedError

    probe_board = legacy_board
    migrated_location: str | None = None
    for _hop in range(3):
        try:
            return await probe_legacy(probe_board, client)
        except SuccessFactorsLegacyRedirect as exc:
            redirected_board = successfactors_legacy_board_from_url(exc.location)
            if (
                redirected_board is not None
                and redirected_board.company == probe_board.company
                and redirected_board != probe_board
            ):
                probe_board = redirected_board
                continue
            migrated_location = exc.location
            break
        except TDMReservedError:
            raise
        except Exception:
            log.debug(
                "rss.successfactors_legacy_probe_failed",
                url=legacy_board.listing_url,
                exc_info=True,
            )
            break

    if migrated_location:
        try:
            redirected = urlparse(migrated_location)
            if (
                redirected.scheme.casefold() == "https"
                and redirected.hostname
                and redirected.username is None
                and redirected.password is None
                and redirected.port in {None, 443}
            ):
                feed = f"https://{redirected.hostname}/googlefeed.xml"
                found, count = await _probe_feed(feed, client, "successfactors")
                if found:
                    migrated: dict = {
                        "preset": "successfactors",
                        "variant": "feed",
                        "feed_url": feed,
                    }
                    if count is not None:
                        migrated["jobs"] = count
                    return migrated
        except (TypeError, ValueError):
            pass
    return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect RSS-based ATS: HTML scan for preset markers → feed probe."""
    if client is None:
        return None

    # Legacy SAP-hosted tenants use a case-sensitive company identity and a
    # static DWR listing protocol. Probe this strict direct shape before the
    # general HTML/feed path. Some of these URLs redirect to a migrated CSB
    # site; in that case prefer the redirected origin's existing Google feed.
    legacy_board = successfactors_legacy_board_from_url(url)
    if legacy_board is not None:
        legacy_result = await _probe_legacy_board(legacy_board, client)
        if legacy_result is not None:
            return legacy_result

    # 1. Fetch page HTML once for all preset pattern checks
    # Large corporate wrapper pages can place the actual ATS application link
    # after the generic 500 kB probe cap (Liebherr is currently about 1 MB).
    html_text = await fetch_page_text(url, client, max_chars=_DETECTION_MAX_CHARS)

    embedded_boards = _embedded_legacy_boards(html_text or "")
    if len(embedded_boards) == 1:
        embedded_board = embedded_boards[0]
        embedded_result = await _probe_legacy_board(embedded_board, client)
        if embedded_result is not None:
            log.info(
                "rss.successfactors_wrapper_detected",
                url=url,
                host=embedded_board.host,
                company=embedded_board.company,
            )
            return embedded_result
    elif embedded_boards:
        log.info(
            "rss.successfactors_wrapper_ambiguous",
            url=url,
            identities=len(embedded_boards),
        )

    # WP Job Manager pages also advertise WordPress's site-wide post feed.
    # Prefer the provider-specific jobs feed when its plugin markers are
    # present, otherwise auto-detection would silently monitor blog posts.
    wp_job_manager = _PRESETS["wp_job_manager"]
    if html_text and any(pattern.search(html_text) for pattern in wp_job_manager.page_patterns):
        feed = _build_feed_url(url, wp_job_manager.feed_paths[0])
        found, count = await _probe_feed(feed, client, "wp_job_manager")
        if found:
            result: dict = {"preset": "wp_job_manager", "feed_url": feed}
            if count is not None:
                result["jobs"] = count
            log.info(
                "rss.detected_in_page",
                url=url,
                preset="wp_job_manager",
                jobs=count,
            )
            return result

    advertised_feed = _advertised_rss_feed_url(url, html_text or "")
    if advertised_feed:
        found, count = await _probe_feed(advertised_feed, client, "generic")
        if found:
            result: dict = {"preset": "generic", "feed_url": advertised_feed}
            if count is not None:
                result["jobs"] = count
            log.info("rss.detected_advertised_feed", url=url, feed=advertised_feed, jobs=count)
            return result

    for preset_name, preset in _PRESETS.items():
        if preset_name == "wp_job_manager":
            continue
        detected = False
        if html_text and preset.page_patterns:
            detected = any(p.search(html_text) for p in preset.page_patterns)

        if detected:
            # Try all feed paths for this preset
            for path in preset.feed_paths:
                feed = _build_feed_url(url, path)
                found, count = await _probe_feed(feed, client, preset_name)
                if found:
                    result: dict = {"preset": preset_name, "feed_url": feed}
                    if preset_name == "successfactors":
                        result["variant"] = "feed"
                    if count is not None:
                        result["jobs"] = count
                    log.info(
                        "rss.detected_in_page",
                        url=url,
                        preset=preset_name,
                        jobs=count,
                    )
                    return result

    # 2. Blind feed probe as fallback — try all presets' feed paths
    for preset_name, preset in _PRESETS.items():
        if preset_name == "wp_job_manager":
            continue
        for path in preset.feed_paths:
            feed = _build_feed_url(url, path)
            found, count = await _probe_feed(feed, client, preset_name)
            if found:
                result = {"preset": preset_name, "feed_url": feed}
                if preset_name == "successfactors":
                    result["variant"] = "feed"
                if count is not None:
                    result["jobs"] = count
                log.info(
                    "rss.detected_by_probe",
                    url=url,
                    preset=preset_name,
                    jobs=count,
                )
                return result

    return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    if metadata.get("preset") == "successfactors" and metadata.get("variant") == "legacy":
        from src.shared.successfactors import successfactors_legacy_board_from_metadata

        identity = successfactors_legacy_board_from_metadata(metadata)
        if identity is None:
            identity = successfactors_legacy_board_from_url(board_url)
        if identity is not None:
            await save_text_response(
                artifact_dir,
                client,
                identity.listing_url,
                filename="response.html",
                follow_redirects=False,
            )
        return
    feed = metadata.get("feed_url")
    if not feed:
        preset = _PRESETS.get(metadata.get("preset", "generic"))
        if preset:
            feed = _build_feed_url(board_url, preset.feed_paths[0])
    if not feed:
        return
    await save_text_response(
        artifact_dir,
        client,
        feed,
        filename="response.xml",
        follow_redirects=True,
    )


register(
    "rss",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    stream=discover_stream,
    save_raw=save_raw,
)
