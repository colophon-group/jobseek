"""iCIMS public career-site monitor.

iCIMS tenants expose server-rendered listings at
``https://{host}/jobs/search?ss=1&in_iframe=1``.  This adapter discovers only
stable job URLs; the existing JSON-LD scraper owns detail extraction on the
normal scrape schedule.
"""

from __future__ import annotations

import html as html_module
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import BoardGoneError, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.dom import _extract_links_static, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_PAGES = 1_000
MAX_HTML_CHARS = 2_000_000

_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.icims\.com$",
    re.IGNORECASE,
)
_DIRECT_PATH_RE = re.compile(
    r"^(?:/jobs(?:/search|/\d+(?:/[^/?#]+)?/job)?)?/?$",
    re.IGNORECASE,
)
_PAGE_PATTERNS = [
    re.compile(
        r"https?://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.icims\.com)"
        r"(?:/jobs(?:/search|/\d+(?:/[^/?#]+)?/job)?)?/?"
        r"(?:\?(?:in_iframe=1|ss=1(?:&(?:amp;)?in_iframe=1)?))?"
        r"(?=[#\"'<\s]|$)",
        re.IGNORECASE,
    )
]
_RESERVED_HOSTS = frozenset(
    {
        "api.icims.com",
        "app.icims.com",
        "help.icims.com",
        "support.icims.com",
        "www.icims.com",
    }
)
_LISTING_MARKER = "iCIMS_ListingsPage"
_PAGE_MARKER_RE = re.compile(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", re.IGNORECASE)
_PAGE_PARAM_RE = re.compile(r"(?:[?&])pr=(\d+)(?=[&#\"'\s]|$)", re.IGNORECASE)
_GONE_STATUSES = frozenset({404, 410})
_ALLOWED_QUERY_VALUES: dict[str, re.Pattern[str]] = {
    "in_iframe": re.compile(r"1"),
    "o": re.compile(r""),
    "pr": re.compile(r"\d+"),
    "schemaId": re.compile(r""),
    "searchRelation": re.compile(r"keyword_all"),
    "ss": re.compile(r"1"),
}

_CROSS_LOCALE_DEDUPE_KEYS = frozenset({"peer_host", "title_aliases"})


def _normalized_listing_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _cross_locale_dedupe_config(
    metadata: dict,
    host: str,
) -> tuple[str, dict[str, str]] | None:
    raw = metadata.get("cross_locale_dedupe")
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - _CROSS_LOCALE_DEDUPE_KEYS:
        raise ValueError("iCIMS cross_locale_dedupe must contain only peer_host and title_aliases")
    peer_host = _normalize_host(raw.get("peer_host"))
    if peer_host is None or peer_host == host:
        raise ValueError("iCIMS cross_locale_dedupe peer_host must be a different valid host")
    raw_aliases = raw.get("title_aliases", {})
    if not isinstance(raw_aliases, dict) or len(raw_aliases) > 100:
        raise ValueError(
            "iCIMS cross_locale_dedupe title_aliases must be an object up to 100 entries"
        )
    aliases: dict[str, str] = {}
    for source, canonical in raw_aliases.items():
        if (
            not isinstance(source, str)
            or not isinstance(canonical, str)
            or not source.strip()
            or not canonical.strip()
            or len(source) > 200
            or len(canonical) > 200
        ):
            raise ValueError(
                "iCIMS cross_locale_dedupe title aliases must be non-empty strings up to 200 chars"
            )
        normalized_source = _normalized_listing_text(source)
        normalized_canonical = _normalized_listing_text(canonical)
        previous = aliases.setdefault(normalized_source, normalized_canonical)
        if previous != normalized_canonical:
            raise ValueError("iCIMS cross_locale_dedupe title aliases conflict after normalization")
    return peer_host, aliases


def _normalize_host(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    host = value.strip().lower().rstrip(".")
    if _HOST_RE.fullmatch(host) is None or host in _RESERVED_HOSTS:
        return None
    return host


def _host_from_url(url: str, *, validate_query: bool = True) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = _normalize_host(parsed.hostname)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query_keys = [key for key, _value in query]
    if (
        host is None
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or _DIRECT_PATH_RE.fullmatch(parsed.path) is None
        or (
            validate_query
            and (
                len(query_keys) != len(set(query_keys))
                or any(
                    key not in _ALLOWED_QUERY_VALUES
                    or _ALLOWED_QUERY_VALUES[key].fullmatch(value) is None
                    for key, value in query
                )
            )
        )
    ):
        return None
    return host


def _listing_url(host: str, page_index: int = 0) -> str:
    if page_index == 0:
        return f"https://{host}/jobs/search?ss=1&in_iframe=1"
    return (
        f"https://{host}/jobs/search?pr={page_index}&in_iframe=1"
        "&searchRelation=keyword_all&schemaId=&o="
    )


def _job_matcher(host: str) -> re.Pattern[str]:
    return re.compile(
        rf"^https://{re.escape(host)}/jobs/\d+(?:/[^/?#]+)?/job(?:[/?#]|$)",
        re.IGNORECASE,
    )


def _canonical_job_url(url: str, host: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) not in {3, 4}
        or segments[0].lower() != "jobs"
        or re.fullmatch(r"\d+", segments[1]) is None
        or segments[-1].lower() != "job"
    ):
        return None
    return f"https://{host}/jobs/{segments[1]}/job?in_iframe=1"


def _parse_listing(page: str, host: str) -> set[str]:
    raw_urls = _extract_links_static(page, _listing_url(host), _job_matcher(host))
    return {
        canonical for url in raw_urls if (canonical := _canonical_job_url(url, host)) is not None
    }


def _parse_listing_identities(page: str, host: str) -> dict[str, tuple[str, str, str]]:
    """Return stable title/region/type identities from iCIMS listing cards."""
    document = LexborHTMLParser(page)
    records: dict[str, tuple[str, str, str]] = {}
    for card in document.css(".iCIMS_JobCardItem"):
        anchor = card.css_first("a.iCIMS_Anchor")
        title = card.css_first("h3")
        region_container = card.css_first("div.header.left")
        if anchor is None or title is None or region_container is None:
            raise ValueError(f"iCIMS host {host!r} returned an incomplete listing card")
        url = _canonical_job_url(anchor.attributes.get("href") or "", host)
        visible_region_spans = [
            span
            for span in region_container.css("span")
            if "sr-only" not in (span.attributes.get("class") or "").split()
        ]
        job_type = None
        for field in card.css(".iCIMS_JobHeaderTag"):
            label = field.css_first("dt")
            value = field.css_first("dd")
            if (
                label is not None
                and value is not None
                and _normalized_listing_text(label.text(strip=True)) == "job type"
            ):
                job_type = value.text(strip=True)
                break
        raw_title = title.text(strip=True)
        raw_region = visible_region_spans[-1].text(strip=True) if visible_region_spans else ""
        if url is None or not raw_title or not raw_region or not job_type:
            raise ValueError(f"iCIMS host {host!r} returned an incomplete listing identity")
        identity = (
            _normalized_listing_text(raw_title),
            _normalized_listing_text(raw_region),
            _normalized_listing_text(job_type),
        )
        previous = records.setdefault(url, identity)
        if previous != identity:
            raise ValueError(f"iCIMS host {host!r} returned conflicting listing identities")
    return records


def _page_metadata(page: str) -> tuple[int | None, int]:
    """Return the one-based current page (when visible) and total pages."""
    marker = _PAGE_MARKER_RE.search(page)
    current = int(marker.group(1)) if marker else None
    marker_total = int(marker.group(2)) if marker else 1
    decoded = html_module.unescape(page)
    linked_indexes = [int(value) for value in _PAGE_PARAM_RE.findall(decoded)]
    linked_total = max(linked_indexes, default=0) + 1
    return current, max(marker_total, linked_total, 1)


async def _fetch_listing(host: str, page_index: int, client: httpx.AsyncClient) -> str:
    url = _listing_url(host, page_index)
    try:
        page = await fetch_text_page_with_retry(
            client,
            url,
            max_chars=MAX_HTML_CHARS,
            require_nonempty=True,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            retryable_statuses={202, 401, 403},
            log_event="icims.list_backoff",
        )
    except PaginationFetchError as exc:
        if page_index == 0 and exc.last_status in _GONE_STATUSES:
            raise BoardGoneError("iCIMS board no longer exists", url=url) from exc
        raise
    if page is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"iCIMS listing fetch returned no page for {host!r}")
    _raise_if_bot_challenge(url, page)
    if _LISTING_MARKER.casefold() not in page.casefold():
        raise ValueError(f"iCIMS host {host!r} returned a non-listing page")
    return page


