"""Prospective Career Center server-rendered listing monitor.

Prospective's legacy ``/public/v1/careercenter/{tenant}`` surface renders job
links on the server and paginates with form-encoded POST requests. Branded
CNAMEs can reject crawler traffic even while the canonical
``ohws.prospective.ch`` tenant remains available, so this monitor derives and
uses that canonical listing endpoint from the public career-center identity.

The monitor returns stable job-detail URLs. Detail pages expose complete
``JobPosting`` JSON-LD and are handled by the shared JSON-LD scraper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, register
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_PAGES = 5_000
MAX_HTML_CHARS = 2_000_000
_CANONICAL_HOST = "ohws.prospective.ch"
_PATH_RE = re.compile(r"^/public/v1/careercenter/(?P<tenant>\d+)/?$")
_LANG_RE = re.compile(r"^[a-z]{2}$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_PAGINATION_RE = re.compile(r"\bsendPagination\((\d+)\)")
_TRANSIENT_STATUSES = frozenset({403, 406})


@dataclass(frozen=True, slots=True)
class ProspectiveBoard:
    tenant: str
    lang: str = "de"

    @property
    def listing_url(self) -> str:
        return f"https://{_CANONICAL_HOST}/public/v1/careercenter/{self.tenant}/?lang={self.lang}"


@dataclass(frozen=True, slots=True)
class ProspectivePage:
    jobs: tuple[str, ...]
    offset: int
    limit: int
    lang: str
    pagination_offsets: tuple[int, ...]


def prospective_board_from_url(url: str) -> ProspectiveBoard | None:
    """Parse an unfiltered v1 Career Center URL, including branded CNAMEs."""

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    try:
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    match = _PATH_RE.fullmatch(parsed.path)
    if not match:
        return None
    params = parse_qs(parsed.query, keep_blank_values=True)
    if set(params) - {"lang"} or len(params.get("lang", [])) > 1:
        return None
    lang = params.get("lang", ["de"])[0].lower() or "de"
    if not _LANG_RE.fullmatch(lang):
        return None
    return ProspectiveBoard(tenant=match.group("tenant"), lang=lang)


def prospective_board_from_metadata(metadata: dict) -> ProspectiveBoard | None:
    tenant = str(metadata.get("tenant", ""))
    lang = str(metadata.get("lang", "de")).lower()
    if not tenant.isdigit() or not _LANG_RE.fullmatch(lang):
        return None
    return ProspectiveBoard(tenant=tenant, lang=lang)


def prospective_request_host(board_url: str, metadata: dict) -> str | None:
    """Return the canonical host requested by the listing monitor."""

    board = prospective_board_from_metadata(metadata) or prospective_board_from_url(board_url)
    return _CANONICAL_HOST if board is not None else None


def _stable_job_url(href: str, listing_url: str) -> str | None:
    candidate = urljoin(listing_url, href)
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or not _UUID_RE.fullmatch(parts[-1]):
        return None
    return parsed._replace(query="", fragment="").geturl()


class _ListingParser(HTMLParser):
    def __init__(self, listing_url: str) -> None:
        super().__init__()
        self.listing_url = listing_url
        self.has_form = False
        self.has_jobs_list = False
        self._jobs_div_depth = 0
        self.jobs: list[str] = []
        self.hidden: dict[str, str] = {}
        self.pagination_offsets: set[int] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and values.get("id") == "careercenter-form":
            self.has_form = True
        if tag == "div":
            if self._jobs_div_depth:
                self._jobs_div_depth += 1
            elif values.get("id") == "jobs-list":
                self.has_jobs_list = True
                self._jobs_div_depth = 1
        if tag == "input" and values.get("type", "").lower() == "hidden":
            name = values.get("name")
            value = values.get("value")
            if name and value is not None:
                self.hidden[name] = value
        onclick = values.get("onclick")
        if onclick:
            self.pagination_offsets.update(int(value) for value in _PAGINATION_RE.findall(onclick))
        if tag != "a" or not self._jobs_div_depth:
            return
        classes = set((values.get("class") or "").split())
        href = values.get("href")
        if "job" not in classes or not href:
            return
        if stable := _stable_job_url(href, self.listing_url):
            self.jobs.append(stable)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._jobs_div_depth:
            self._jobs_div_depth -= 1


def parse_prospective_page(html: str, listing_url: str) -> ProspectivePage | None:
    parser = _ListingParser(listing_url)
    parser.feed(html)
    if not parser.has_form or not parser.has_jobs_list:
        return None
    try:
        offset = int(parser.hidden.get("offset", "0"))
        limit = int(parser.hidden["limit"])
    except (KeyError, ValueError):
        return None
    lang = parser.hidden.get("lang", "").lower()
    if offset < 0 or limit < 1 or limit > 1_000 or not _LANG_RE.fullmatch(lang):
        return None
    jobs = tuple(dict.fromkeys(parser.jobs))
    if len(jobs) > limit:
        return None
    return ProspectivePage(
        jobs=jobs,
        offset=offset,
        limit=limit,
        lang=lang,
        pagination_offsets=tuple(sorted(parser.pagination_offsets)),
    )


async def _fetch_listing(
    board: ProspectiveBoard,
    client: httpx.AsyncClient,
    *,
    offset: int,
    limit: int = 10,
) -> str:
    headers = {"accept": "text/html,application/xhtml+xml"}
    method = "GET"
    content = None
    if offset:
        method = "POST"
        headers.update(
            {
                "content-type": "application/x-www-form-urlencoded",
                "referer": board.listing_url,
            }
        )
        content = urlencode({"offset": offset, "limit": limit, "lang": board.lang})
    try:
        body = await fetch_text_page_with_retry(
            client,
            board.listing_url,
            method=method,
            content=content,
            headers=headers,
            follow_redirects=True,
            retryable_statuses=_TRANSIENT_STATUSES,
            end_of_pagination_statuses=(),
            require_nonempty=True,
            max_chars=MAX_HTML_CHARS + 1,
            log_event="prospective.listing_backoff",
        )
    except PaginationFetchError as exc:
        if offset == 0 and exc.last_status in (404, 410):
            raise BoardGoneError(
                "Prospective Career Center no longer exists",
                url=board.listing_url,
                status_code=exc.last_status,
            ) from exc
        raise
    if body is None:
        raise RuntimeError("Prospective listing fetch returned no page")
    if len(body) > MAX_HTML_CHARS:
        raise ValueError("Prospective listing exceeded the HTML safety cap")
    return body


async def _collect_urls(
    board: ProspectiveBoard,
    client: httpx.AsyncClient,
) -> tuple[set[str], bool, int]:
    urls: set[str] = set()
    offset = 0
    expected_limit: int | None = None
    truncated = False

    for page_number in range(1, MAX_PAGES + 1):
        html = await _fetch_listing(board, client, offset=offset, limit=expected_limit or 10)
        page = parse_prospective_page(html, board.listing_url)
        if page is None:
            raise ValueError("Prospective response was not a valid Career Center listing")
        # Prospective resets the hidden offset to zero in every response even
        # when the POST body selected a later page. The returned job slice is
        # still correct; language and page size are the stable identity checks.
        if page.lang != board.lang:
            raise ValueError("Prospective pagination changed listing identity")
        if expected_limit is None:
            expected_limit = page.limit
        elif page.limit != expected_limit:
            raise ValueError("Prospective pagination changed page size")

        current = set(page.jobs)
        if current & urls:
            truncated = True
            if current <= urls:
                return urls, truncated, page_number
        remaining = max(MAX_JOBS - len(urls), 0)
        urls.update(sorted(current - urls)[:remaining])

        if len(urls) >= MAX_JOBS:
            truncated = True
            return urls, truncated, page_number
        if len(page.jobs) < page.limit:
            return urls, truncated, page_number

        offset += page.limit

    return urls, True, MAX_PAGES


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return every stable job URL from the unfiltered Career Center."""

    _ = pw
    metadata = board.get("metadata") or {}
    resolved = prospective_board_from_metadata(metadata) or prospective_board_from_url(
        board["board_url"]
    )
    if resolved is None:
        raise ValueError(
            "Prospective monitor requires an unfiltered "
            "/public/v1/careercenter/{tenant}/ URL or tenant metadata"
        )
    urls, truncated, pages = await _collect_urls(resolved, client)
    log_method = log.warning if truncated else log.info
    log_method(
        "prospective.discovered",
        tenant=resolved.tenant,
        jobs=len(urls),
        pages=pages,
        truncated=truncated,
    )
    return truncated_url_result(urls) if truncated else urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect v1 Career Centers through their canonical Prospective endpoint."""

    _ = pw
    board = prospective_board_from_url(url)
    if board is None:
        return None
    if client is None:
        return (
            {"tenant": board.tenant, "lang": board.lang}
            if (urlparse(url).hostname or "").lower() == _CANONICAL_HOST
            else None
        )
    try:
        urls, truncated, _pages = await _collect_urls(board, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("prospective.probe_failed", tenant=board.tenant, exc_info=True)
        return None
    if truncated:
        return None
    return {"tenant": board.tenant, "lang": board.lang, "jobs": len(urls)}


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    board = prospective_board_from_metadata(metadata) or prospective_board_from_url(board_url)
    if board is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        board.listing_url,
        filename="listing.html",
        headers={"accept": "text/html,application/xhtml+xml"},
        follow_redirects=True,
    )


register(
    "prospective",
    discover,
    cost=10,
    can_handle=can_handle,
    save_raw=save_raw,
)
