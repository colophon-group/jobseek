"""Umantis ATS monitor (Haufe Group / Abacus).

Server-rendered HTML listing pages at ``recruitingapp-{ID}[.de].umantis.com``.
Job links use class ``HSTableLinkSubTitle`` across all customer templates.

Listing:  GET /Jobs/All  (paginated via ``tc{tableNr}=p{page}``)
Detail:   /Vacancies/{id}/Description (redirects to an available locale)

Returns partial rich data from the shared listing template: URL, title,
location, and employment type. Templates vary widely across customers, so a
detail scraper is still required for descriptions.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.truncation import truncated_rich_result, truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_PAGES = 100
PAGE_SIZE = 10  # Umantis default per page

# Pagination retry budget. Symmetric with the dom monitor (#2737),
# accenture (#2735), api_sniffer (#2733), PCSX (#2734), and workday
# (#2748): 3 total attempts, exponential backoff with full jitter
# starting at 0.5s. Pre-fix, a transient 5xx / 429 / network error
# mid-pagination silently truncated the URL set, then
# ``_MARK_GONE_BY_TIMESTAMP`` tombstoned every URL on unfetched pages —
# the same shape of bug as the 2026-04-26 NHS spike (#2722).
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5

# recruitingapp-{ID}[.de|.ch].umantis.com
_HOST_RE = re.compile(r"^recruitingapp-(\d+)(?:\.\w+)?\.umantis\.com$", re.IGNORECASE)

_IGNORE_SUBDOMAINS = {"www", "api", "app", "static", "cdn", "mail", "help"}

_PAGE_MARKERS = [
    re.compile(r"recruitingapp-\d+(?:\.\w+)?\.umantis\.com"),
    re.compile(r"umantis\.com/Vacancies/"),
    re.compile(r"umantis\.com/Jobs/"),
    re.compile(r"globalUmantisParams"),
    re.compile(r"HSTableLinkSubTitle"),
]

_LISTING_URL_RE = re.compile(
    r"https?://recruitingapp-\d+(?:\.\w+)?\.umantis\.com/Jobs/[^\"'<>\\\s]+",
    re.IGNORECASE,
)
_VACANCY_PATH_RE = re.compile(r"/Vacancies/([1-9]\d*)/Description/([1-9]\d*)/?")


@dataclass(slots=True)
class _ParsedJob:
    vacancy_id: str
    language_id: str
    job: DiscoveredJob


@dataclass(frozen=True, slots=True)
class _Navigation:
    table_nr: str
    total: int
    first: int
    last: int
    page: int
    next_url: str | None
    next_active: bool


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Borrow a caller-owned transport without sharing cookies or closing it."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        """Leave the caller-owned transport open."""


@asynccontextmanager
async def _isolated_client(
    client: httpx.AsyncClient,
    *,
    expected_origin: str | None = None,
):
    """Use the caller's network policy with a fresh per-discovery cookie jar.

    Umantis persists listing filters in cookies. Reusing a worker-wide jar can
    therefore make a later ``/Jobs/All`` request inherit a previous employer's
    ``CompanyID``. The borrowed transport preserves SSRF/proxy/test behavior,
    while the nested client owns an empty cookie jar and cannot close the
    caller's connection pool.
    """

    async def validate_request_origin(request: httpx.Request) -> None:
        if expected_origin is None:
            return
        requested = urlparse(str(request.url))
        expected = urlparse(expected_origin)
        if (
            requested.scheme.casefold() != expected.scheme.casefold()
            or requested.netloc.casefold() != expected.netloc.casefold()
            or requested.username is not None
            or requested.password is not None
        ):
            # The shared retry helper propagates PaginationFetchError without
            # retrying. This hook therefore blocks the cross-origin redirect
            # before the redirected response body can influence discovery.
            from src.shared.http_retry import PaginationFetchError

            raise PaginationFetchError(
                str(request.url),
                attempts=1,
                last_location=str(request.url),
            )

    transport = client._transport  # type: ignore[attr-defined]
    async with httpx.AsyncClient(
        transport=_BorrowedTransport(transport),
        headers=dict(client.headers),
        timeout=client.timeout,
        follow_redirects=True,
        event_hooks={"request": [validate_request_origin]},
    ) as isolated:
        yield isolated


# ── URL helpers ─────────────────────────────────────────────────────────


def _parse_host(url: str) -> tuple[str | None, str | None]:
    """Extract (customer_id, region) from an Umantis URL.

    Returns e.g. ("2698", "") for .umantis.com or ("5181", "de") for .de.umantis.com.
    Returns (None, None) for non-Umantis URLs.
    """
    host = urlparse(url).hostname or ""
    m = _HOST_RE.match(host)
    if not m:
        return None, None
    cid = m.group(1)
    # Determine region from subdomain: recruitingapp-{ID}.de.umantis.com
    parts = host.split(".")
    # e.g. ['recruitingapp-{ID}', 'de', 'umantis', 'com']
    if len(parts) == 4:
        return cid, parts[1]  # "de", "ch", etc.
    return cid, ""


def _base_url(customer_id: str, region: str = "") -> str:
    """Build the base URL for a customer."""
    if region:
        return f"https://recruitingapp-{customer_id}.{region}.umantis.com"
    return f"https://recruitingapp-{customer_id}.umantis.com"


def _listing_path_from_url(url: str) -> str | None:
    """Return a tenant-scoped listing path without dropping its query."""
    parsed = urlparse(unescape(url))
    if not parsed.path.startswith("/Jobs/"):
        return None
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _embedded_listing_path(html: str, customer_id: str) -> str | None:
    """Extract the first listing URL for *customer_id* from embedding HTML."""
    for candidate in _LISTING_URL_RE.findall(unescape(html)):
        cid, _region = _parse_host(candidate)
        if cid == customer_id:
            return _listing_path_from_url(candidate)
    return None


def _pagination_url(listing_url: str, table_nr: str, page: int) -> str:
    """Legacy pagination fallback that preserves scoped listing filters."""
    parsed = urlparse(listing_url)
    page_key = f"tc{table_nr}"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != page_key and key.casefold() != "reset"
    ]
    query.append((page_key, f"p{page}"))
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


# ── Listing page parsing ────────────────────────────────────────────────


class _JobLinkParser(HTMLParser):
    """Extract partial job data from Umantis listing rows.

    The job link class is stable across Umantis customer templates. Listing
    fields are identified by their stable icon classes, with translated column
    labels as a fallback. Detail templates are customer-specific, but the
    listing rows consistently expose the location and employment type needed
    to enrich those detail scrapes.
    """

    def __init__(
        self,
        base_url: str,
        *,
        expected_employer: str | None = None,
        employer_field_id: str | None = None,
    ):
        super().__init__()
        self.base = base_url.rstrip("/")
        self._base_parts = urlparse(self.base)
        self._expected_employer = expected_employer
        self._employer_field_id = employer_field_id
        self.jobs: list[_ParsedJob] = []
        self._in_row = False
        self._in_link = False
        self._current_url: str | None = None
        self._current_vacancy_id: str | None = None
        self._current_language_id: str | None = None
        self._current_title: str = ""
        self._current_employer_values: list[str] = []
        self._current_location: str | None = None
        self._current_employment_type: str | None = None
        self._current_field: str | None = None
        self._capture_label_depth = 0
        self._current_label = ""
        self._capture_value_depth = 0
        self._current_value = ""
        self._capture_employer_value_depth = 0
        self._current_employer_value = ""

    def _reset_job(self) -> None:
        self._current_url = None
        self._current_vacancy_id = None
        self._current_language_id = None
        self._current_title = ""
        self._current_employer_values = []
        self._current_location = None
        self._current_employment_type = None
        self._current_field = None
        self._capture_label_depth = 0
        self._current_label = ""
        self._capture_value_depth = 0
        self._current_value = ""
        self._capture_employer_value_depth = 0
        self._current_employer_value = ""

    def _append_job(self) -> None:
        title = self._current_title.strip()
        if self._current_url and self._current_vacancy_id and self._current_language_id and title:
            if self._expected_employer is not None:
                employer_values = [
                    _normalized_identity(value) for value in self._current_employer_values
                ]
                if employer_values != [_normalized_identity(self._expected_employer)]:
                    raise ValueError(
                        "Umantis listing row did not have the exact configured employer field: "
                        f"{self._current_vacancy_id}"
                    )
            self.jobs.append(
                _ParsedJob(
                    vacancy_id=self._current_vacancy_id,
                    language_id=self._current_language_id,
                    job=DiscoveredJob(
                        url=self._current_url,
                        title=title,
                        locations=[self._current_location] if self._current_location else None,
                        employment_type=self._current_employment_type,
                    ),
                )
            )
        self._reset_job()

    def _vacancy_identity(self, href: str) -> tuple[str, str, str]:
        """Return a same-origin numeric vacancy identity and canonical URL."""
        resolved = urlparse(urljoin(f"{self.base}/", unescape(href)))
        if (
            resolved.scheme.casefold() != self._base_parts.scheme.casefold()
            or resolved.netloc.casefold() != self._base_parts.netloc.casefold()
            or resolved.username is not None
            or resolved.password is not None
        ):
            raise ValueError(f"Umantis vacancy link crossed the configured origin: {href}")
        match = _VACANCY_PATH_RE.fullmatch(resolved.path)
        if match is None:
            raise ValueError(f"Umantis vacancy link did not have a numeric canonical path: {href}")
        vacancy_id, language_id = match.groups()
        # The numeric provider vacancy ID is stable across locale variants.
        # Umantis's suffix-free Description route redirects to a currently
        # available locale, so it is both a stable source identity and a viable
        # scrape URL even when a vacancy exists only as /2 or /3.
        canonical_url = f"{self.base}/Vacancies/{vacancy_id}/Description"
        return vacancy_id, language_id, canonical_url

    @staticmethod
    def _field_from_label(label: str) -> str | None:
        normalized = re.sub(r"\s+", " ", label).strip().rstrip(":").casefold()
        if normalized in {
            "anstellungsort",
            "arbeitsort",
            "standort",
            "ort",
            "location",
            "lieu",
            "localité",
            "localita",
            "località",
            "luogo",
            "sede",
        }:
            return "location"
        if normalized in {
            "art",
            "beschäftigungsart",
            "employment category",
            "employment type",
            "type d'emploi",
            "tipo di impiego",
        }:
            return "employment_type"
        return None

    def _store_value(self, value: str) -> None:
        clean = re.sub(r"\s+", " ", value).strip()
        if not clean:
            return
        if self._current_field == "location":
            self._current_location = clean
        elif self._current_field == "employment_type":
            self._current_employment_type = clean

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "") or ""

        # Capture complete field contents, including arbitrarily nested spans
        # and formatting tags. A boolean stopped at the first nested closing
        # tag and could validate only a forged prefix of the owner field.
        if tag not in _VOID_HTML_TAGS:
            if self._capture_label_depth:
                self._capture_label_depth += 1
            if self._capture_value_depth:
                self._capture_value_depth += 1
            if self._capture_employer_value_depth:
                self._capture_employer_value_depth += 1

        if tag == "tr":
            if self._in_row:
                self._append_job()
            self._in_row = True
            self._reset_job()
            return

        if tag == "li" and self._in_row:
            self._current_field = None
            self._current_label = ""
            return

        if tag == "i" and self._in_row:
            if "icon-department" in cls:
                self._current_field = "location"
            elif "icon-jobtype" in cls:
                self._current_field = "employment_type"
            return

        if tag == "span" and self._in_row:
            if "visually-hidden" in cls and not self._capture_label_depth:
                self._capture_label_depth = 1
                self._current_label = ""
            elif "column-value" in cls and not self._capture_value_depth:
                self._capture_value_depth = 1
                self._current_value = ""
                if (
                    self._employer_field_id is not None
                    and attrs_dict.get("id") == self._employer_field_id
                ):
                    self._capture_employer_value_depth = 1
                    self._current_employer_value = ""

        if tag != "a" or "HSTableLinkSubTitle" not in cls:
            return
        href = attrs_dict.get("href")
        if not href or "/Vacancies/" not in href:
            return
        self._in_link = True
        vacancy_id, language_id, canonical_url = self._vacancy_identity(href)
        self._current_vacancy_id = vacancy_id
        self._current_language_id = language_id
        self._current_url = canonical_url
        self._current_title = ""

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._current_title += data
        if self._capture_label_depth:
            self._current_label += data
        if self._capture_value_depth:
            self._current_value += data
        if self._capture_employer_value_depth:
            self._current_employer_value += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            self._in_link = False
            # Some custom CNAME templates expose bare links without table
            # rows. Preserve the previous URL/title-only fallback for them.
            if not self._in_row:
                self._append_job()
            return

        if self._capture_label_depth:
            self._capture_label_depth -= 1
            if not self._capture_label_depth:
                self._current_field = self._field_from_label(self._current_label)

        if self._capture_value_depth:
            self._capture_value_depth -= 1
            if not self._capture_value_depth:
                self._store_value(self._current_value)
                self._current_value = ""

        if self._capture_employer_value_depth:
            self._capture_employer_value_depth -= 1
            if not self._capture_employer_value_depth:
                self._current_employer_values.append(self._current_employer_value)
                self._current_employer_value = ""

        if tag == "li" and self._in_row:
            self._current_field = None
            self._current_label = ""
            return

        if tag == "tr" and self._in_row:
            self._append_job()
            self._in_row = False


class _NavigationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "table-navigation":
            return
        payload = dict(attrs).get("initial-data-string")
        if payload is not None:
            self.payloads.append(payload)


_VOID_HTML_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_HIDDEN_CLASS_TOKENS = frozenset({"d-none", "hidden", "sr-only", "visually-hidden"})


def _normalized_identity(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _element_is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    values = {name.casefold(): value for name, value in attrs}
    if "hidden" in values or (values.get("aria-hidden") or "").strip().casefold() == "true":
        return True
    classes = set((values.get("class") or "").casefold().split())
    if classes & _HIDDEN_CLASS_TOKENS:
        return True
    style = re.sub(r"\s+", "", (values.get("style") or "").casefold())
    return "display:none" in style or "visibility:hidden" in style


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._hidden_depth = 0
        self._elements: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        hidden = tag in {
            "head",
            "title",
            "script",
            "style",
            "template",
            "noscript",
        } or _element_is_hidden(attrs)
        if tag not in _VOID_HTML_TAGS:
            if hidden:
                self._hidden_depth += 1
            self._elements.append((tag, hidden))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._elements) - 1, -1, -1):
            if self._elements[index][0] != tag:
                continue
            closing = self._elements[index:]
            del self._elements[index:]
            self._hidden_depth -= sum(hidden for _name, hidden in closing)
            break

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


class _DetailOwnerParser(HTMLParser):
    """Extract the page-level owner declared by Umantis detail documents."""

    def __init__(self) -> None:
        super().__init__()
        self.descriptions: list[str] = []
        self.head_count = 0
        self.outside_head_descriptions = 0
        self.invalid_head_structure = False
        self._head_depth = 0
        self._body_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "head":
            self.head_count += 1
            if self._head_depth or self._body_depth:
                self.invalid_head_structure = True
            self._head_depth += 1
        elif tag == "body":
            self._body_depth += 1

        if tag != "meta":
            return
        values = {name.casefold(): value for name, value in attrs}
        if (values.get("name") or "").strip().casefold() != "description":
            return
        content = values.get("content")
        if self._head_depth == 1 and not self._body_depth and content is not None:
            self.descriptions.append(content)
        else:
            self.outside_head_descriptions += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            if not self._head_depth:
                self.invalid_head_structure = True
            else:
                self._head_depth -= 1
        elif tag == "body":
            if not self._body_depth:
                self.invalid_head_structure = True
            else:
                self._body_depth -= 1

    @property
    def structurally_complete(self) -> bool:
        return not self.invalid_head_structure and not self._head_depth and not self._body_depth


def _bounded_int(value: object, *, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Umantis navigation {name} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value):
        parsed = int(value)
    else:
        raise ValueError(f"Umantis navigation {name} must be an integer")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"Umantis navigation {name} was outside its safe range")
    return parsed


def _navigation_from_payload(payload: str) -> _Navigation:
    try:
        raw = json.loads(unescape(payload))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Umantis navigation JSON was invalid") from exc
    if not isinstance(raw, dict):
        raise ValueError("Umantis navigation JSON must be an object")
    table_nr = raw.get("TableNr")
    if not isinstance(table_nr, str) or re.fullmatch(r"[1-9]\d{0,11}", table_nr) is None:
        raise ValueError("Umantis navigation TableNr must be a bounded numeric identifier")
    total = _bounded_int(raw.get("TableTotalLines"), name="total", minimum=0, maximum=MAX_JOBS)
    first = _bounded_int(raw.get("TableFrom"), name="first", minimum=0, maximum=MAX_JOBS)
    last = _bounded_int(raw.get("TableTo"), name="last", minimum=0, maximum=MAX_JOBS)
    page = _bounded_int(raw.get("TableCurrentPage"), name="page", minimum=1, maximum=MAX_PAGES)
    next_link = raw.get("NextLink")
    if next_link is None:
        next_url = None
        next_active = False
    elif isinstance(next_link, dict):
        next_url_raw = next_link.get("EnhancedUrl")
        next_url = next_url_raw if isinstance(next_url_raw, str) and next_url_raw else None
        next_active = next_link.get("FieldIsActive") in {1, "1"}
    else:
        raise ValueError("Umantis navigation NextLink must be an object")
    return _Navigation(table_nr, total, first, last, page, next_url, next_active)


def _extract_navigation(html: str) -> _Navigation | None:
    parser = _NavigationParser()
    parser.feed(html)
    parser.close()
    if not parser.payloads:
        return None
    navigations = {_navigation_from_payload(payload) for payload in parser.payloads}
    if len(navigations) != 1:
        raise ValueError("Umantis page exposed conflicting navigation metadata")
    return navigations.pop()


def _extract_table_nr(html: str) -> str | None:
    """Extract the table number, retaining a legacy pagination fallback."""
    try:
        navigation = _extract_navigation(html)
    except ValueError:
        navigation = None
    if navigation is not None:
        return navigation.table_nr
    decoded = unescape(html)
    match = re.search(r'"TableNr"\s*:\s*"(\d+)"', decoded)
    if match:
        return match.group(1)
    match = re.search(r"tc(\d+)=p\d+", decoded)
    return match.group(1) if match else None


def _has_visible_text(html: str, expected: str) -> bool:
    parser = _VisibleTextParser()
    parser.feed(html)
    parser.close()
    visible = re.sub(r"\s+", " ", " ".join(parser.parts)).strip().casefold()
    marker = re.sub(r"\s+", " ", expected).strip().casefold()
    return marker in visible


def _parse_jobs_from_html(html: str, base_url: str) -> list[tuple[str, str]]:
    """Parse job links from listing HTML. Returns [(url, title), ...]."""
    return [(job.url, job.title or "") for job in _parse_discovered_jobs_from_html(html, base_url)]


def _parse_parsed_jobs_from_html(
    html: str,
    base_url: str,
    *,
    expected_employer: str | None = None,
    employer_field_id: str | None = None,
) -> list[_ParsedJob]:
    parser = _JobLinkParser(
        base_url,
        expected_employer=expected_employer,
        employer_field_id=employer_field_id,
    )
    parser.feed(html)
    parser.close()
    return parser.jobs


def _parse_discovered_jobs_from_html(html: str, base_url: str) -> list[DiscoveredJob]:
    """Parse partial rich job data from an Umantis listing page."""
    return [parsed.job for parsed in _parse_parsed_jobs_from_html(html, base_url)]


def _deduplicate_vacancies(parsed_jobs: list[_ParsedJob]) -> dict[str, _ParsedJob]:
    """Collapse locale aliases onto one stable numeric provider identity."""
    unique: dict[str, _ParsedJob] = {}
    for parsed in parsed_jobs:
        current = unique.get(parsed.vacancy_id)
        if current is None:
            unique[parsed.vacancy_id] = parsed
            continue
        if current.language_id == parsed.language_id:
            if current.job != parsed.job:
                raise ValueError(
                    "Umantis listing exposed conflicting rows for one vacancy locale: "
                    f"{parsed.vacancy_id}/{parsed.language_id}"
                )
            continue
        if int(parsed.language_id) < int(current.language_id):
            unique[parsed.vacancy_id] = parsed
    return unique


def _uses_rich_listing_results(metadata: dict) -> bool:
    """Return rich rows only when the board explicitly enriches them.

    Existing Umantis boards rely on URL-only discovery so their configured
    detail scraper still runs.  A rich monitor result without ``enrich`` is
    treated as complete by the board pipeline and would otherwise skip those
    detail scrapes, dropping descriptions.
    """
    scraper_config = metadata.get("scraper_config")
    if not isinstance(scraper_config, dict):
        return False
    enrich = scraper_config.get("enrich")
    return isinstance(enrich, list) and bool(enrich)


def _strict_contract(metadata: dict) -> tuple[str, str, str] | None:
    """Validate the opt-in fail-closed listing identity contract."""
    strict = metadata.get("strict_listing_contract", False)
    if not isinstance(strict, bool):
        raise ValueError("Umantis strict_listing_contract must be a boolean")
    if not strict:
        return None
    expected_employer = metadata.get("expected_employer")
    employer_field_id = metadata.get("employer_field_id")
    empty_state_text = metadata.get("empty_state_text")
    if (
        not isinstance(expected_employer, str)
        or not expected_employer.strip()
        or len(expected_employer) > 256
        or "\x00" in expected_employer
    ):
        raise ValueError("Umantis strict_listing_contract requires expected_employer")
    if (
        not isinstance(employer_field_id, str)
        or re.fullmatch(r"column_value_[1-9]\d{0,11}", employer_field_id) is None
    ):
        raise ValueError("Umantis strict_listing_contract requires a bounded employer_field_id")
    if (
        not isinstance(empty_state_text, str)
        or not empty_state_text.strip()
        or len(empty_state_text) > 256
        or "\x00" in empty_state_text
    ):
        raise ValueError("Umantis strict_listing_contract requires empty_state_text")
    return (
        expected_employer.strip(),
        employer_field_id,
        empty_state_text.strip(),
    )


def _validate_navigation_page(
    navigation: _Navigation,
    parsed_jobs: list[_ParsedJob],
    *,
    expected_page: int,
    expected_first: int,
    expected_total: int | None,
) -> dict[str, _ParsedJob]:
    """Prove one advertised Umantis range against unique vacancy IDs."""
    if navigation.page != expected_page:
        raise ValueError("Umantis navigation page did not advance monotonically")
    if navigation.total == 0:
        if navigation.first != 0 or navigation.last != 0 or parsed_jobs:
            raise ValueError("Umantis zero navigation contradicted the listing rows")
        if expected_page != 1:
            raise ValueError("Umantis zero navigation appeared after the first page")
        return {}
    if expected_total is not None and navigation.total != expected_total:
        raise ValueError("Umantis advertised total changed during pagination")
    if navigation.first != expected_first or not (
        1 <= navigation.first <= navigation.last <= navigation.total
    ):
        raise ValueError("Umantis navigation range did not advance monotonically")
    unique = _deduplicate_vacancies(parsed_jobs)
    advertised_page_size = navigation.last - navigation.first + 1
    if len(unique) != advertised_page_size:
        raise ValueError("Umantis listing row count did not match its advertised navigation range")
    return unique


def _validated_next_url(
    navigation: _Navigation,
    current_url: str,
    base_url: str,
) -> str:
    """Validate the exact token-bearing same-origin provider next link."""
    if not navigation.next_active or navigation.next_url is None:
        raise ValueError("Umantis navigation omitted an active next-page link")
    candidate = urlparse(urljoin(current_url, unescape(navigation.next_url)))
    base = urlparse(base_url)
    current = urlparse(current_url)
    if (
        candidate.scheme.casefold() != base.scheme.casefold()
        or candidate.netloc.casefold() != base.netloc.casefold()
        or candidate.username is not None
        or candidate.password is not None
        or candidate.path != current.path
    ):
        raise ValueError("Umantis navigation next link crossed its configured listing origin")
    pairs = parse_qsl(candidate.query, keep_blank_values=True)
    page_key = f"tc{navigation.table_nr}"
    token_key = f"_search_token{navigation.table_nr}"
    page_values = [value for key, value in pairs if key == page_key]
    token_values = [value for key, value in pairs if key == token_key]
    if page_values != [f"p{navigation.page + 1}"]:
        raise ValueError("Umantis navigation next link did not identify the next page")
    if len(token_values) != 1 or re.fullmatch(r"[1-9]\d{0,63}", token_values[0]) is None:
        raise ValueError("Umantis navigation next link omitted its bounded search token")
    return urlunparse(candidate._replace(fragment=""))


async def _validate_detail_ownership(
    client: httpx.AsyncClient,
    jobs: list[DiscoveredJob],
    expected_employer: str,
) -> None:
    """Require every detail document's page-level metadata to name its owner."""
    for job in jobs:
        html = await _get_page_with_retry(client, job.url)
        parser = _DetailOwnerParser()
        if html is not None:
            parser.feed(html)
            parser.close()
        if (
            parser.head_count != 1
            or not parser.structurally_complete
            or parser.outside_head_descriptions
            or len(parser.descriptions) != 1
        ):
            raise ValueError(
                f"Umantis detail metadata did not identify the exact configured employer: {job.url}"
            )
        owners = []
        for description in parser.descriptions:
            # These provider documents declare ``Employer - locale copy`` in
            # the page-level description. Compare the complete owner segment,
            # never arbitrary title/body text.
            owner = re.split(r"\s+[-–—]\s+", description, maxsplit=1)[0]
            owners.append(_normalized_identity(owner))
        if owners != [_normalized_identity(expected_employer)]:
            raise ValueError(
                f"Umantis detail metadata did not identify the exact configured employer: {job.url}"
            )


