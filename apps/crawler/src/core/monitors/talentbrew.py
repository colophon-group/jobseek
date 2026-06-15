"""TalentBrew/Radancy search-results monitor.

TalentBrew boards render a static search-results page with platform metadata
on ``#search-results`` and one page of job links in ``#search-results-list``.
Some tenants publish incomplete XML sitemaps, so this monitor uses the search
page's own pagination counters as the source of truth.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

import structlog

from src.core.monitors import fetch_page_text, register
from src.shared.http_retry import fetch_with_retry

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_URLS = 50_000
_MAX_PAGES = 5_000
_DEFAULT_PAGE_CHARS = 5_000_000
_MAX_PAGE_CHARS = 25_000_000
_MAX_AJAX_CHARS = 5_000_000
_DEFAULT_AJAX_PAGE_SIZE = 1_000
_MAX_AJAX_PAGE_SIZE = 10_000
_VOID_TAGS = frozenset(
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


@dataclass(slots=True)
class _ParsedPage:
    urls: set[str] = field(default_factory=set)
    total_jobs: int | None = None
    total_pages: int | None = None
    current_page: int | None = None
    records_per_page: int | None = None
    ajax_url: str | None = None
    search_attrs: dict[str, str] = field(default_factory=dict)


def _attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key.lower(): value or "" for key, value in attrs}


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _is_job_url(url: str) -> bool:
    return "/job/" in urlparse(url).path.lower()


class _TalentBrewParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.total_jobs: int | None = None
        self.total_pages: int | None = None
        self.current_page: int | None = None
        self.records_per_page: int | None = None
        self.ajax_url: str | None = None
        self.search_attrs: dict[str, str] = {}
        self.urls: set[str] = set()
        self._fallback_urls: set[str] = set()
        self._results_depth = 0
        self._saw_results_list = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = _attrs_dict(attrs)

        if attr.get("id") == "search-results":
            self.search_attrs = attr
            self.total_jobs = _to_int(attr.get("data-total-job-results")) or _to_int(
                attr.get("data-total-results")
            )
            self.total_pages = _to_int(attr.get("data-total-pages"))
            self.current_page = _to_int(attr.get("data-current-page"))
            self.records_per_page = _to_int(attr.get("data-records-per-page"))
            self.ajax_url = attr.get("data-ajax-url") or None

        if attr.get("id") == "search-results-list":
            self._saw_results_list = True
            self._results_depth = 1
        elif self._results_depth and tag not in _VOID_TAGS:
            self._results_depth += 1

        if tag != "a":
            return

        href = attr.get("href")
        if not href:
            return
        absolute = urljoin(self.base_url, href)
        if not absolute.startswith("http") or not _is_job_url(absolute):
            return

        if self._results_depth:
            self.urls.add(absolute)
        elif attr.get("data-job-id"):
            self._fallback_urls.add(absolute)

    def handle_endtag(self, tag: str) -> None:
        if self._results_depth and tag.lower() not in _VOID_TAGS:
            self._results_depth -= 1

    def parsed(self) -> _ParsedPage:
        urls = self.urls if self._saw_results_list else self._fallback_urls
        return _ParsedPage(
            urls=urls,
            total_jobs=self.total_jobs,
            total_pages=self.total_pages,
            current_page=self.current_page,
            records_per_page=self.records_per_page,
            ajax_url=self.ajax_url,
            search_attrs=self.search_attrs,
        )


def _parse_page(html: str, base_url: str) -> _ParsedPage:
    parser = _TalentBrewParser(base_url)
    parser.feed(html)
    parser.close()
    return parser.parsed()


def _looks_like_talentbrew(html: str) -> bool:
    lower = html.lower()
    has_platform_marker = (
        "tbcdn.talentbrew.com" in lower
        or "radancy.net" in lower
        or "tmpwebeng.com/magicbullet" in lower
    )
    return has_platform_marker and 'id="search-results"' in lower


def _page_url(board_url: str, page: int) -> str:
    if page <= 1:
        return board_url

    parsed = urlparse(board_url)
    params = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "p"
    ]
    params.append(("p", str(page)))
    return urlunparse(parsed._replace(query=urlencode(params)))


def _url_with_params(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(params.items())
    return urlunparse(parsed._replace(query=urlencode(query)))


def _target_pages(parsed: _ParsedPage, metadata: dict) -> int:
    configured_max = _to_int(str(metadata.get("max_pages"))) if metadata.get("max_pages") else None
    max_pages = min(configured_max or _MAX_PAGES, _MAX_PAGES)
    total_pages = parsed.total_pages
    if total_pages is None and parsed.total_jobs is not None and parsed.records_per_page:
        total_pages = math.ceil(parsed.total_jobs / parsed.records_per_page)
    if total_pages is None:
        return 1
    return max(1, min(total_pages, max_pages))


def _ajax_page_size(metadata: dict) -> int:
    configured = (
        _to_int(str(metadata.get("ajax_page_size")))
        or _to_int(str(metadata.get("page_size")))
        or _to_int(str(metadata.get("records_per_page")))
    )
    if configured is None:
        return _DEFAULT_AJAX_PAGE_SIZE
    return max(1, min(configured, _MAX_AJAX_PAGE_SIZE))


def _page_max_chars(metadata: dict) -> int:
    configured = (
        _to_int(str(metadata.get("page_max_chars"))) if metadata.get("page_max_chars") else None
    )
    if configured is None:
        return _DEFAULT_PAGE_CHARS
    return max(1, min(configured, _MAX_PAGE_CHARS))


def _attr(attrs: dict[str, str], key: str, default: str = "") -> str:
    return attrs.get(key) or default


def _ajax_params(parsed: _ParsedPage, board_url: str, page: int, page_size: int) -> dict[str, str]:
    attrs = parsed.search_attrs
    params = {
        "ActiveFacetID": _attr(attrs, "data-active-facet-id", "0"),
        "CurrentPage": str(page),
        "RecordsPerPage": str(page_size),
        "Distance": _attr(attrs, "data-distance", "50"),
        "ShowRadius": _attr(attrs, "data-show-radius", "False"),
        "CustomFacetName": _attr(attrs, "data-custom-facet-name"),
        "FacetTerm": _attr(attrs, "data-facet-term"),
        "FacetType": _attr(attrs, "data-facet-type", "0"),
        "SearchResultsModuleName": _attr(
            attrs, "data-search-results-module-name", "Search Results"
        ),
        "SearchFiltersModuleName": _attr(
            attrs, "data-search-filters-module-name", "Search Filters"
        ),
        "SortCriteria": _attr(attrs, "data-sort-criteria", "0"),
        "SortDirection": _attr(attrs, "data-sort-direction", "0"),
        "SearchType": _attr(attrs, "data-search-type", "5"),
        "PostalCode": _attr(attrs, "data-postal-code"),
    }

    optional = {
        "Keywords": _attr(attrs, "data-keywords"),
        "Location": _attr(attrs, "data-location"),
        "Latitude": _attr(attrs, "data-latitude"),
        "Longitude": _attr(attrs, "data-longitude"),
        "KeywordType": _attr(attrs, "data-keyword-type"),
        "LocationType": _attr(attrs, "data-location-type"),
        "LocationPath": _attr(attrs, "data-location-path"),
        "OrganizationIds": _attr(attrs, "data-organization-ids"),
    }
    params.update({key: value for key, value in optional.items() if value})

    query = parse_qs(urlparse(board_url).query)
    facet_ids = [
        facet_id.strip()
        for raw in query.get("fl", [])
        for facet_id in raw.split(",")
        if facet_id.strip()
    ]
    for index, facet_id in enumerate(facet_ids):
        params[f"FacetFilters[{index}].ID"] = facet_id
        params[f"FacetFilters[{index}].FacetType"] = "3"
        params[f"FacetFilters[{index}].IsApplied"] = "true"

    return params


async def _discover_via_ajax(
    board_url: str,
    parsed: _ParsedPage,
    metadata: dict,
    client: httpx.AsyncClient,
) -> set[str] | None:
    if not parsed.ajax_url or metadata.get("ajax") is False:
        return None

    page_size = _ajax_page_size(metadata)
    if parsed.total_jobs is not None:
        pages = math.ceil(parsed.total_jobs / page_size)
    else:
        pages = parsed.total_pages or 1
    configured_max = _to_int(str(metadata.get("max_pages"))) if metadata.get("max_pages") else None
    pages = min(max(1, pages), configured_max or _MAX_PAGES, _MAX_PAGES)
    api_url = urljoin(board_url, parsed.ajax_url)
    max_chars = max(_MAX_AJAX_CHARS, min(page_size * 5_000, 25_000_000))

    urls: set[str] = set()
    for page in range(1, pages + 1):
        request_url = _url_with_params(api_url, _ajax_params(parsed, board_url, page, page_size))
        text = await fetch_with_retry(
            client,
            request_url,
            max_chars=max_chars,
            transient_403=True,
        )
        if text is None:
            log.warning("talentbrew.ajax_missing", board_url=board_url, page=page, url=request_url)
            break

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            log.warning("talentbrew.ajax_non_json", board_url=board_url, page=page, url=request_url)
            return None

        results_html = payload.get("results") if isinstance(payload, dict) else None
        if not results_html:
            log.warning("talentbrew.ajax_empty", board_url=board_url, page=page, url=request_url)
            return None if page == 1 else urls

        page_urls = _parse_page(results_html, board_url).urls
        if not page_urls:
            log.warning("talentbrew.ajax_no_urls", board_url=board_url, page=page, url=request_url)
            return None if page == 1 else urls

        new_urls = page_urls - urls
        if not new_urls:
            log.warning(
                "talentbrew.ajax_no_new_urls",
                board_url=board_url,
                page=page,
                url=request_url,
            )
            break
        urls.update(new_urls)

        if parsed.total_jobs is not None and len(urls) >= parsed.total_jobs:
            break
        if len(urls) >= MAX_URLS:
            log.warning("talentbrew.truncated", total=len(urls), cap=MAX_URLS)
            return set(sorted(urls)[:MAX_URLS])

    return urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect TalentBrew/Radancy search result pages from static HTML."""
    if client is None:
        return None

    html = await fetch_page_text(url, client, max_chars=_DEFAULT_PAGE_CHARS)
    if not html or not _looks_like_talentbrew(html):
        return None

    parsed = _parse_page(html, url)
    if parsed.total_jobs is None and not parsed.urls:
        return None

    result: dict[str, int] = {"urls": len(parsed.urls)}
    if parsed.total_jobs is not None:
        result["jobs"] = parsed.total_jobs
    if parsed.total_pages is not None:
        result["pages"] = parsed.total_pages
    return result


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> set[str]:
    """Discover job URLs from a TalentBrew/Radancy search page."""
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}

    first_html = await fetch_with_retry(
        client,
        board_url,
        max_chars=_page_max_chars(metadata),
        transient_403=True,
    )
    if first_html is None:
        log.warning("talentbrew.fetch_failed", board_url=board_url)
        return set()

    parsed = _parse_page(first_html, board_url)
    ajax_urls = await _discover_via_ajax(board_url, parsed, metadata, client)
    if ajax_urls is not None:
        urls = ajax_urls
        pages = math.ceil((parsed.total_jobs or len(urls)) / _ajax_page_size(metadata))
        if parsed.total_jobs is not None and len(urls) < parsed.total_jobs:
            log.warning(
                "talentbrew.count_mismatch",
                board_url=board_url,
                expected=parsed.total_jobs,
                discovered=len(urls),
                pages=pages,
            )
        log.info(
            "talentbrew.complete",
            board_url=board_url,
            urls_found=len(urls),
            expected=parsed.total_jobs,
            pages=pages,
            source="ajax",
        )
        return urls

    urls = set(parsed.urls)
    pages = _target_pages(parsed, metadata)

    for page in range(2, pages + 1):
        url = _page_url(board_url, page)
        html = await fetch_with_retry(
            client,
            url,
            max_chars=_page_max_chars(metadata),
            transient_403=True,
        )
        if html is None:
            log.warning("talentbrew.pagination_missing", board_url=board_url, page=page, url=url)
            break

        page_urls = _parse_page(html, url).urls
        if not page_urls:
            log.warning("talentbrew.pagination_empty", board_url=board_url, page=page, url=url)
            break
        new_urls = page_urls - urls
        if not new_urls:
            log.warning(
                "talentbrew.pagination_no_new_urls",
                board_url=board_url,
                page=page,
                url=url,
            )
            break
        urls.update(new_urls)

        if len(urls) >= MAX_URLS:
            log.warning("talentbrew.truncated", total=len(urls), cap=MAX_URLS)
            urls = set(sorted(urls)[:MAX_URLS])
            break

    if parsed.total_jobs is not None and len(urls) < parsed.total_jobs:
        log.warning(
            "talentbrew.count_mismatch",
            board_url=board_url,
            expected=parsed.total_jobs,
            discovered=len(urls),
            pages=pages,
        )

    log.info(
        "talentbrew.complete",
        board_url=board_url,
        urls_found=len(urls),
        expected=parsed.total_jobs,
        pages=pages,
    )
    return urls


register("talentbrew", discover, cost=45, can_handle=can_handle)
