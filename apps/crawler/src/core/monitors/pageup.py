"""PageUp server-rendered listing monitor.

PageUp exposes complete job anchors and authoritative totals in static HTML.
The monitor reuses Jobseek's generic DOM link extraction, HTTP retry, bot
challenge, raw-artifact, and rich-stream machinery.  Detail descriptions are
left to the existing static DOM scraper on its normal enrichment schedule.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, fetch_page_text, register
from src.core.monitors.dom import _extract_links_static, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.pageup import (
    PageUpBoard,
    pageup_board_from_metadata,
    pageup_board_from_url,
    pageup_job_identity,
    pageup_listing_boards_from_html,
    pageup_pagination_identity,
)
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

PAGE_SIZE = 500
PROBE_PAGE_SIZE = 20
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
MAX_HTML_CHARS = 5_000_000
MAX_LINKED_BOARDS = 8
_TRANSIENT_STATUSES = frozenset({202, 401, 403, 429})
_GONE_STATUSES = frozenset({404, 410})
_JOB_LINK_RE = re.compile(
    r"^https://careers\.pageuppeople\.com/[1-9]\d{0,8}/"
    r"[a-z][a-z0-9-]{0,15}/[a-z]{2,3}(?:-[a-z0-9]{2,8})*/"
    r"job/[1-9]\d{0,18}/[^/?#\s]{1,200}(?:[?#]|$)",
    re.IGNORECASE,
)
_PAGE_URL_RE = re.compile(
    r"(https?://careers\.pageuppeople\.com/[1-9]\d{0,8}/"
    r"[a-z][a-z0-9-]{0,15}/[a-z]{2,3}(?:-[a-z0-9]{2,8})*"
    r"(?:/listing/?(?:\?[^#\"'<\s]*)?|/job/[1-9]\d{0,18}/"
    r"[^/?#\"'<\s]{1,200})?/?)(?=[#\"'<\s]|$)",
    re.IGNORECASE,
)
_GENERIC_JOB_TITLES = frozenset(
    {
        "apply",
        "apply now",
        "learn more",
        "read more",
        "view details",
        "view job",
        "view position",
    }
)


class _ListingFactsParser(HTMLParser):
    """Collect titles, total markers, and explicit PageUp next links."""

    def __init__(self, request_url: str, board: PageUpBoard):
        super().__init__()
        self.request_url = request_url
        self.board = board
        self.titles: dict[str, str] = {}
        self.conflicting_titles = False
        self.totals: set[int] = set()
        self.next_pages: set[tuple[int, int]] = set()
        self.next_remaining: dict[tuple[int, int], set[int]] = {}
        self.invalid_next = False
        self._job_href: str | None = None
        self._job_text: list[str] = []
        self._in_total = False
        self._total_text: list[str] = []
        self._more_identity: tuple[int, int] | None = None
        self._more_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        classes = values.get("class", "").split()
        if tag == "a" and "job-link" in classes:
            self._job_href = values.get("href")
            self._job_text = []
        if tag == "a" and "more-link" in classes:
            href = values.get("href", "")
            identity = pageup_pagination_identity(urljoin(self.request_url, href), self.board)
            data_page = values.get("data-page", "")
            data_items = values.get("data-page-items", "")
            if (
                identity is None
                or not data_page.isdigit()
                or not data_items.isdigit()
                or identity != (int(data_page), int(data_items))
            ):
                self.invalid_next = True
            else:
                self.next_pages.add(identity)
                self._more_identity = identity
                self._more_text = []
        if tag == "span" and "result-count" in classes:
            self._in_total = True
            self._total_text = []

    def handle_data(self, data: str) -> None:
        if self._job_href is not None:
            self._job_text.append(data)
        if self._in_total:
            self._total_text.append(data)
        if self._more_identity is not None:
            self._more_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._job_href is not None:
            target = urljoin(self.request_url, self._job_href)
            identity = pageup_job_identity(target, self.board)
            title = " ".join("".join(self._job_text).split())
            if identity is not None and title:
                canonical = self.board.job_url(*identity)
                previous = self.titles.get(canonical)
                previous_generic = (
                    previous is not None and previous.casefold() in _GENERIC_JOB_TITLES
                )
                title_generic = title.casefold() in _GENERIC_JOB_TITLES
                if previous is None or (previous_generic and not title_generic):
                    self.titles[canonical] = title
                elif not title_generic and not previous_generic and previous != title:
                    self.conflicting_titles = True
            self._job_href = None
            self._job_text = []
        if tag == "a" and self._more_identity is not None:
            numbers = re.findall(r"\d[\d,]*", " ".join(self._more_text))
            if not numbers:
                self.invalid_next = True
            else:
                remaining = int(numbers[-1].replace(",", ""))
                self.next_remaining.setdefault(self._more_identity, set()).add(remaining)
            self._more_identity = None
            self._more_text = []
        if tag == "span" and self._in_total:
            raw = "".join(self._total_text).strip().replace(",", "")
            if raw.isdigit():
                self.totals.add(int(raw))
            self._in_total = False
            self._total_text = []


def _board_identity(board: dict) -> PageUpBoard:
    metadata = board.get("metadata") or {}
    configured = pageup_board_from_metadata(metadata) if isinstance(metadata, dict) else None
    direct = pageup_board_from_url(board["board_url"])
    identity_keys = {"instance", "source_pointer", "locale", "listing_url"}
    has_configured_identity = isinstance(metadata, dict) and bool(identity_keys & metadata.keys())
    if has_configured_identity and configured is None:
        raise ValueError("Invalid or internally inconsistent PageUp monitor configuration")
    if configured is not None and direct is not None and configured != direct:
        raise ValueError("Configured PageUp identity does not match the board URL")
    resolved = configured or direct
    if resolved is None:
        raise ValueError(
            f"Cannot derive a PageUp board from {board['board_url']!r}; configure "
            "metadata.instance, source_pointer, and locale"
        )
    return resolved


async def _fetch_listing_page(
    board: PageUpBoard,
    client: httpx.AsyncClient,
    *,
    page: int,
    page_size: int,
    terminal: bool,
) -> tuple[str, str]:
    url = board.page_url(page, page_size=page_size)
    try:
        document = await fetch_text_page_with_retry(
            client,
            url,
            follow_redirects=False,
            retryable_statuses=_TRANSIENT_STATUSES,
            end_of_pagination_statuses=(),
            require_nonempty=True,
            max_chars=MAX_HTML_CHARS + 1,
            log_event="pageup.listing_backoff",
        )
    except PaginationFetchError as exc:
        if terminal and exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "PageUp board no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    if document is None:  # Strict status handling makes this unreachable.
        raise RuntimeError(f"PageUp listing fetch returned no page for {url!r}")
    _raise_if_bot_challenge(url, document)
    if len(document) > MAX_HTML_CHARS:
        raise ValueError("PageUp listing exceeded the HTML safety cap")
    return url, document


def _parse_listing_page(
    document: str,
    request_url: str,
    board: PageUpBoard,
    *,
    page: int,
    page_size: int,
    expected_total: int | None,
) -> tuple[list[DiscoveredJob], int, bool]:
    assertions = pageup_listing_boards_from_html(document)
    if assertions and assertions != {board}:
        raise ValueError("PageUp listing identity does not match the configured board")

    parser = _ListingFactsParser(request_url, board)
    parser.feed(document)
    parser.close()
    if parser.conflicting_titles:
        raise ValueError("PageUp repeated a job URL with conflicting titles")

    raw_urls = _extract_links_static(document, request_url, _JOB_LINK_RE)
    canonical_urls = {
        board.job_url(*identity)
        for url in raw_urls
        if (identity := pageup_job_identity(url, board)) is not None
    }
    if canonical_urls != parser.titles.keys():
        raise ValueError("PageUp listing exposed job links without stable visible titles")

    offset = (page - 1) * page_size
    if not assertions and not canonical_urls:
        raise ValueError("PageUp markerless listing cannot prove an empty inventory")
    if len(parser.totals) > 1:
        raise ValueError("PageUp listing exposed conflicting authoritative result counts")
    if parser.totals:
        total = next(iter(parser.totals))
    elif len(parser.next_pages) == 1:
        next_identity = next(iter(parser.next_pages))
        remaining_values = parser.next_remaining.get(next_identity, set())
        if len(remaining_values) != 1:
            raise ValueError("PageUp next link omitted a unique remaining-job count")
        total = offset + len(canonical_urls) + next(iter(remaining_values))
    elif not parser.next_pages:
        total = offset + len(canonical_urls)
    else:
        raise ValueError("PageUp listing exposed conflicting next-page links")
    if total > MAX_JOBS:
        raise ValueError(f"PageUp listing exceeded the {MAX_JOBS:,}-job safety cap")
    if expected_total is not None and total != expected_total:
        raise ValueError("PageUp result count changed during pagination")

    expected_jobs = max(min(page_size, total - offset), 0)
    if len(canonical_urls) != expected_jobs:
        raise ValueError(
            f"PageUp page {page} exposed {len(canonical_urls)} unique jobs; "
            f"expected {expected_jobs} from total {total}"
        )
    has_next = offset + expected_jobs < total
    expected_next = {(page + 1, page_size)} if has_next else set()
    if parser.invalid_next or parser.next_pages != expected_next:
        raise ValueError("PageUp listing exposed invalid or incomplete next-page navigation")
    if has_next:
        expected_remaining = total - (offset + expected_jobs)
        if parser.next_remaining != {(page + 1, page_size): {expected_remaining}}:
            raise ValueError("PageUp next link exposed an inconsistent remaining-job count")
    elif parser.next_remaining:
        raise ValueError("PageUp terminal page exposed a remaining-job count")

    jobs = [DiscoveredJob(url=url, title=parser.titles[url]) for url in sorted(canonical_urls)]
    return jobs, total, has_next


async def stream(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> AsyncIterator[list[DiscoveredJob]]:
    """Stream validated listing pages while preserving a constant total."""

    _ = pw
    resolved = _board_identity(board)
    seen: set[str] = set()
    expected_total: int | None = None
    page = 1
    while True:
        request_url, document = await _fetch_listing_page(
            resolved,
            client,
            page=page,
            page_size=PAGE_SIZE,
            terminal=page == 1,
        )
        jobs, total, has_next = _parse_listing_page(
            document,
            request_url,
            resolved,
            page=page,
            page_size=PAGE_SIZE,
            expected_total=expected_total,
        )
        expected_total = total
        urls = {job.url for job in jobs}
        if overlap := seen & urls:
            raise ValueError(f"PageUp pagination repeated {len(overlap)} jobs")
        seen.update(urls)
        if jobs or not has_next:
            yield jobs
        if not has_next:
            if len(seen) != total:
                raise ValueError("PageUp pagination ended before its authoritative total")
            log.info(
                "pageup.discovered",
                instance=resolved.instance,
                jobs=len(seen),
                pages=page,
            )
            return
        page += 1
        if page > MAX_PAGES:
            raise ValueError("PageUp pagination exceeded the page safety cap")


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Materialized form used by workspace commands and focused tests."""

    jobs: list[DiscoveredJob] = []
    async for batch in stream(board, client, pw=pw):
        jobs.extend(batch)
    return jobs


