"""Jobvite server-rendered listing monitor.

Jobvite publishes open requisitions as stable anchors on static HTML listings.
Large branded sites can truncate each category behind explicit ``Show More``
search pages, so this adapter follows those same-tenant pages and verifies their
advertised ranges before accepting the inventory.  The existing JSON-LD scraper
owns detail extraction on its normal schedule.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, fetch_page_text, register
from src.core.monitors.dom import _extract_links_static, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.jobvite import (
    JobviteBoard,
    is_jobvite_invalid_redirect,
    jobvite_board_from_metadata,
    jobvite_board_from_url,
    jobvite_job_url,
    jobvite_page_tenant,
)
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_HTML_CHARS = 5_000_000
MAX_LINKED_LISTINGS = 6
MAX_SEARCH_CATEGORIES = 128
MAX_SEARCH_PAGES = 5_000
_TRANSIENT_STATUSES = frozenset({202, 401, 403})
_GONE_STATUSES = frozenset({404, 410})
_JOB_LINK_RE = re.compile(
    r"^https://jobs\.jobvite\.com/[a-z0-9](?:[a-z0-9-]{0,62})/"
    r"job/[A-Za-z0-9_-]{6,64}(?:[/?#]|$)",
    re.IGNORECASE,
)
_LISTING_LINK_RE = re.compile(
    r"^https://jobs\.jobvite\.com/(?:"
    r"[a-z0-9](?:[a-z0-9-]{0,62})(?:/jobs(?:/positions)?)?"
    r"|careers/[a-z0-9](?:[a-z0-9-]{0,62})(?:/jobs)?"
    r")(?:[?#]|$)",
    re.IGNORECASE,
)
_SEARCH_LINK_RE = re.compile(
    r"^https://jobs\.jobvite\.com/"
    r"[a-z0-9](?:[a-z0-9-]{0,62})/search/?\?[^#\s]+$",
    re.IGNORECASE,
)
_SEARCH_PAGINATION_RE = re.compile(
    r'<div\s+class=["\']jv-pagination-text["\']>\s*'
    r"(\d+)\s*-\s*(\d+)\s+of\s+(\d+)\s*</div>",
    re.IGNORECASE,
)
_PAGE_URL_RE = re.compile(
    r"(https?://jobs\.jobvite\.com/(?:careers/)?"
    r"[a-z0-9](?:[a-z0-9-]{0,62})"
    r"(?:/jobs(?:/positions)?|/job/[A-Za-z0-9_-]{6,64})?/?"
    r"(?:\?[^\"'<\s]*)?)(?=[#\"'<\s]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _SearchPage:
    category: str
    page: int


@dataclass(frozen=True, slots=True)
class _SearchPagination:
    start: int
    end: int
    total: int


def _search_page_from_url(url: str, tenant: str) -> _SearchPage | None:
    """Parse one same-tenant Jobvite category-search page."""
    try:
        parsed = urlparse(html.unescape(url))
        port = parsed.port
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold().rstrip(".") != "jobs.jobvite.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or len(segments) != 2
        or segments[0].casefold() != tenant.casefold()
        or segments[1].casefold() != "search"
        or set(query) != {"c", "p"}
        or any(len(values) != 1 for values in query.values())
    ):
        return None
    category = query["c"][0].strip()
    raw_page = query["p"][0]
    if (
        not category
        or len(category) > 256
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in category)
        or not raw_page.isascii()
        or not raw_page.isdigit()
    ):
        return None
    page = int(raw_page)
    if page >= MAX_SEARCH_PAGES:
        return None
    return _SearchPage(category=category, page=page)


def _search_page_url(tenant: str, search: _SearchPage) -> str:
    query = urlencode({"c": search.category, "p": str(search.page)})
    return urlunparse(("https", "jobs.jobvite.com", f"/{tenant}/search", "", query, ""))


def _search_pagination(body: str) -> _SearchPagination | None:
    match = _SEARCH_PAGINATION_RE.search(body)
    if match is None:
        return None
    return _SearchPagination(*(int(value) for value in match.groups()))


def _board_identity(board: dict) -> JobviteBoard:
    metadata = board.get("metadata") or {}
    configured = jobvite_board_from_metadata(metadata) if isinstance(metadata, dict) else None
    direct = jobvite_board_from_url(board["board_url"])
    has_configured_identity = isinstance(metadata, dict) and bool(
        {"tenant", "listing_url"} & metadata.keys()
    )
    if has_configured_identity and configured is None:
        raise ValueError("Invalid or internally inconsistent Jobvite monitor configuration")
    if configured is not None and direct is not None and configured.tenant != direct.tenant:
        raise ValueError(
            f"Configured Jobvite tenant {configured.tenant!r} does not match "
            f"board URL tenant {direct.tenant!r}"
        )
    resolved = configured or direct
    if resolved is None:
        raise ValueError(
            f"Cannot derive a Jobvite tenant/listing from {board['board_url']!r}; "
            "configure metadata.tenant or metadata.listing_url"
        )
    return resolved


async def _fetch_listing(
    key: JobviteBoard,
    client: httpx.AsyncClient,
    *,
    terminal: bool,
    page_url: str | None = None,
) -> str:
    url = page_url or key.listing_url
    try:
        body = await fetch_text_page_with_retry(
            client,
            url,
            follow_redirects=False,
            retryable_statuses=_TRANSIENT_STATUSES,
            end_of_pagination_statuses=(),
            require_nonempty=True,
            max_chars=MAX_HTML_CHARS + 1,
            log_event="jobvite.listing_backoff",
        )
    except PaginationFetchError as exc:
        if terminal and (
            exc.last_status in _GONE_STATUSES or is_jobvite_invalid_redirect(exc.last_location)
        ):
            raise BoardGoneError(
                "Jobvite board no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    if body is None:  # Strict status handling makes this unreachable.
        raise RuntimeError(f"Jobvite listing fetch returned no page for {url!r}")
    _raise_if_bot_challenge(url, body)
    if len(body) > MAX_HTML_CHARS:
        raise ValueError("Jobvite listing exceeded the HTML safety cap")
    return body


def _parse_listing(
    body: str,
    key: JobviteBoard,
    *,
    page_url: str | None = None,
) -> tuple[set[str], tuple[JobviteBoard, ...], tuple[str, ...]]:
    page_tenant = jobvite_page_tenant(body)
    if page_tenant != key.tenant:
        raise ValueError(
            f"Jobvite listing identity mismatch: expected {key.tenant!r}, found {page_tenant!r}"
        )

    base_url = page_url or key.listing_url
    raw_jobs = _extract_links_static(body, base_url, _JOB_LINK_RE)
    jobs = {
        canonical for url in raw_jobs if (canonical := jobvite_job_url(url, key.tenant)) is not None
    }
    if len(jobs) > MAX_JOBS:
        raise ValueError(f"Jobvite listing exceeded the {MAX_JOBS:,}-job safety cap")

    raw_listings = _extract_links_static(body, base_url, _LISTING_LINK_RE)
    candidates = {
        candidate
        for url in raw_listings
        if (candidate := jobvite_board_from_url(url)) is not None
        and candidate.tenant == key.tenant
        and candidate != key
    }
    raw_searches = _extract_links_static(body, base_url, _SEARCH_LINK_RE)
    searches = {
        _search_page_url(key.tenant, search)
        for url in raw_searches
        if (search := _search_page_from_url(url, key.tenant)) is not None
    }
    if len(searches) > MAX_SEARCH_CATEGORIES:
        raise ValueError(
            f"Jobvite listing exceeded the {MAX_SEARCH_CATEGORIES}-category safety cap"
        )
    return (
        jobs,
        tuple(sorted(candidates, key=lambda item: item.listing_url)),
        tuple(sorted(searches)),
    )


async def _discover_search_pages(
    key: JobviteBoard,
    initial_urls: tuple[str, ...],
    client: httpx.AsyncClient,
) -> set[str]:
    """Expand explicit Jobvite category links, validating every advertised page."""
    categories: dict[str, _SearchPage] = {}
    for url in initial_urls:
        search = _search_page_from_url(url, key.tenant)
        if search is None or search.page != 0:
            raise ValueError(f"Jobvite listing exposed an invalid category page: {url!r}")
        categories[search.category] = search

    urls: set[str] = set()
    pages_fetched = 0
    for category in sorted(categories):
        expected_total: int | None = None
        page_size: int | None = None
        page = 0
        while True:
            if pages_fetched >= MAX_SEARCH_PAGES:
                raise ValueError(
                    f"Jobvite category pagination exceeded the {MAX_SEARCH_PAGES}-page safety cap"
                )
            search = _SearchPage(category=category, page=page)
            page_url = _search_page_url(key.tenant, search)
            body = await _fetch_listing(
                key,
                client,
                terminal=False,
                page_url=page_url,
            )
            pages_fetched += 1
            page_jobs, _listings, linked_searches = _parse_listing(
                body,
                key,
                page_url=page_url,
            )
            pagination = _search_pagination(body)
            if pagination is None:
                raise ValueError(f"Jobvite category page omitted pagination totals: {page_url}")
            if pagination.total < 1 or not (1 <= pagination.start <= pagination.end):
                raise ValueError(f"Jobvite category page returned invalid pagination: {page_url}")
            if expected_total is None:
                if pagination.start != 1:
                    raise ValueError(f"Jobvite category pagination did not start at 1: {page_url}")
                expected_total = pagination.total
                page_size = pagination.end
            assert page_size is not None
            assert expected_total is not None
            expected_start = (page * page_size) + 1
            expected_end = min(expected_start + page_size - 1, expected_total)
            if (
                pagination.total != expected_total
                or pagination.start != expected_start
                or pagination.end != expected_end
                or len(page_jobs) != expected_end - expected_start + 1
            ):
                raise ValueError(f"Jobvite category pagination drifted: {page_url}")
            urls.update(page_jobs)
            if len(urls) > MAX_JOBS:
                raise ValueError(f"Jobvite listing exceeded the {MAX_JOBS:,}-job safety cap")
            if expected_end == expected_total:
                break

            expected_next = _SearchPage(category=category, page=page + 1)
            linked_pages = {
                linked
                for url in linked_searches
                if (linked := _search_page_from_url(url, key.tenant)) is not None
            }
            if expected_next not in linked_pages:
                raise ValueError(f"Jobvite category page omitted its next link: {page_url}")
            page += 1

    return urls


async def resolve_listing(
    initial: JobviteBoard,
    client: httpx.AsyncClient,
    *,
    terminal: bool,
) -> tuple[JobviteBoard, set[str]]:
    """Resolve branded landing pages to an explicit same-tenant job listing."""
    body = await _fetch_listing(initial, client, terminal=terminal)
    jobs, candidates, searches = _parse_listing(body, initial)
    if searches:
        jobs.update(await _discover_search_pages(initial, searches, client))
    if jobs or not candidates:
        return initial, jobs

    # Some branded ``/careers/{tenant}`` pages are marketing shells whose
    # Jobs CTA points to ``/{tenant}/jobs/positions``.  Never treat such a
    # shell as an authoritative empty inventory before checking that explicit
    # same-tenant destination.
    for candidate in candidates[:MAX_LINKED_LISTINGS]:
        try:
            candidate_body = await _fetch_listing(candidate, client, terminal=False)
            candidate_jobs, _, candidate_searches = _parse_listing(candidate_body, candidate)
            if candidate_searches:
                candidate_jobs.update(
                    await _discover_search_pages(candidate, candidate_searches, client)
                )
        except PaginationFetchError as exc:
            if exc.last_status in _GONE_STATUSES:
                continue
            raise
        if candidate_jobs:
            return candidate, candidate_jobs

    return initial, jobs


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover every canonical detail URL on one Jobvite career site."""
    _ = pw
    initial = _board_identity(board)
    resolved, jobs = await resolve_listing(initial, client, terminal=True)
    log.info(
        "jobvite.discovered",
        tenant=resolved.tenant,
        listing_url=resolved.listing_url,
        jobs=len(jobs),
    )
    return jobs


