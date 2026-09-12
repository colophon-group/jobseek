"""Computrabajo and PandaPe employer-profile monitor.

Computrabajo's country portals expose a server-rendered employer inventory.
The listing carries an explicit total, 20 stable job links per page, and
``?p=N`` pagination. Detail pages publish complete JobPosting JSON-LD and are
handled by the existing JSON-LD scraper.

PandaPe is Computrabajo/InfoJobs' hosted employer-portal product. Its
``/Vacancies`` route has the same useful contracts: an explicit localized
total, 20 stable ``/Detail/{id}`` links per page, and a last-page marker.

The explicit total is important for empty employer profiles: a verified zero
page is authoritative, while a generic DOM monitor would only observe an
unexplained absence of links.
"""

from __future__ import annotations

import math
import re
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, register
from src.shared.http_retry import fetch_response_with_status_retries
from src.shared.tdm import TDMReservedError, check_response
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

PAGE_SIZE = 20
MAX_JOBS = 10_000

_COMPANY_PATH_RE = re.compile(
    r"^/empresas/ofertas-de-trabajo-de-[a-z0-9][a-z0-9-]*-([0-9a-f]{16})/?$",
    re.IGNORECASE,
)
_JOB_PATH_RE = re.compile(
    r"^/ofertas-de-trabajo/oferta-de-trabajo-de-[^/?#]+-"
    r"([0-9a-f]{32})/?$",
    re.IGNORECASE,
)
_COUNT_RE = re.compile(r"^\s*([0-9][0-9.,]*)\s+ofertas?\s+de\s+trabajo\b", re.IGNORECASE)
_PANDAPE_HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.pandape\."
    r"(?:infojobs\.com\.br|computrabajo\.com)",
    re.IGNORECASE,
)
_PANDAPE_PATH_RE = re.compile(r"/vacancies/?", re.IGNORECASE)
_PANDAPE_JOB_PATH_RE = re.compile(r"/detail/([1-9][0-9]{0,15})/?", re.IGNORECASE)
_PANDAPE_COUNT_RE = re.compile(
    r"^\s*([0-9][0-9.,]*)\s+(?:vagas|ofertas)\s+de\s+(?:emprego|empleo)\s*$",
    re.IGNORECASE,
)


class _ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.canonical: str | None = None
        self.meta_title: str | None = None
        self.job_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta" and (attributes.get("name") or "").casefold() == "title":
            self.meta_title = attributes.get("content")
            return
        if tag == "link" and "canonical" in (attributes.get("rel") or "").casefold().split():
            self.canonical = attributes.get("href")
            return
        if tag != "a" or "js-o-link" not in (attributes.get("class") or "").split():
            return
        href = attributes.get("href")
        if href:
            self.job_hrefs.append(href)


class _PandapeListingParser(HTMLParser):
    """Read PandaPe's server-rendered inventory and pagination contract."""

    def __init__(self) -> None:
        super().__init__()
        self.has_vacancy_section = False
        self.job_hrefs: list[str] = []
        self.total_chunks: list[str] = []
        self._total_depth = 0
        self.is_last: str | None = None
        self.page_number: str | None = None
        self.page_size: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "section" and attributes.get("id") == "VacancySection":
            self.has_vacancy_section = True
        if self._total_depth:
            self._total_depth += 1
        elif tag == "div" and {"color-title", "font-3xl"} <= classes:
            self._total_depth = 1
        if tag == "a" and "card-vacancy" in classes:
            href = attributes.get("href")
            if href:
                self.job_hrefs.append(href)
        if tag == "input":
            field = attributes.get("id")
            if field == "hdn_isLast":
                self.is_last = attributes.get("value")
            elif field == "hdn_PageNumber":
                self.page_number = attributes.get("value")
            elif field == "hdn_PageSize":
                self.page_size = attributes.get("value")

    def handle_endtag(self, tag: str) -> None:
        _ = tag
        if self._total_depth:
            self._total_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._total_depth:
            self.total_chunks.append(data)