async def probe_listing(board: PageUpBoard, client: httpx.AsyncClient) -> int | None:
    """Validate one small first page and return its authoritative total."""

    try:
        request_url, document = await _fetch_listing_page(
            board,
            client,
            page=1,
            page_size=PROBE_PAGE_SIZE,
            terminal=False,
        )
        _jobs, total, _has_next = _parse_listing_page(
            document,
            request_url,
            board,
            page=1,
            page_size=PROBE_PAGE_SIZE,
            expected_total=None,
        )
        return total
    except TDMReservedError:
        raise
    except Exception:
        log.debug("pageup.probe_failed", listing_url=board.listing_url, exc_info=True)
        return None


def _result(board: PageUpBoard, jobs: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "instance": board.instance,
        "source_pointer": board.source_pointer,
        "locale": board.locale,
        "listing_url": board.listing_url,
    }
    if jobs is not None:
        result["jobs"] = jobs
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked PageUp boards without guessing IDs."""

    _ = pw
    direct = pageup_board_from_url(html.unescape(url))
    if direct is not None:
        if client is None:
            return _result(direct)
        total = await probe_listing(direct, client)
        return _result(direct, total) if total is not None else None
    if client is None:
        return None

    document = await fetch_page_text(url, client)
    if not document:
        return None
    candidates: dict[str, PageUpBoard] = {}
    for matched in _PAGE_URL_RE.findall(html.unescape(document)):
        candidate = pageup_board_from_url(matched)
        if candidate is not None:
            candidates.setdefault(candidate.listing_url, candidate)
        if len(candidates) >= MAX_LINKED_BOARDS:
            break
    for candidate in candidates.values():
        total = await probe_listing(candidate, client)
        if total is not None:
            log.info("pageup.detected_in_page", url=url, listing_url=candidate.listing_url)
            return _result(candidate, total)
    return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    resolved = _board_identity({"board_url": board_url, "metadata": metadata})
    await save_text_response(
        artifact_dir,
        client,
        resolved.page_url(1, page_size=PROBE_PAGE_SIZE),
        filename="pageup-listing.html",
        follow_redirects=False,
    )


register(
    "pageup",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    stream=stream,
    save_raw=save_raw,
)