# ── Pagination fetch with retries ───────────────────────────────────────


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> str | None:
    """GET an Umantis pagination page with bounded retries (#2747)."""
    return await fetch_text_page_with_retry(
        client,
        url,
        retries=retries,
        base_delay=base_delay,
        follow_redirects=True,
        log_event="umantis.page_backoff",
        sleep=asyncio.sleep,
    )


# ── Discovery ──────────────────────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover partial rich jobs from an Umantis board.

    Paginates through /Jobs/All using tc{tableNr}=p{page} params.
    Returns URL, title, location, and employment type from listing rows. A
    detail scraper remains responsible for the description.
    """
    metadata = board.get("metadata") or {}
    customer_id = metadata.get("customer_id")
    region = metadata.get("region", "")
    cname = metadata.get("cname")

    if not customer_id:
        # Try to extract from board URL
        cid, reg = _parse_host(board["board_url"])
        if cid:
            customer_id = cid
            if reg is not None:
                region = reg
        else:
            # Check for CNAME .umantis.com domain
            host = (urlparse(board["board_url"]).hostname or "").lower()
            if host.endswith(".umantis.com"):
                cname = host
            else:
                raise ValueError(
                    f"Umantis monitor requires 'customer_id' in metadata "
                    f"for board {board['board_url']!r}"
                )

    if cname:
        parsed = urlparse(board["board_url"])
        base = f"{parsed.scheme}://{cname}"
    else:
        assert isinstance(customer_id, str)
        base = _base_url(customer_id, region)
    listing_path = metadata.get("listing_path", "/Jobs/All")

    listing_url = f"{base}{listing_path}"
    strict_contract = _strict_contract(metadata)
    expected_employer = strict_contract[0] if strict_contract else None
    employer_field_id = strict_contract[1] if strict_contract else None
    empty_state_text = strict_contract[2] if strict_contract else None

    # Umantis stores listing filters in cookies. A nested client over the same
    # caller-owned transport gives every discovery an independent cookie jar.
    async with _isolated_client(
        client,
        expected_origin=base,
    ) as session:
        resp = await session.get(listing_url, follow_redirects=True)
        resp.raise_for_status()
        response_url = urlparse(str(resp.url))
        base_parts = urlparse(base)
        if (
            response_url.scheme.casefold() != base_parts.scheme.casefold()
            or response_url.netloc.casefold() != base_parts.netloc.casefold()
        ):
            raise ValueError("Umantis listing redirected across the configured origin")
        html = resp.text
        parsed_jobs = _parse_parsed_jobs_from_html(
            html,
            base,
            expected_employer=expected_employer,
            employer_field_id=employer_field_id,
        )

        if strict_contract is not None:
            navigation = _extract_navigation(html)
            if navigation is None:
                raise ValueError("Umantis strict listing omitted navigation metadata")
            unique = _validate_navigation_page(
                navigation,
                parsed_jobs,
                expected_page=1,
                expected_first=0 if navigation.total == 0 else 1,
                expected_total=None,
            )
            if navigation.total == 0:
                assert empty_state_text is not None
                if not _has_visible_text(html, empty_state_text):
                    raise ValueError(
                        "Umantis zero listing omitted its explicit visible empty state"
                    )
            else:
                current_url = str(resp.url)
                expected_page = 2
                expected_first = navigation.last + 1
                while navigation.last < navigation.total:
                    if expected_page > MAX_PAGES:
                        raise ValueError("Umantis pagination exceeded the safe page cap")
                    next_url = _validated_next_url(navigation, current_url, base)
                    page_html = await _get_page_with_retry(session, next_url)
                    if page_html is None:
                        raise ValueError("Umantis advertised next page returned a terminal status")
                    page_navigation = _extract_navigation(page_html)
                    if page_navigation is None:
                        raise ValueError("Umantis pagination page omitted navigation metadata")
                    page_jobs = _parse_parsed_jobs_from_html(
                        page_html,
                        base,
                        expected_employer=expected_employer,
                        employer_field_id=employer_field_id,
                    )
                    page_unique = _validate_navigation_page(
                        page_navigation,
                        page_jobs,
                        expected_page=expected_page,
                        expected_first=expected_first,
                        expected_total=navigation.total,
                    )
                    overlap = set(unique) & set(page_unique)
                    if overlap:
                        raise ValueError(
                            "Umantis pagination repeated provider vacancy IDs: "
                            + ", ".join(sorted(overlap))
                        )
                    unique.update(page_unique)
                    current_url = next_url
                    navigation = page_navigation
                    expected_page += 1
                    expected_first = navigation.last + 1
                if len(unique) != navigation.total:
                    raise ValueError(
                        "Umantis final unique vacancy count did not match advertised total"
                    )

            rich_results = [parsed.job for parsed in unique.values()]
            if expected_employer is not None:
                await _validate_detail_ownership(session, rich_results, expected_employer)
            label = cname or customer_id
            log.info("umantis.listed", customer_id=label, jobs=len(rich_results))
            if _uses_rich_listing_results(metadata):
                return rich_results
            return {job.url for job in rich_results}

        # Legacy boards retain their tolerant end-of-pagination behavior, but
        # locale aliases are still deduplicated by numeric provider identity.
        table_nr = _extract_table_nr(html)
        truncated = False
        if table_nr:
            page = 2
            while page <= MAX_PAGES:
                if len(parsed_jobs) >= MAX_JOBS:
                    truncated = True
                    break
                page_url = _pagination_url(listing_url, table_nr, page)
                page_html = await _get_page_with_retry(session, page_url)
                if page_html is None:
                    break
                page_jobs = _parse_parsed_jobs_from_html(page_html, base)
                if not page_jobs:
                    break
                new_ids = {job.vacancy_id for job in page_jobs}
                existing_ids = {job.vacancy_id for job in parsed_jobs}
                if not (new_ids - existing_ids):
                    break
                parsed_jobs.extend(page_jobs)
                page += 1
            else:
                truncated = True

        label = cname or customer_id
        if not parsed_jobs:
            log.info("umantis.no_jobs", customer_id=label)
            return set()

        unique = _deduplicate_vacancies(parsed_jobs)
        rich_results = [parsed.job for parsed in unique.values()]
        log.info("umantis.listed", customer_id=label, jobs=len(rich_results))
        if _uses_rich_listing_results(metadata):
            if truncated:
                log.warning("umantis.truncated", total=len(parsed_jobs), cap=MAX_JOBS)
                return truncated_rich_result(rich_results)
            return rich_results

        urls = {job.url for job in rich_results}
        if truncated:
            log.warning("umantis.truncated", total=len(parsed_jobs), cap=MAX_JOBS)
            return truncated_url_result(urls)
        return urls


# ── Probing ─────────────────────────────────────────────────────────────


async def _probe_listing(
    customer_id: str,
    region: str,
    client: httpx.AsyncClient,
    listing_path: str = "/Jobs/All",
) -> int | None:
    """Probe a listing page and return job count, or None if not found."""
    base = _base_url(customer_id, region)
    try:
        resp = await client.get(f"{base}{listing_path}", follow_redirects=True)
        if resp.status_code != 200:
            return None
        jobs = _parse_jobs_from_html(resp.text, base)
        if jobs:
            return len(jobs)
        # Page loaded but no jobs found — might still be valid
        if "umantis" in resp.text.lower():
            return 0
        return None
    except Exception:
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Umantis: URL pattern match or HTML marker scan."""
    # 1. URL pattern match
    cid, region = _parse_host(url)
    if cid:
        listing_path = _listing_path_from_url(url)
        result: dict = {"customer_id": cid, "region": region or ""}
        if listing_path and listing_path != "/Jobs/All":
            result["listing_path"] = listing_path
        if client:
            count = await _probe_listing(
                cid,
                region or "",
                client,
                listing_path or "/Jobs/All",
            )
            if count is not None:
                if count > 0:
                    result["jobs"] = count
                return result
        return result

    # 2. Check for custom CNAME (.umantis.com but not recruitingapp-{ID})
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".umantis.com"):
        sub = host.removesuffix(".umantis.com").split(".")[-1]
        if sub and sub not in _IGNORE_SUBDOMAINS:
            if not client:
                return None
            html = await fetch_page_text(url, client)
            if not html:
                return None
            # Try to find recruitingapp-{ID} reference in page
            m = re.search(r"recruitingapp-(\d+)", html)
            if m:
                cid = m.group(1)
                reg_match = re.search(r"recruitingapp-\d+\.(\w+)\.umantis\.com", html)
                region = reg_match.group(1) if reg_match else ""
                listing_path = _embedded_listing_path(html, cid)
                count = await _probe_listing(
                    cid,
                    region,
                    client,
                    listing_path or "/Jobs/All",
                )
                result = {"customer_id": cid, "region": region}
                if listing_path and listing_path != "/Jobs/All":
                    result["listing_path"] = listing_path
                if count is not None and count > 0:
                    result["jobs"] = count
                return result
            # No recruitingapp reference — CNAME serves directly
            has_marker = any(marker.search(html) for marker in _PAGE_MARKERS)
            if has_marker:
                parsed = urlparse(url)
                base = f"{parsed.scheme}://{parsed.hostname}"
                jobs = _parse_jobs_from_html(html, base)
                result = {"customer_id": sub, "cname": host, "region": ""}
                if jobs:
                    result["jobs"] = len(jobs)
                return result
            return None

    # 3. HTML marker scan (for career pages embedding Umantis)
    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if not html:
        return None

    has_marker = any(marker.search(html) for marker in _PAGE_MARKERS)
    if not has_marker:
        return None

    # Try to extract customer ID from the page
    m = re.search(r"recruitingapp-(\d+)", html)
    if not m:
        return None

    cid = m.group(1)
    reg_match = re.search(r"recruitingapp-\d+\.(\w+)\.umantis\.com", html)
    region = reg_match.group(1) if reg_match else ""
    listing_path = _embedded_listing_path(html, cid)
    count = await _probe_listing(
        cid,
        region,
        client,
        listing_path or "/Jobs/All",
    )
    result = {"customer_id": cid, "region": region}
    if listing_path and listing_path != "/Jobs/All":
        result["listing_path"] = listing_path
    if count is not None and count > 0:
        result["jobs"] = count
    return result


register("umantis", discover, cost=15, can_handle=can_handle)