def _profile_from_url(url: str) -> tuple[str, str] | None:
    """Return ``(host, company_id)`` for an exact unfiltered employer URL."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or not re.fullmatch(r"[a-z]{2}\.computrabajo\.com", host)
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None
    match = _COMPANY_PATH_RE.fullmatch(parsed.path)
    return (host, match.group(1).casefold()) if match else None


def _pandape_from_url(url: str) -> tuple[str, bool] | None:
    """Return ``(host, proxy_required)`` for an exact PandaPe listing URL."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or _PANDAPE_HOST_RE.fullmatch(host) is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or _PANDAPE_PATH_RE.fullmatch(parsed.path) is None
    ):
        return None
    return host, host.endswith(".pandape.computrabajo.com")


def _canonical_job_url(board_url: str, href: str) -> str | None:
    board_identity = _profile_from_url(board_url)
    if board_identity is None:
        return None
    host, _company_id = board_identity
    absolute, _fragment = urldefrag(urljoin(board_url, href))
    encoded = str(httpx.URL(absolute))
    try:
        parsed = urlparse(encoded)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
    ):
        return None
    if _JOB_PATH_RE.fullmatch(parsed.path) is None:
        return None
    return f"https://{host}{parsed.path}"


def _canonical_pandape_job_url(board_url: str, href: str) -> str | None:
    identity = _pandape_from_url(board_url)
    if identity is None:
        return None
    host, _proxy_required = identity
    absolute, fragment = urldefrag(urljoin(board_url, href))
    if fragment:
        return None
    encoded = str(httpx.URL(absolute))
    try:
        parsed = urlparse(encoded)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or _PANDAPE_JOB_PATH_RE.fullmatch(parsed.path) is None
    ):
        return None
    return f"https://{host}{parsed.path}"


def _parse_count(value: str | None) -> int | None:
    match = _COUNT_RE.match(value or "")
    if match is None:
        return None
    digits = re.sub(r"[^0-9]", "", match.group(1))
    return int(digits) if digits else None


def _parse_listing(
    html: str,
    *,
    board_url: str,
    requested_page: int,
) -> tuple[set[str], int]:
    identity = _profile_from_url(board_url)
    if identity is None:
        raise ValueError(f"Invalid Computrabajo employer URL: {board_url!r}")

    parser = _ListingParser()
    parser.feed(html)
    if parser.canonical is None or _profile_from_url(parser.canonical) != identity:
        raise ValueError("Computrabajo listing omitted the expected canonical employer identity")
    total = _parse_count(parser.meta_title)
    if total is None:
        raise ValueError("Computrabajo listing omitted its explicit job total")

    urls: set[str] = set()
    for href in parser.job_hrefs:
        canonical = _canonical_job_url(board_url, href)
        if canonical is None:
            raise ValueError("Computrabajo listing returned an invalid job URL")
        if canonical in urls:
            raise ValueError("Computrabajo listing repeated a job URL on one page")
        urls.add(canonical)

    expected = min(PAGE_SIZE, max(0, total - (requested_page - 1) * PAGE_SIZE))
    if len(urls) != expected:
        raise ValueError(
            f"Computrabajo page {requested_page} returned {len(urls)} jobs, expected {expected}"
        )
    return urls, total


def _parse_pandape_listing(
    html: str,
    *,
    board_url: str,
    requested_page: int,
) -> tuple[set[str], int]:
    if _pandape_from_url(board_url) is None:
        raise ValueError(f"Invalid PandaPe employer URL: {board_url!r}")

    parser = _PandapeListingParser()
    parser.feed(html)
    count_text = " ".join(" ".join(parser.total_chunks).split())
    count_match = _PANDAPE_COUNT_RE.fullmatch(count_text)
    if not parser.has_vacancy_section or count_match is None:
        raise ValueError("PandaPe listing omitted its provider identity or explicit job total")
    raw_total = count_match.group(1)
    digits = re.sub(r"[^0-9]", "", raw_total)
    if not digits:
        raise ValueError("PandaPe listing returned an invalid job total")
    total = int(digits)
    if parser.page_size != str(PAGE_SIZE) or parser.page_number != str(requested_page):
        raise ValueError("PandaPe listing omitted or changed its pagination contract")
    expected_last = requested_page * PAGE_SIZE >= total
    if (parser.is_last or "").casefold() != str(expected_last).casefold():
        raise ValueError("PandaPe listing returned an inconsistent last-page marker")

    urls: set[str] = set()
    for href in parser.job_hrefs:
        canonical = _canonical_pandape_job_url(board_url, href)
        if canonical is None:
            raise ValueError("PandaPe listing returned an invalid job URL")
        if canonical in urls:
            raise ValueError("PandaPe listing repeated a job URL on one page")
        urls.add(canonical)

    expected = min(PAGE_SIZE, max(0, total - (requested_page - 1) * PAGE_SIZE))
    if len(urls) != expected:
        raise ValueError(
            f"PandaPe page {requested_page} returned {len(urls)} jobs, expected {expected}"
        )
    return urls, total


