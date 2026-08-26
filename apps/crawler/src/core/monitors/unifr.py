"""University of Fribourg authoritative vacancy inventories.

The University does not expose one locale-complete public feed.  Its central
HR widget publishes different (overlapping) numeric vacancy IDs in French and
German, while several faculties publish bounded first-party inventories.  This
monitor keeps those source contracts explicit and fails closed when a page can
no longer prove that the complete inventory was observed.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import DiscoveredJob, register
from src.shared.http_retry import fetch_text_page_with_retry

if TYPE_CHECKING:
    import httpx

_MAX_LISTING_BYTES = 2_000_000
_MAX_DETAIL_BYTES = 2_000_000
_MAX_CENTRAL_ITEMS = 100
_MAX_SOURCE_ITEMS = 50
_DETAIL_CONCURRENCY = 8

_CENTRAL_FR = "https://www.unifr.ch/sp/fr/postes-vacants.html"
_CENTRAL_DE = "https://www.unifr.ch/sp/de/offene-stellen.html"
_CENTRAL_CANONICAL = _CENTRAL_FR
_DETAIL_ROOT = "https://webapps.unifr.ch/sp/ws/b49e151b85b415a7201e88d3e9ecf54f11d7589e/detail"
_LOCALES = {
    "fr": (_CENTRAL_FR, "Université"),
    "de": (_CENTRAL_DE, "Universität"),
}
_NUMERIC_ID_RE = re.compile(r"^[1-9][0-9]{0,9}$")
_DEADLINE_RE = re.compile(
    r"(?i)\bby\s+"
    r"(?P<month>january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+"
    r"(?P<day>[0-3]?[0-9])(?:st|nd|rd|th)?(?:,\s*(?P<year>20[0-9]{2}))?"
)
_GEO_DEADLINE_RE = re.compile(
    r"(?i)\bbefore\s+(?P<day>[0-3]?[0-9])(?:st|nd|rd|th)?\s+of\s+"
    r"(?P<month>january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+(?P<year>20[0-9]{2})"
)
_GERMAN_DEADLINE_RE = re.compile(
    r"(?i)\bbewerbungsfrist:\s*(?P<day>[0-3]?[0-9])\.\s*"
    r"(?P<month>januar|februar|märz|april|mai|juni|juli|august|september|"
    r"oktober|november|dezember)\s+(?P<year>20[0-9]{2})"
)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "oktober": 10,
    "dezember": 12,
}


@dataclass(frozen=True, slots=True)
class _AccordionSource:
    url: str
    page_title_suffix: str
    heading: str
    expected_ids: frozenset[str]
    excluded_central_ids: dict[str, str]
    list_selector: str = "main ul.accordion.brandedstyle"
    deadline_required: frozenset[str] = frozenset()
    immediately_available: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _LinkSource:
    url: str
    page_title_suffix: str
    heading: str
    path_prefix: str
    rewrite_from: str | None = None
    rewrite_to: str | None = None


_ACCORDION_SOURCES = {
    "chemistry": _AccordionSource(
        url="https://www.unifr.ch/chem/en/department/jobs.html",
        page_title_suffix="| Department of Chemistry | University of Fribourg",
        heading="Open Positions",
        expected_ids=frozenset({"styleguide-2-1", "styleguide-2-2"}),
        excluded_central_ids={},
    ),
    "ami": _AccordionSource(
        url="https://www.ami.swiss/en/about-us/join/open-positions.html",
        page_title_suffix="| Adolphe Merkle Institute | Université de Fribourg",
        heading="Open positions",
        expected_ids=frozenset({"styleguide-3-1", "styleguide-3-2", "styleguide-3-3"}),
        excluded_central_ids={},
        deadline_required=frozenset({"styleguide-3-2", "styleguide-3-3"}),
        immediately_available=frozenset({"styleguide-3-1"}),
    ),
    "biology": _AccordionSource(
        url="https://www.unifr.ch/bio/en/department/jobs.html",
        page_title_suffix="| Department of Biology | University of Fribourg",
        heading="Open Positions",
        expected_ids=frozenset(
            {
                "styleguide-5-1",
                "styleguide-5-2D3",
                "styleguide-5-3",
                "styleguide-5-4",
                "styleguide-5-5",
            }
        ),
        excluded_central_ids={},
    ),
    "geosciences": _AccordionSource(
        url="https://www.unifr.ch/geo/en/department/jobs/",
        page_title_suffix="| Department of Geosciences | University of Fribourg",
        heading="Open Positions",
        expected_ids=frozenset({"styleguide-2-1", "styleguide-2-2"}),
        excluded_central_ids={},
        deadline_required=frozenset({"styleguide-2-1"}),
    ),
    "physics": _AccordionSource(
        url="https://www.unifr.ch/phys/de/department/jobs/",
        page_title_suffix="| Departement für Physik | Universität Freiburg",
        heading="Freie Stellen im Physikdepartement",
        expected_ids=frozenset({"styleguide-2-1", "styleguide-2-2"}),
        excluded_central_ids={"styleguide-2-2": "1911"},
        deadline_required=frozenset({"styleguide-2-1"}),
    ),
    "ses": _AccordionSource(
        url="https://www.unifr.ch/ses/en/fac/jobs.html",
        page_title_suffix=(
            "| Faculty of Management, Economics and Social Sciences | University of Fribourg"
        ),
        heading="Open positions",
        expected_ids=frozenset({"styleguide-2-1", "styleguide-2-2"}),
        excluded_central_ids={"styleguide-2-2": "1898"},
        list_selector="main ul.accordion.light",
    ),
}

_LINK_SOURCES = {
    "law": _LinkSource(
        url="https://www.unifr.ch/ius/de/fakultaet/stellen.html",
        page_title_suffix="| Rechtswissenschaftliche Fakultät | Universität Freiburg",
        heading="Stellenangebote",
        path_prefix="/ius/de/assets/public/documents/offres-emploi/",
    ),
    "regional-school-service": _LinkSource(
        url="https://www.unifr.ch/rsd/de/schuldienst/jobs.html",
        page_title_suffix="| Regionaler Schuldienst | Universität Freiburg",
        heading="Offene Stellen",
        path_prefix="/rsd/de/assets/public/files/",
        rewrite_from="/rsd/de/schuldienst/assets/public/files/",
        rewrite_to="/rsd/de/assets/public/files/",
    ),
}


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _canonical_job_url(board_url: str, identity: str) -> str:
    parsed = urlparse(board_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["_jid"] = [identity]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


async def _fetch_bounded(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    expected_type: str,
) -> str:
    headers: dict[str, str] = {}
    text = await fetch_text_page_with_retry(
        client,
        url,
        follow_redirects=False,
        end_of_pagination_statuses=set(),
        require_nonempty=True,
        max_bytes=max_bytes,
        response_headers=headers,
    )
    if text is None:
        raise ValueError(f"University of Fribourg source returned no response: {url}")
    content_type = headers.get("content-type", "").partition(";")[0].strip().casefold()
    if expected_type == "html":
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("University of Fribourg listing did not return HTML")
    elif content_type != "application/json" and not content_type.endswith("+json"):
        raise ValueError("University of Fribourg detail did not return JSON")
    return text


def _assert_page_contract(tree: LexborHTMLParser, *, suffix: str, heading: str) -> None:
    title = tree.css_first("title")
    if title is None or not _normalized_text(title.text()).endswith(suffix):
        raise ValueError("University of Fribourg source ownership marker is missing")
    headings = [
        node for node in tree.css("main h1, main h2") if _normalized_text(node.text()) == heading
    ]
    if len(headings) != 1:
        raise ValueError("University of Fribourg source heading is missing or ambiguous")
    if tree.css("main .pagination, main ul.pagination, main a[rel='next'], link[rel='next']"):
        raise ValueError("University of Fribourg source unexpectedly exposes pagination")


def _parse_central_listing(html: str, locale: str) -> dict[str, str]:
    tree = LexborHTMLParser(html)
    heading = (
        "Postes vacants - Offres d'emploi à l'Université de Fribourg"
        if locale == "fr"
        else "Offene Stellen - Stellenangebote an der Universität Freiburg"
    )
    suffix = (
        "| Service du personnel | Université de Fribourg"
        if locale == "fr"
        else "| Personaldienst | Universität Freiburg"
    )
    _assert_page_contract(tree, suffix=suffix, heading=heading)
    shells = tree.css("main ul.list-group.list")
    if len(shells) != 1:
        raise ValueError("University of Fribourg central inventory shell is missing or ambiguous")
    nodes = tree.css("main ul.list-group.list > li.list-group-item")
    if not nodes:
        raise ValueError("University of Fribourg central inventory cannot prove a safe zero")
    if len(nodes) > _MAX_CENTRAL_ITEMS:
        raise ValueError("University of Fribourg central inventory exceeded its item limit")
    result: dict[str, str] = {}
    for node in nodes:
        identifier = node.attributes.get("id") or ""
        if _NUMERIC_ID_RE.fullmatch(identifier) is None:
            raise ValueError("University of Fribourg central listing has an invalid provider ID")
        titles = node.css("h4.list-group-item-heading.name")
        controls = node.css(f"div#open{identifier}")
        if len(titles) != 1 or len(controls) != 1:
            raise ValueError("University of Fribourg central card boundary is malformed")
        title = _normalized_text(titles[0].text())
        if not title:
            raise ValueError("University of Fribourg central listing has an empty title")
        if identifier in result:
            raise ValueError("University of Fribourg central listing has a duplicate provider ID")
        result[identifier] = title
    return result


def _parse_iso_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"University of Fribourg detail has invalid {field}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"University of Fribourg detail has invalid {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"University of Fribourg detail has timezone-free {field}")
    return parsed


def _parse_central_detail(
    text: str,
    *,
    identifier: str,
    locale: str,
    listing_title: str,
    today: date,
) -> dict[str, str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("University of Fribourg detail is malformed JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("University of Fribourg detail must be an object")
    payload_id = payload.get("id")
    if isinstance(payload_id, int):
        payload_id = str(payload_id)
    if payload_id != identifier:
        raise ValueError("University of Fribourg detail ID does not match the requested vacancy")
    expected_url, expected_owner = _LOCALES[locale]
    if payload.get("autorite") != expected_owner:
        raise ValueError("University of Fribourg detail authority does not match the University")
    if payload.get("link") != f"{expected_url}#{identifier}":
        raise ValueError("University of Fribourg detail link does not match its listing identity")
    values: dict[str, str] = {}
    for field in ("fonction", "content", "startpublish", "endpublish"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"University of Fribourg detail has invalid {field}")
        values[field] = value.strip()
    if _normalized_text(values["fonction"]) != listing_title:
        raise ValueError("University of Fribourg listing and detail titles disagree")
    start = _parse_iso_datetime(values["startpublish"], "startpublish")
    end = _parse_iso_datetime(values["endpublish"], "endpublish")
    if end < start or end.astimezone(UTC).date() < today:
        raise ValueError("University of Fribourg central listing contains an expired vacancy")
    values["startpublish"] = start.isoformat()
    values["endpublish"] = end.date().isoformat()
    return values


async def _central_jobs(client: httpx.AsyncClient, today: date) -> list[DiscoveredJob]:
    listings = await asyncio.gather(
        *(
            _fetch_bounded(
                client,
                listing_url,
                max_bytes=_MAX_LISTING_BYTES,
                expected_type="html",
            )
            for listing_url, _owner in _LOCALES.values()
        )
    )
    by_locale = {
        locale: _parse_central_listing(html, locale)
        for locale, html in zip(_LOCALES, listings, strict=True)
    }
    union = set().union(*(values.keys() for values in by_locale.values()))
    if not union:
        raise ValueError("University of Fribourg central locale union cannot prove a safe zero")

    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def fetch_detail(identifier: str, locale: str) -> tuple[str, str, dict[str, str]]:
        async with semaphore:
            text = await _fetch_bounded(
                client,
                f"{_DETAIL_ROOT}/{locale}/{identifier}",
                max_bytes=_MAX_DETAIL_BYTES,
                expected_type="json",
            )
        return (
            identifier,
            locale,
            _parse_central_detail(
                text,
                identifier=identifier,
                locale=locale,
                listing_title=by_locale[locale][identifier],
                today=today,
            ),
        )

    details = await asyncio.gather(
        *(
            fetch_detail(identifier, locale)
            for identifier in sorted(union, key=int)
            for locale in _LOCALES
            if identifier in by_locale[locale]
        )
    )
    grouped: dict[str, dict[str, dict[str, str]]] = {identifier: {} for identifier in union}
    for identifier, locale, detail in details:
        grouped[identifier][locale] = detail

    jobs: list[DiscoveredJob] = []
    for identifier in sorted(grouped, key=int):
        localized = grouped[identifier]
        primary_locale = "fr" if "fr" in localized else "de"
        primary = localized[primary_locale]
        jobs.append(
            DiscoveredJob(
                url=_canonical_job_url(_CENTRAL_CANONICAL, identifier),
                title=primary["fonction"],
                description=primary["content"],
                locations=["Fribourg, Switzerland"],
                date_posted=primary["startpublish"],
                language=primary_locale,
                localizations={
                    locale: {
                        "title": value["fonction"],
                        "description": value["content"],
                        "locations": ["Fribourg, Switzerland"],
                    }
                    for locale, value in sorted(localized.items())
                },
                extras={"valid_through": primary["endpublish"]},
                metadata={"unifr_provider_id": identifier},
            )
        )
    return jobs


def _deadline_from_text(text: str) -> date | None:
    matches: list[tuple[int, int, int]] = []
    for pattern in (_DEADLINE_RE, _GEO_DEADLINE_RE, _GERMAN_DEADLINE_RE):
        for match in pattern.finditer(text):
            year = match.groupdict().get("year")
            if year is None:
                years = set(re.findall(r"\b20[0-9]{2}\b", text))
                if len(years) != 1:
                    raise ValueError("University of Fribourg source deadline year is ambiguous")
                year = years.pop()
            month = _MONTHS.get(match.group("month").casefold())
            if month is None:
                raise ValueError("University of Fribourg source deadline month is invalid")
            matches.append((int(year), month, int(match.group("day"))))
    if not matches:
        return None
    unique = set(matches)
    if len(unique) != 1:
        raise ValueError("University of Fribourg source has ambiguous deadlines")
    try:
        return date(*unique.pop())
    except ValueError as exc:
        raise ValueError("University of Fribourg source deadline is invalid") from exc


async def _central_ids(client: httpx.AsyncClient) -> frozenset[str]:
    pages = await asyncio.gather(
        *(
            _fetch_bounded(
                client,
                listing_url,
                max_bytes=_MAX_LISTING_BYTES,
                expected_type="html",
            )
            for listing_url, _owner in _LOCALES.values()
        )
    )
    by_locale = {
        locale: _parse_central_listing(html, locale)
        for locale, html in zip(_LOCALES, pages, strict=True)
    }
    return frozenset().union(*(values.keys() for values in by_locale.values()))


async def _accordion_jobs(
    client: httpx.AsyncClient,
    source: _AccordionSource,
    today: date,
) -> list[DiscoveredJob]:
    html = await _fetch_bounded(
        client,
        source.url,
        max_bytes=_MAX_LISTING_BYTES,
        expected_type="html",
    )
    tree = LexborHTMLParser(html)
    _assert_page_contract(tree, suffix=source.page_title_suffix, heading=source.heading)
    lists = tree.css(source.list_selector)
    if len(lists) != 1:
        raise ValueError("University of Fribourg accordion inventory is missing or ambiguous")
    controls = lists[0].css("a[data-accordion-toggler]")
    if not controls:
        raise ValueError("University of Fribourg accordion inventory cannot prove a safe zero")
    if len(controls) > _MAX_SOURCE_ITEMS:
        raise ValueError("University of Fribourg accordion inventory exceeded its item limit")
    raw_ids = [control.attributes.get("data-accordion-toggler") or "" for control in controls]
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("University of Fribourg accordion has a duplicate ID")

    items: dict[str, tuple[str, str]] = {}
    for control in controls:
        identifier = control.attributes.get("data-accordion-toggler", "")
        if not identifier or len(identifier) > 128 or identifier in items:
            raise ValueError("University of Fribourg accordion has an invalid or duplicate ID")
        panels = lists[0].css(f"div[data-accordion-content='{identifier}']")
        if len(panels) != 1:
            raise ValueError("University of Fribourg accordion ID has no unique detail panel")
        title = _normalized_text(control.text())
        description = panels[0].html or ""
        if not title or not _normalized_text(panels[0].text()):
            raise ValueError("University of Fribourg accordion item is incomplete")
        items[identifier] = (title, description)
    if set(items) != source.expected_ids:
        raise ValueError("University of Fribourg accordion source inventory drifted")

    if source.excluded_central_ids:
        central_ids = await _central_ids(client)
        missing = set(source.excluded_central_ids.values()) - central_ids
        if missing:
            raise ValueError(
                "University of Fribourg departmental duplicate is not present centrally"
            )

    jobs: list[DiscoveredJob] = []
    for identifier in sorted(items):
        if identifier in source.excluded_central_ids:
            continue
        title, description = items[identifier]
        plain = _normalized_text(LexborHTMLParser(description).text())
        deadline = _deadline_from_text(plain)
        if identifier in source.deadline_required and deadline is None:
            raise ValueError("University of Fribourg source omitted a required deadline")
        if identifier in source.immediately_available and "available immediately" not in plain:
            raise ValueError("University of Fribourg source currentness marker is missing")
        if deadline is not None and deadline < today:
            continue
        jobs.append(
            DiscoveredJob(
                url=_canonical_job_url(source.url, identifier),
                title=title,
                description=description,
                locations=["Fribourg, Switzerland"],
                language="de" if "/de/" in source.url else "en",
                extras={"valid_through": deadline.isoformat()} if deadline else None,
                metadata={"unifr_source_id": identifier},
            )
        )
    if not jobs:
        raise ValueError("University of Fribourg source cannot prove a safe current zero")
    return jobs


async def _link_inventory(client: httpx.AsyncClient, source: _LinkSource) -> set[str]:
    html = await _fetch_bounded(
        client,
        source.url,
        max_bytes=_MAX_LISTING_BYTES,
        expected_type="html",
    )
    tree = LexborHTMLParser(html)
    _assert_page_contract(tree, suffix=source.page_title_suffix, heading=source.heading)
    nodes = [
        node
        for node in tree.css("main a[href]")
        if urlparse(urljoin(source.url, node.attributes.get("href") or ""))
        .path.casefold()
        .endswith(".pdf")
    ]
    if not nodes:
        raise ValueError("University of Fribourg link inventory cannot prove a safe zero")
    if len(nodes) > _MAX_SOURCE_ITEMS:
        raise ValueError("University of Fribourg link inventory exceeded its item limit")
    urls: set[str] = set()
    for node in nodes:
        raw = node.attributes.get("href")
        if not raw:
            raise ValueError("University of Fribourg link inventory contains an empty href")
        url = urljoin(source.url, raw)
        if source.rewrite_from and source.rewrite_to:
            url = url.replace(source.rewrite_from, source.rewrite_to)
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.unifr.ch"
            or not parsed.path.startswith(source.path_prefix)
            or not parsed.path.casefold().endswith(".pdf")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("University of Fribourg link escaped its authoritative inventory")
        if url in urls:
            raise ValueError("University of Fribourg link inventory contains a duplicate URL")
        urls.add(url)
    return urls


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | set[str]:
    """Discover one fixed University of Fribourg source contract."""
    metadata = board.get("metadata") or {}
    if set(metadata) != {"source"} or not isinstance(metadata.get("source"), str):
        raise ValueError("unifr monitor requires exactly one named source")
    source_name = metadata["source"]
    today = datetime.now(UTC).date()
    if source_name == "central":
        if board["board_url"] != _CENTRAL_CANONICAL:
            raise ValueError("unifr central board URL is not canonical")
        return await _central_jobs(client, today)
    if source_name in _ACCORDION_SOURCES:
        source = _ACCORDION_SOURCES[source_name]
        if board["board_url"] != source.url:
            raise ValueError("unifr accordion board URL does not match its named source")
        return await _accordion_jobs(client, source, today)
    if source_name in _LINK_SOURCES:
        source = _LINK_SOURCES[source_name]
        if board["board_url"] != source.url:
            raise ValueError("unifr link board URL does not match its named source")
        return await _link_inventory(client, source)
    raise ValueError(f"unknown University of Fribourg source: {source_name!r}")


register("unifr", discover, cost=10, rich=True)
