"""Papa Johns branded careers monitor.

The public Papa Johns portal mixes corporate and franchise postings on a
server-rendered listing. It exposes an explicit inventory count, stable job
detail URLs, and ``page_jobs`` pagination. The origin blocks datacenter egress,
so production boards opt into the shared proxy transport; the monitor itself
remains transport-agnostic and fail-closed on incomplete pagination.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
import structlog

from src.core.monitors import register
from src.shared.http_retry import fetch_with_retry

log = structlog.get_logger()

BOARD_URL = "https://jobs.papajohns.com/jobs/"
_HOST = "jobs.papajohns.com"
_COUNT_RE = re.compile(r"\bfound\s+([0-9][0-9,]*)\s+jobs\s+at\s+papa\s+johns\b", re.I)
_JOB_PATH_RE = re.compile(r"^/job/[0-9]+/[a-z0-9][a-z0-9-]*/?$", re.I)
_PAGE_PARAM = "page_jobs"
_MAX_PAGES = 1_000
_PAGE_MAX_CHARS = 10_000_000
_PAGE_CONCURRENCY = 4


def _is_board_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() == _HOST
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and parsed.path.rstrip("/").casefold() == "/jobs"
        and not parsed.query
        and not parsed.fragment
    )


def _canonical_job_url(base_url: str, href: str) -> str | None:
    try:
        parsed = urlparse(urljoin(base_url, href))
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or _JOB_PATH_RE.fullmatch(parsed.path) is None
    ):
        return None
    return f"https://{_HOST}{parsed.path.rstrip('/')}/"


@dataclass(slots=True)
class _ParsedListing:
    urls: set[str] = field(default_factory=set)
    total_jobs: int | None = None
    total_pages: int = 1


class _ListingParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.urls: set[str] = set()
        self.pages: set[int] = {1}
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return

        job_url = _canonical_job_url(self.base_url, href)
        if job_url is not None:
            self.urls.add(job_url)

        absolute = urlparse(urljoin(self.base_url, href))
        if (
            absolute.scheme.casefold() == "https"
            and (absolute.hostname or "").casefold() == _HOST
            and absolute.path.rstrip("/").casefold() == "/jobs"
        ):
            for raw_page in parse_qs(absolute.query).get(_PAGE_PARAM, []):
                if raw_page.isdigit() and 1 <= int(raw_page) <= _MAX_PAGES:
                    self.pages.add(int(raw_page))

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.text.append(value)

    def parsed(self) -> _ParsedListing:
        match = _COUNT_RE.search(" ".join(self.text))
        total = int(match.group(1).replace(",", "")) if match else None
        return _ParsedListing(
            urls=self.urls,
            total_jobs=total,
            total_pages=max(self.pages),
        )


def _parse_listing(html: str, base_url: str = BOARD_URL) -> _ParsedListing:
    parser = _ListingParser(base_url)
    parser.feed(html)
    parser.close()
    parsed = parser.parsed()
    if parsed.total_jobs is None:
        raise ValueError("Papa Johns listing omitted its explicit inventory count")
    if parsed.total_jobs > 0 and not parsed.urls:
        raise ValueError("Papa Johns listing exposed jobs but no canonical job URLs")
    if not 1 <= parsed.total_pages <= _MAX_PAGES:
        raise ValueError("Papa Johns listing exposed an invalid page count")
    return parsed


def _page_url(page: int) -> str:
    if page <= 1:
        return BOARD_URL
    parsed = urlparse(BOARD_URL)
    return urlunparse(parsed._replace(query=urlencode({_PAGE_PARAM: page})))


async def _fetch_listing(client: httpx.AsyncClient, page: int) -> _ParsedListing:
    url = _page_url(page)
    html = await fetch_with_retry(
        client,
        url,
        max_chars=_PAGE_MAX_CHARS,
        transient_403=True,
    )
    if html is None:
        raise RuntimeError(f"Papa Johns listing page {page} could not be fetched")
    return _parse_listing(html, url)


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover the complete Papa Johns branded job inventory."""
    _ = pw
    if not _is_board_url(board["board_url"]):
        raise ValueError(f"Invalid Papa Johns board URL: {board['board_url']!r}")

    first = await _fetch_listing(client, 1)
    urls = set(first.urls)
    total = first.total_jobs or 0
    pages = first.total_pages

    for start in range(2, pages + 1, _PAGE_CONCURRENCY):
        batch_pages = range(start, min(start + _PAGE_CONCURRENCY, pages + 1))
        listings = await asyncio.gather(*(_fetch_listing(client, page) for page in batch_pages))
        for listing in listings:
            if listing.total_jobs != total or listing.total_pages != pages:
                raise ValueError("Papa Johns inventory changed during pagination")
            urls.update(listing.urls)

    if len(urls) != total:
        raise ValueError(f"Papa Johns discovered {len(urls)} jobs, expected {total}")
    log.info("papa_johns.discovered", jobs=len(urls), pages=pages)
    return urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize the exact unfiltered Papa Johns branded jobs URL."""
    _ = pw
    if not _is_board_url(url):
        return None
    result: dict = {"host": _HOST, "proxy_required": True}
    if client is None:
        return result
    try:
        parsed = await _fetch_listing(client, 1)
    except Exception:
        log.debug("papa_johns.probe_failed", url=url, exc_info=True)
        return result
    result.update({"jobs": parsed.total_jobs, "pages": parsed.total_pages})
    return result


register("papa_johns", discover, cost=45, can_handle=can_handle)
