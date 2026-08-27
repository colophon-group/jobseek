"""Unisanté first-party careers monitor.

The public listing has two equivalent routes and mixes numeric detail slugs
with evergreen, title-only slugs.  This monitor treats the displayed
``Référence`` value as the provider identity, validates both listing aliases,
and reads the visible detail body because the site's JSON-LD description is
mojibake-corrupted and sometimes incomplete.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import DiscoveredJob, register
from src.core.monitors.dom import _raise_if_bot_challenge
from src.shared.html_normalize import normalize_description_html
from src.shared.http_retry import fetch_text_page_with_retry

_ORIGIN = "https://emploi.unisante.ch"
_LISTING_URLS = (f"{_ORIGIN}/index.php/offres", f"{_ORIGIN}/offres")
_DETAIL_PREFIX = "/index.php/offre/"
_MAX_JOBS = 50
_MAX_LISTING_BYTES = 512 * 1024
_MAX_DETAIL_BYTES = 1024 * 1024
_DETAIL_CONCURRENCY = 3
_MIN_DESCRIPTION_CHARS = 300
_TRANSIENT_STATUSES = frozenset({202, 401, 403, 429})
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_REFERENCE_RE = re.compile(r"\bRéférence\s*:\s*([1-9]\d{0,8})\b", re.IGNORECASE)
_DEADLINE_LABEL_RE = re.compile(
    r"\bDélai\s+de\s+postulation"
    r"(?:\s*/\s*Application\s+deadline)?\s*:\s*",
    re.IGNORECASE,
)
_DATE_PREFIX_RE = re.compile(
    r"(?P<value>"
    r"\d{1,2}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{4}"
    r"|\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+\d{4}"
    r")\b"
)
_MOJIBAKE_MARKERS = ("Ã", "â€™", "â€“", "â€”", "\ufffd")
_FRENCH_MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}


@dataclass(frozen=True, slots=True)
class _ListingJob:
    slug: str
    title: str

    @property
    def detail_url(self) -> str:
        return f"{_ORIGIN}{_DETAIL_PREFIX}{self.slug}"


class _JsonLdParser(HTMLParser):
    """Collect standards-compliant JSON-LD blocks without trusting content."""

    def __init__(self) -> None:
        super().__init__()
        self._active = False
        self._parts: list[str] = []
        self.blocks: list[object] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value for key, value in attrs}
        if tag.lower() == "script" and (attributes.get("type") or "").lower() == (
            "application/ld+json"
        ):
            self._active = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._active:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._active:
            return
        self._active = False
        raw = "".join(self._parts).strip()
        if not raw:
            return
        try:
            self.blocks.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ValueError("Unisanté detail returned malformed JSON-LD") from exc


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _fold(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def _today() -> date:
    return datetime.now(ZoneInfo("Europe/Zurich")).date()


def _is_official_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "emploi.unisante.ch"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _slug_from_href(raw_href: str, listing_url: str) -> str | None:
    candidate = urljoin(listing_url, raw_href)
    if not _is_official_url(candidate):
        return None
    parsed = urlparse(candidate)
    if parsed.query or parsed.fragment:
        return None
    match = re.fullmatch(r"/(?:index\.php/)?offre/([^/]+)/?", parsed.path)
    if match is None or _SLUG_RE.fullmatch(match.group(1)) is None:
        return None
    return match.group(1)


def _normalized_text(node: Any) -> str:
    return " ".join(node.text(separator=" ", strip=True).split())


def _is_explicitly_hidden(node: Any) -> bool:
    """Return whether the provider explicitly hides an empty-state node.

    ``#no-ads`` is permanently present on ordinary non-empty pages and the
    category-filter JavaScript reveals it only when a selected category has no
    matching cards.  Its text therefore proves nothing by itself.  A zero
    inventory is authoritative only when the server renders that exact marker
    visible instead of returning the normal hidden template shell.
    """
    attributes = {key.lower(): value for key, value in node.attributes.items()}
    classes = set((attributes.get("class") or "").split())
    style = re.sub(r"\s+", "", (attributes.get("style") or "").casefold())
    aria_hidden = (attributes.get("aria-hidden") or "").strip().casefold()
    return (
        "hidden" in attributes
        or "d-none" in classes
        or aria_hidden == "true"
        or re.search(r"(?:^|;)display:none(?:!important)?(?:;|$)", style) is not None
    )


def _parse_listing(html: str, listing_url: str) -> dict[str, _ListingJob]:
    _raise_if_bot_challenge(listing_url, html)
    document = LexborHTMLParser(html)
    main = document.css_first("main#main")
    if main is None:
        raise ValueError("Unisanté listing omitted main#main")
    heading = main.css_first("h1")
    if heading is None or _normalized_text(heading) != "Nos offres d'emploi":
        raise ValueError("Unisanté listing heading changed")
    if main.css_first(".row.offres-items") is None:
        raise ValueError("Unisanté listing omitted the offers inventory")
    all_option = main.css_first("select#offres-filter option[value='0']")
    if all_option is None:
        raise ValueError("Unisanté listing omitted its all-offers filter")
    empty = main.css_first("#no-ads")
    if empty is None or _normalized_text(empty) != "Aucune offre n'est disponible pour le moment.":
        raise ValueError("Unisanté listing omitted its scoped empty marker")
    if main.css_first(".pagination, a[rel='next'], link[rel='next']") is not None:
        raise ValueError("Unisanté listing introduced unsupported pagination")

    links = main.css(".row.offres-items .offres-item a.box-job__header_link[href]")
    cards = main.css(".row.offres-items .offres-item")
    if len(cards) != len(links):
        raise ValueError("Unisanté listing card/link structure changed")
    if len(links) > _MAX_JOBS:
        raise ValueError(f"Unisanté listing exceeded {_MAX_JOBS} jobs")

    jobs: dict[str, _ListingJob] = {}
    selected_slugs: set[str] = set()
    for link in links:
        href = link.attributes.get("href") or ""
        slug = _slug_from_href(href, listing_url)
        if slug is None:
            raise ValueError(f"Unisanté listing exposed an invalid job URL: {href!r}")
        title = _clean_text(link.attributes.get("title")) or _normalized_text(link)
        if not title:
            raise ValueError(f"Unisanté listing job {slug!r} omitted its title")
        if slug in jobs:
            raise ValueError(f"Unisanté listing repeated detail slug {slug!r}")
        selected_slugs.add(slug)
        jobs[slug] = _ListingJob(slug=slug, title=title)

    # If provider classes drift while the href shape remains recognizable, do
    # not mistake the resulting zero extraction for a healthy empty board.
    jobish_slugs = {
        slug
        for anchor in main.css("a[href]")
        if (slug := _slug_from_href(anchor.attributes.get("href") or "", listing_url)) is not None
    }
    if jobish_slugs != selected_slugs:
        raise ValueError("Unisanté listing contains jobs outside the authoritative inventory")
    if not jobs and cards:
        raise ValueError("Unisanté listing exposed empty malformed offer cards")
    empty_is_hidden = _is_explicitly_hidden(empty)
    if jobs and not empty_is_hidden:
        raise ValueError("Unisanté listing exposed jobs with a visible empty state")
    if not jobs and empty_is_hidden:
        raise ValueError(
            "Unisanté listing returned the normal hidden empty marker without "
            "authoritative zero evidence"
        )
    return jobs


async def _fetch_page(client: httpx.AsyncClient, url: str, *, detail: bool) -> str:
    page = await fetch_text_page_with_retry(
        client,
        url,
        retryable_statuses=_TRANSIENT_STATUSES,
        end_of_pagination_statuses=(),
        require_nonempty=True,
        max_bytes=_MAX_DETAIL_BYTES if detail else _MAX_LISTING_BYTES,
        log_event="unisante.detail_backoff" if detail else "unisante.listing_backoff",
    )
    if page is None:  # Strict status handling makes this unreachable.
        raise RuntimeError(f"Unisanté fetch returned no page for {url!r}")
    _raise_if_bot_challenge(url, page)
    return page


def _job_postings(value: object) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            found.extend(_job_postings(item))
    elif isinstance(value, dict):
        raw_type = value.get("@type")
        types = [raw_type] if isinstance(raw_type, str) else raw_type
        if isinstance(types, list) and any(item == "JobPosting" for item in types):
            found.append(value)
        graph = value.get("@graph")
        if isinstance(graph, (dict, list)):
            found.extend(_job_postings(graph))
    return found


def _parse_structured_fields(html: str) -> tuple[str | None, str | None, list[str] | None]:
    parser = _JsonLdParser()
    parser.feed(html)
    postings = [posting for block in parser.blocks for posting in _job_postings(block)]
    if len(postings) != 1:
        raise ValueError(f"Unisanté detail exposed {len(postings)} JobPosting JSON-LD records")
    posting = postings[0]
    organization = posting.get("hiringOrganization")
    if not isinstance(organization, dict):
        raise ValueError("Unisanté JobPosting omitted hiringOrganization")
    organization_name = _clean_text(organization.get("name"))
    same_as = _clean_text(organization.get("sameAs"))
    same_as_host = (urlparse(same_as).hostname or "").lower() if same_as else ""
    if not organization_name or "unisante" not in _fold(organization_name):
        raise ValueError("Unisanté JobPosting ownership name changed")
    if same_as_host not in {"unisante.ch", "www.unisante.ch"}:
        raise ValueError("Unisanté JobPosting ownership URL changed")

    date_posted = _clean_text(posting.get("datePosted"))
    if date_posted:
        try:
            date_posted = date.fromisoformat(date_posted[:10]).isoformat()
        except ValueError as exc:
            raise ValueError("Unisanté JobPosting has invalid datePosted") from exc

    employment = posting.get("employmentType")
    if isinstance(employment, list):
        values = [value for item in employment if (value := _clean_text(item))]
        employment_type = ", ".join(values) or None
    else:
        employment_type = _clean_text(employment)

    raw_locations = posting.get("jobLocation")
    location_items = raw_locations if isinstance(raw_locations, list) else [raw_locations]
    locations: list[str] = []
    for item in location_items:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if not isinstance(address, dict):
            continue
        locality = _clean_text(address.get("addressLocality"))
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name") or country.get("addressCountry")
        country_text = _clean_text(country)
        location = ", ".join(part for part in (locality, country_text) if part)
        if location and location not in locations:
            locations.append(location)
    return date_posted, employment_type, locations or None


def _parse_deadline(text: str) -> date | None:
    label = _DEADLINE_LABEL_RE.search(text)
    if label is None:
        return None
    value_match = _DATE_PREFIX_RE.match(text[label.end() :].lstrip())
    if value_match is None:
        raise ValueError("Unisanté detail has an unparseable application deadline")
    value = value_match.group("value")
    numeric = re.fullmatch(r"(\d{1,2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{4})", value)
    if numeric is not None:
        day, month, year = (int(part) for part in numeric.groups())
    else:
        words = value.split()
        if len(words) != 3 or _fold(words[1]) not in _FRENCH_MONTHS:
            raise ValueError("Unisanté detail has an unparseable French deadline")
        day, month, year = int(words[0]), _FRENCH_MONTHS[_fold(words[1])], int(words[2])
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError("Unisanté detail has an invalid application deadline") from exc


def _parse_detail(html: str, listing_job: _ListingJob) -> DiscoveredJob | None:
    document = LexborHTMLParser(html)
    main = document.css_first("main#main")
    if main is None:
        raise ValueError(f"Unisanté detail {listing_job.slug!r} omitted main#main")
    scopes = [
        node
        for node in main.css(".col-md-10")
        if _REFERENCE_RE.search(_normalized_text(node)) is not None
    ]
    if len(scopes) != 1:
        raise ValueError(
            f"Unisanté detail {listing_job.slug!r} exposed {len(scopes)} content scopes"
        )
    scope = scopes[0]
    visible_text = _normalized_text(scope)
    references = set(_REFERENCE_RE.findall(visible_text))
    if len(references) != 1:
        raise ValueError(f"Unisanté detail {listing_job.slug!r} has ambiguous reference")
    reference = references.pop()
    slug_reference = re.match(r"([1-9]\d*)-", listing_job.slug)
    if slug_reference is not None and slug_reference.group(1) != reference:
        raise ValueError(
            f"Unisanté detail {listing_job.slug!r} reference disagrees with its numeric slug"
        )
    deadline = _parse_deadline(visible_text)

    for selector in ("form", "script", "style", "noscript", "input", "button"):
        for node in scope.css(selector):
            node.decompose()
    description = normalize_description_html(scope.inner_html)
    description_text = _normalized_text(LexborHTMLParser(description or "").body)
    if len(description_text) < _MIN_DESCRIPTION_CHARS:
        raise ValueError(f"Unisanté detail {listing_job.slug!r} description is not substantive")
    if any(marker in description_text for marker in _MOJIBAKE_MARKERS):
        raise ValueError(f"Unisanté detail {listing_job.slug!r} contains mojibake")

    date_posted, employment_type, locations = _parse_structured_fields(html)
    if deadline is not None and deadline < _today():
        return None
    return DiscoveredJob(
        url=listing_job.detail_url,
        title=listing_job.title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        date_posted=date_posted,
        language="fr",
        metadata={
            "provider_reference": reference,
            "detail_slug": listing_job.slug,
            "detail_url": listing_job.detail_url,
            **({"application_deadline": deadline.isoformat()} if deadline else {}),
        },
        source_identity=f"unisante:emploi:{reference}",
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    del pw
    board_url = board.get("board_url")
    if board_url not in _LISTING_URLS or not _is_official_url(board_url):
        raise ValueError(f"Unisanté monitor requires an official listing URL, got {board_url!r}")

    pages = await asyncio.gather(
        *(_fetch_page(client, listing_url, detail=False) for listing_url in _LISTING_URLS)
    )
    inventories = [
        _parse_listing(page, listing_url)
        for page, listing_url in zip(pages, _LISTING_URLS, strict=True)
    ]
    expected = {(slug, job.title) for slug, job in inventories[0].items()}
    alias = {(slug, job.title) for slug, job in inventories[1].items()}
    if expected != alias:
        raise ValueError("Unisanté listing aliases exposed different inventories")
    if not inventories[0]:
        # Each parser independently proved the exact provider shell, a
        # server-visible empty state, and absence of recognizable hidden jobs.
        # The permanent hidden marker on ordinary pages is deliberately not
        # sufficient evidence for this path.
        return []

    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def fetch_one(job: _ListingJob) -> DiscoveredJob | None:
        async with semaphore:
            detail = await _fetch_page(client, job.detail_url, detail=True)
        return _parse_detail(detail, job)

    parsed = await asyncio.gather(
        *(fetch_one(job) for job in sorted(inventories[0].values(), key=lambda item: item.slug))
    )
    by_reference: dict[str, DiscoveredJob] = {}
    for job in parsed:
        if job is None:
            continue
        reference = str((job.metadata or {})["provider_reference"])
        existing = by_reference.get(reference)
        if existing is None:
            by_reference[reference] = job
            continue
        if existing.title != job.title:
            raise ValueError(f"Unisanté reference {reference} has conflicting titles")
        # Prefer the provider's numeric-slug alias when present, then choose a
        # stable lexical winner. This prevents duplicate rows inside a cycle.
        by_reference[reference] = min(
            (existing, job),
            key=lambda item: (
                not str((item.metadata or {}).get("detail_slug", "")).startswith(f"{reference}-"),
                str((item.metadata or {}).get("detail_url", "")),
            ),
        )
    return [by_reference[reference] for reference in sorted(by_reference, key=int)]


register("unisante", discover, cost=10, rich=True)