def _page_url(board_url: str, page: int) -> str:
    if page == 1:
        return board_url
    param = "pageNumber" if _pandape_from_url(board_url) is not None else "p"
    return f"{board_url.rstrip('/')}?{param}={page}"


async def _fetch_listing(
    client: httpx.AsyncClient,
    board_url: str,
    page: int,
) -> tuple[set[str], int]:
    url = _page_url(board_url, page)
    response = await fetch_response_with_status_retries(
        client,
        url,
        retry_limits={403: 1, 429: 2},
        same_origin_redirects=True,
        log_event="computrabajo.list_backoff",
    )
    if response.status_code in {404, 410}:
        raise BoardGoneError(
            "Computrabajo employer profile no longer exists",
            url=url,
            status_code=response.status_code,
        )
    response.raise_for_status()
    html = response.text
    check_response(response, body_excerpt=html[:500_000])
    parser = _parse_pandape_listing if _pandape_from_url(board_url) is not None else _parse_listing
    return parser(html, board_url=board_url, requested_page=page)


async def _discover_urls(board_url: str, client: httpx.AsyncClient) -> tuple[set[str], int]:
    first_urls, total = await _fetch_listing(client, board_url, 1)
    target = min(total, MAX_JOBS)
    pages = math.ceil(target / PAGE_SIZE) if target else 0
    urls = set(first_urls)
    for page in range(2, pages + 1):
        page_urls, page_total = await _fetch_listing(client, board_url, page)
        if page_total != total:
            raise ValueError("Computrabajo inventory changed during pagination")
        overlap = urls & page_urls
        if overlap:
            raise ValueError(f"Computrabajo page {page} repeated {len(overlap)} jobs")
        urls.update(page_urls)
    if len(urls) != target:
        raise ValueError(f"Computrabajo discovered {len(urls)} jobs, expected {target}")
    return urls, total


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover every active URL from one Computrabajo employer profile."""
    _ = pw
    board_url = board["board_url"]
    if _profile_from_url(board_url) is None and _pandape_from_url(board_url) is None:
        raise ValueError(f"Invalid Computrabajo/PandaPe employer URL: {board_url!r}")
    urls, total = await _discover_urls(board_url, client)
    log.info("computrabajo.discovered", board_url=board_url, jobs=len(urls), total=total)
    return truncated_url_result(urls) if total > MAX_JOBS else urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize and, when possible, verify a Computrabajo employer profile."""
    _ = pw
    identity = _profile_from_url(url)
    pandape = _pandape_from_url(url)
    if identity is None and pandape is None:
        return None
    if pandape is not None:
        host, proxy_required = pandape
        result: dict = {"host": host, "variant": "pandape"}
        if proxy_required:
            result["proxy"] = True
    else:
        assert identity is not None
        host, company_id = identity
        result = {"host": host, "company_id": company_id}
    if client is None:
        return result
    try:
        _urls, total = await _fetch_listing(client, url, 1)
    except TDMReservedError:
        raise
    except BoardGoneError:
        return None
    except Exception:
        log.debug("computrabajo.probe_failed", url=url, exc_info=True)
        return result
    result["jobs"] = total
    return result


register("computrabajo", discover, cost=10, can_handle=can_handle)