async def _probe_candidate(
    candidate: JobviteBoard,
    client: httpx.AsyncClient,
) -> tuple[JobviteBoard, set[str]] | None:
    try:
        return await resolve_listing(candidate, client, terminal=False)
    except TDMReservedError:
        raise
    except Exception:
        log.debug(
            "jobvite.probe_failed",
            listing_url=candidate.listing_url,
            exc_info=True,
        )
        return None


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked Jobvite public career sites."""
    _ = pw
    direct = jobvite_board_from_url(url)
    if direct is not None:
        if client is None:
            return {"tenant": direct.tenant, "listing_url": direct.listing_url}
        probed = await _probe_candidate(direct, client)
        if probed is None:
            return None
        resolved, jobs = probed
        return {
            "tenant": resolved.tenant,
            "listing_url": resolved.listing_url,
            "jobs": len(jobs),
        }

    if client is None:
        return None
    page = await fetch_page_text(url, client)
    if not page:
        return None
    candidates: dict[str, JobviteBoard] = {}
    for match in _PAGE_URL_RE.finditer(html.unescape(page)):
        candidate = jobvite_board_from_url(match.group(1))
        if candidate is not None:
            candidates[candidate.listing_url] = candidate
    for candidate in list(candidates.values())[:MAX_LINKED_LISTINGS]:
        probed = await _probe_candidate(candidate, client)
        if probed is None:
            continue
        resolved, jobs = probed
        log.info(
            "jobvite.detected_in_page",
            url=url,
            tenant=resolved.tenant,
            listing_url=resolved.listing_url,
        )
        return {
            "tenant": resolved.tenant,
            "listing_url": resolved.listing_url,
            "jobs": len(jobs),
        }
    return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    key = jobvite_board_from_metadata(metadata) or jobvite_board_from_url(board_url)
    if key is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        key.listing_url,
        filename="jobvite-listing.html",
        follow_redirects=False,
    )


register("jobvite", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