async def _discover_pages(
    host: str,
    client: httpx.AsyncClient,
    *,
    collect_identities: bool = False,
) -> tuple[set[str], bool, int, dict[str, tuple[str, str, str]]]:
    first_page = await _fetch_listing(host, 0, client)
    first_current, advertised_pages = _page_metadata(first_page)
    if first_current not in {None, 1}:
        raise ValueError(f"iCIMS host {host!r} returned page {first_current} for page index 0")

    urls: set[str] = set()
    identities: dict[str, tuple[str, str, str]] = {}
    truncated = advertised_pages > MAX_PAGES

    def merge_page(page_index: int, page: str) -> None:
        nonlocal truncated
        current, total = _page_metadata(page)
        if current is not None and current != page_index + 1:
            raise ValueError(
                f"iCIMS host {host!r} returned page {current} for page index {page_index}"
            )
        if total != advertised_pages:
            truncated = True
        page_urls = _parse_listing(page, host)
        if page_index > 0 and not page_urls:
            raise ValueError(
                f"iCIMS host {host!r} returned an empty advertised page {page_index + 1}"
            )
        if urls.intersection(page_urls):
            truncated = True
        urls.update(page_urls)
        if collect_identities:
            page_identities = _parse_listing_identities(page, host)
            if set(page_identities) != page_urls:
                raise ValueError(
                    f"iCIMS host {host!r} listing identities do not match discovered jobs"
                )
            for url, identity in page_identities.items():
                previous = identities.setdefault(url, identity)
                if previous != identity:
                    raise ValueError(f"iCIMS host {host!r} returned conflicting listing identities")
        if len(page) >= MAX_HTML_CHARS:
            truncated = True

    merge_page(0, first_page)
    page_limit = min(advertised_pages, MAX_PAGES)
    for page_index in range(1, page_limit):
        if len(urls) >= MAX_JOBS:
            truncated = True
            break
        # iCIMS pagination is session-sensitive: concurrent requests through
        # one cookie-bearing client can return a neighbouring page. Fetching
        # sequentially is deterministic and lets each HTML body be discarded
        # immediately after parsing.
        page = await _fetch_listing(host, page_index, client)
        merge_page(page_index, page)

    return urls, truncated, advertised_pages, identities


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover stable iCIMS detail URLs with complete bounded pagination."""
    _ = pw
    metadata = board.get("metadata") or {}
    host = _normalize_host(metadata.get("host")) or _host_from_url(board["board_url"])
    if host is None:
        raise ValueError(
            f"Cannot derive iCIMS host from board URL {board['board_url']!r} "
            "and no valid host is present in metadata"
        )

    dedupe = _cross_locale_dedupe_config(metadata, host)
    urls, truncated, pages, identities = await _discover_pages(
        host,
        client,
        collect_identities=dedupe is not None,
    )
    if dedupe is not None:
        peer_host, aliases = dedupe
        _peer_urls, peer_truncated, _peer_pages, peer_identities = await _discover_pages(
            peer_host,
            client,
            collect_identities=True,
        )
        if peer_truncated:
            raise ValueError(
                f"iCIMS cross-locale peer {peer_host!r} was truncated; refusing partial dedupe"
            )

        def canonical(identity: tuple[str, str, str]) -> tuple[str, str, str]:
            title, region, job_type = identity
            return aliases.get(title, title), region, job_type

        peer_keys = {canonical(identity) for identity in peer_identities.values()}
        duplicates = {
            url for url, identity in identities.items() if canonical(identity) in peer_keys
        }
        urls.difference_update(duplicates)
        log.info(
            "icims.cross_locale_deduped",
            host=host,
            peer_host=peer_host,
            removed=len(duplicates),
            kept=len(urls),
        )
    log_method = log.warning if truncated else log.info
    log_method("icims.discovered", host=host, jobs=len(urls), pages=pages, truncated=truncated)
    return truncated_url_result(urls) if truncated else urls


async def _probe_host(host: str, client: httpx.AsyncClient) -> ProbeResult:
    try:
        page = await _fetch_listing(host, 0, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("icims.probe_failed", host=host, exc_info=True)
        return False, None
    urls = _parse_listing(page, host)
    _, pages = _page_metadata(page)
    count: ProbeCount = len(urls) if pages == 1 else f"{len(urls)}+ (first of {pages} pages)"
    return True, count


async def _fetch_job_count(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_host(token, client)
    return count if found else None


async def _probe_candidate(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_host(token, client)


def _build_result(host: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    result: dict = {"host": host}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect only direct or explicitly linked iCIMS public hosts."""
    _ = pw
    if _host_from_url(url) is None and _host_from_url(url, validate_query=False) is not None:
        # Do not fetch a filtered tenant URL and rediscover its host from its
        # own job links; doing so would silently widen the configured scope.
        return None
    return await ats_can_handle(
        url,
        client,
        monitor_name="icims",
        token_from_url=_host_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=frozenset(),
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_candidate,
        initial_context=None,
        result_builder=_build_result,
        page_token_probe=_probe_candidate,
        require_direct_count=True,
        allow_slug_guess=False,
        log_token_field="host",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    host = _normalize_host(metadata.get("host")) or _host_from_url(board_url)
    if host is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        _listing_url(host),
        filename="icims-listing.html",
        follow_redirects=False,
    )


register("icims", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
