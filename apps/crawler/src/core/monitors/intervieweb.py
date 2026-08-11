"""Intervieweb / In-recruiting career-site monitor.

Intervieweb renders the first vacancy page in the career-page HTML and loads
subsequent pages from a CSRF-protected form endpoint with ``POST`` requests.
The endpoint and token are generated per page load, so they must be resolved
afresh on every discovery cycle rather than persisted in board configuration.

This adapter returns canonical detail URLs.  Intervieweb detail pages expose
complete schema.org ``JobPosting`` data, so the shared JSON-LD scraper owns
content extraction.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx
import structlog

from src.core.monitors import register
from src.core.monitors.dom import _raise_if_bot_challenge
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_PAGES = 1_000
MAX_HTML_BYTES = 5_000_000

_TRANSIENT_STATUSES = frozenset({202, 401, 403, 429})
_PAGE_COUNT_RE = re.compile(r"\bPage\s+\d+\s+of\s+(\d+)\b", re.IGNORECASE)
_SECTION_RE = re.compile(
    r"[\"']section[\"']\s*:\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_PROVIDER_MARKERS = ("vacancyListCareer", "researchAnnounces")


class _ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ajax_url: str | None = None
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "input" and attributes.get("id") == "url-for-announces":
            self.ajax_url = attributes.get("value")
        elif tag == "a" and attributes.get("href"):
            self.hrefs.append(attributes["href"] or "")


def _is_intervieweb_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and (host == "intervieweb.it" or host.endswith(".intervieweb.it"))
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _same_origin(candidate: str, board_url: str) -> bool:
    try:
        parsed = urlparse(candidate)
        board = urlparse(board_url)
        port = parsed.port
        board_port = board.port
    except ValueError:
        return False
    return (
        parsed.scheme == board.scheme == "https"
        and (parsed.hostname or "").lower() == (board.hostname or "").lower()
        and port in {None, 443}
        and board_port in {None, 443}
        and parsed.username is None
        and parsed.password is None
    )


def _canonical_job_url(raw_url: str, board_url: str) -> str | None:
    candidate = urljoin(board_url, raw_url)
    if not _same_origin(candidate, board_url):
        return None
    parsed = urlparse(candidate)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 3 or segments[0].lower() != "jobs":
        return None
    if not segments[1] or re.fullmatch(r"[A-Za-z]{2}", segments[2]) is None:
        return None
    return f"https://{parsed.hostname}/jobs/{segments[1]}/{segments[2].lower()}/"


def _parse_job_urls(page: str, board_url: str) -> set[str]:
    parser = _ListingParser()
    parser.feed(page)
    return {
        canonical
        for href in parser.hrefs
        if (canonical := _canonical_job_url(href, board_url)) is not None
    }


def _page_count(page: str) -> int:
    counts = [int(value) for value in _PAGE_COUNT_RE.findall(page)]
    count = max(counts, default=1)
    if count < 1 or count > MAX_PAGES:
        raise ValueError(f"Intervieweb advertised invalid page count {count}")
    return count


def _pagination_protocol(page: str, board_url: str) -> tuple[str, str]:
    parser = _ListingParser()
    parser.feed(page)
    if not parser.ajax_url:
        raise ValueError("Intervieweb listing omitted its pagination endpoint")
    ajax_url = urljoin(board_url, parser.ajax_url)
    if not _same_origin(ajax_url, board_url):
        raise ValueError("Intervieweb pagination endpoint changed origin")

    parsed = urlparse(ajax_url)
    query = parse_qs(parsed.query)
    if not parsed.path.endswith("/app.php") or query.get("module") != ["newcareer"]:
        raise ValueError("Intervieweb pagination endpoint has an unexpected shape")

    section_match = _SECTION_RE.search(page)
    if section_match is None:
        raise ValueError("Intervieweb listing omitted its pagination section")
    return ajax_url, section_match.group(1)


async def _fetch_first_page(board_url: str, client: httpx.AsyncClient) -> str:
    page = await fetch_text_page_with_retry(
        client,
        board_url,
        require_nonempty=True,
        retryable_statuses=_TRANSIENT_STATUSES,
        end_of_pagination_statuses=(),
        max_bytes=MAX_HTML_BYTES,
        log_event="intervieweb.listing_backoff",
    )
    if page is None:  # Strict status handling makes this unreachable.
        raise RuntimeError(f"Intervieweb listing fetch returned no page for {board_url!r}")
    _raise_if_bot_challenge(board_url, page)
    has_endpoint_input = 'id="url-for-announces"' in page or "id='url-for-announces'" in page
    if not has_endpoint_input or not all(marker in page for marker in _PROVIDER_MARKERS):
        raise ValueError("Intervieweb provider markers are missing")
    return page


async def _fetch_page(
    ajax_url: str,
    board_url: str,
    section: str,
    page_number: int,
    client: httpx.AsyncClient,
) -> str:
    content = urlencode(
        {
            "act1": "vacancyListCareer",
            "section": section,
            "order": "name",
            "page": str(page_number),
            "country": "",
            "region": "",
            "function": "",
            "project": "",
            "text": "",
            "division": "",
            "company": "",
        }
    )
    raw = await fetch_text_page_with_retry(
        client,
        ajax_url,
        method="POST",
        content=content,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": board_url,
            "X-Requested-With": "XMLHttpRequest",
        },
        require_nonempty=True,
        retryable_statuses=_TRANSIENT_STATUSES,
        end_of_pagination_statuses=(),
        max_bytes=MAX_HTML_BYTES,
        log_event="intervieweb.page_backoff",
    )
    if raw is None:  # Strict status handling makes this unreachable.
        raise RuntimeError(f"Intervieweb pagination returned no page {page_number}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Intervieweb pagination returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("Intervieweb pagination reported failure")
    page = payload.get("data")
    if not isinstance(page, str):
        raise ValueError("Intervieweb pagination omitted HTML data")
    _raise_if_bot_challenge(ajax_url, page)
    return page


async def _discover_url(
    board_url: str,
    client: httpx.AsyncClient,
) -> tuple[set[str], int]:
    first_page = await _fetch_first_page(board_url, client)
    pages = _page_count(first_page)
    urls = _parse_job_urls(first_page, board_url)
    if len(urls) > MAX_JOBS:
        raise ValueError(f"Intervieweb listing exceeded the {MAX_JOBS:,}-job safety cap")

    if pages == 1:
        return urls, pages

    ajax_url, section = _pagination_protocol(first_page, board_url)
    for page_number in range(2, pages + 1):
        page = await _fetch_page(ajax_url, board_url, section, page_number, client)
        advertised_pages = _page_count(page)
        if advertised_pages != pages:
            raise ValueError(
                f"Intervieweb page count changed during pagination ({pages} -> {advertised_pages})"
            )
        page_urls = _parse_job_urls(page, board_url)
        if not page_urls:
            raise ValueError(f"Intervieweb advertised page {page_number} returned no jobs")
        if page_urls <= urls:
            raise ValueError(f"Intervieweb pagination repeated page {page_number}")
        urls.update(page_urls)
        if len(urls) > MAX_JOBS:
            raise ValueError(f"Intervieweb listing exceeded the {MAX_JOBS:,}-job safety cap")

    return urls, pages


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover all detail URLs from an Intervieweb career page."""
    _ = pw
    board_url = board["board_url"]
    if not _is_intervieweb_url(board_url):
        raise ValueError(f"Unsupported Intervieweb board URL {board_url!r}")
    urls, pages = await _discover_url(board_url, client)
    log.info("intervieweb.discovered", board_url=board_url, jobs=len(urls), pages=pages)
    return urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect and verify an Intervieweb/In-recruiting career page."""
    _ = pw
    if not _is_intervieweb_url(url):
        return None
    if client is None:
        return {"provider": "intervieweb"}
    try:
        urls, pages = await _discover_url(url, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("intervieweb.probe_failed", url=url, exc_info=True)
        return None
    return {"provider": "intervieweb", "jobs": len(urls), "pages": pages}


register("intervieweb", discover, cost=10, can_handle=can_handle)
