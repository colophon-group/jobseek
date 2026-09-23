"""JobConvo server-rendered career-page monitor.

JobConvo career pages publish the complete active inventory as regular HTML
and advertise additional pages through ordinary ``?page=N`` links.  Detail
pages are React applications, so the paired :mod:`src.core.scrapers.jobconvo`
scraper reads JobConvo's public detail API instead of rendering them.
"""

from __future__ import annotations

import re
from collections import deque
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import BoardGoneError, register
from src.shared.http_retry import fetch_text_page_with_retry

log = structlog.get_logger()

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_LISTING_PATH_RE = re.compile(
    rf"^/(?P<locale>[a-z]{{2}}-[a-z]{{2}})/careers/(?P<slug>[^/]+)/"
    rf"(?P<career_page>{_UUID})/?$",
    re.IGNORECASE,
)
_JOB_PATH_RE = re.compile(
    rf"^/job/(?P<slug>[A-Za-z0-9_-]+)/(?P<job_id>{_UUID})/?$",
    re.IGNORECASE,
)
_LISTING_HOSTS = frozenset({"app.jobconvo.com", "jobs.jobconvo.com"})
_JOB_HOSTS = frozenset({"app.jobconvo.com", "jobs.jobconvo.com"})
_MAX_PAGES = 1_000
_MAX_JOBS = 50_000
_MAX_HTML_BYTES = 4 * 1024 * 1024


def _listing_identity(url: str) -> tuple[str, str, str] | None:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in _LISTING_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    match = _LISTING_PATH_RE.fullmatch(parsed.path)
    if match is None:
        return None
    return (
        match.group("locale").lower(),
        match.group("slug"),
        match.group("career_page").lower(),
    )


def _canonical_listing_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, parsed.query, ""))


def _canonical_job_url(href: str, expected_career_page: str) -> tuple[str, str] | None:
    parsed = urlsplit(href)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in _JOB_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    match = _JOB_PATH_RE.fullmatch(parsed.path)
    if match is None:
        return None

    query = parse_qs(parsed.query, keep_blank_values=True)
    career_pages = query.get("career_page", [])
    if career_pages and any(value.lower() != expected_career_page for value in career_pages):
        raise ValueError("JobConvo job link belongs to a different career page")

    job_id = match.group("job_id").lower()
    url = f"https://app.jobconvo.com/job/{match.group('slug')}/{job_id}/"
    return job_id, url


def _pagination_url(href: str, current_url: str, identity: tuple[str, str, str]) -> str | None:
    if not href or href == "#":
        return None
    candidate = _canonical_listing_url(urljoin(current_url, href))
    if _listing_identity(candidate) != identity:
        raise ValueError("JobConvo pagination escaped the configured career page")
    query = parse_qs(urlsplit(candidate).query, keep_blank_values=True)
    if set(query) != {"page"} or len(query["page"]) != 1:
        raise ValueError("JobConvo pagination URL has unexpected parameters")
    try:
        page = int(query["page"][0])
    except ValueError as exc:
        raise ValueError("JobConvo pagination page is not an integer") from exc
    if not 1 <= page <= _MAX_PAGES:
        raise ValueError("JobConvo pagination page is outside the supported range")
    return candidate


def _parse_page(
    html: str,
    page_url: str,
    identity: tuple[str, str, str],
) -> tuple[dict[str, str], set[str]]:
    tree = LexborHTMLParser(html)
    tables = tree.css("table#tbl")
    if len(tables) != 1:
        raise ValueError("JobConvo listing omitted its authoritative jobs table")

    paginators = tree.css("ul.pagination")
    if len(paginators) != 1:
        raise ValueError("JobConvo listing omitted its authoritative paginator")
    paginator = paginators[0]
    active_pages = paginator.css("li.active")
    if len(active_pages) != 1:
        raise ValueError("JobConvo paginator did not identify exactly one active page")
    active_text = active_pages[0].text(strip=True)
    try:
        active_page = int(active_text)
    except ValueError as exc:
        raise ValueError("JobConvo paginator active page is not an integer") from exc
    query = parse_qs(urlsplit(page_url).query, keep_blank_values=True)
    expected_page = int(query.get("page", ["1"])[0])
    if active_page != expected_page:
        raise ValueError("JobConvo paginator active page does not match the requested page")

    jobs: dict[str, str] = {}
    for row in tables[0].css("tr.joblist"):
        links = row.css('a[href*="jobconvo.com/job/"]')
        if len(links) != 1:
            raise ValueError("JobConvo job row did not contain exactly one detail link")
        href = links[0].attributes.get("href", "")
        parsed = _canonical_job_url(urljoin(page_url, href), identity[2])
        if parsed is None:
            raise ValueError("JobConvo job row contained an invalid detail URL")
        job_id, job_url = parsed
        previous = jobs.setdefault(job_id, job_url)
        if previous != job_url:
            raise ValueError(f"JobConvo job {job_id!r} has conflicting detail URLs")

    pages: set[str] = set()
    for link in paginator.css("a[href]"):
        candidate = _pagination_url(link.attributes.get("href", ""), page_url, identity)
        if candidate is not None:
            pages.add(candidate)
    return jobs, pages


async def _collect_urls(
    listing_url: str,
    client: httpx.AsyncClient,
    *,
    missing_is_gone: bool,
) -> set[str]:
    identity = _listing_identity(listing_url)
    if identity is None:
        raise ValueError(f"Unsupported JobConvo career-page URL: {listing_url!r}")

    first_url = _canonical_listing_url(listing_url)
    queue = deque([first_url])
    queued = {first_url}
    visited: set[str] = set()
    jobs: dict[str, str] = {}

    while queue:
        page_url = queue.popleft()
        visited.add(page_url)
        html = await fetch_text_page_with_retry(
            client,
            page_url,
            retries=3,
            require_nonempty=True,
            max_bytes=_MAX_HTML_BYTES,
            end_of_pagination_statuses=() if page_url != first_url else {404, 410},
            log_event="jobconvo.list_backoff",
        )
        if html is None:
            if missing_is_gone:
                raise BoardGoneError(
                    "JobConvo career page no longer exists",
                    url=page_url,
                    status_code=404,
                )
            raise ValueError("JobConvo career page no longer exists")

        page_jobs, advertised_pages = _parse_page(html, page_url, identity)
        for job_id, job_url in page_jobs.items():
            previous = jobs.setdefault(job_id, job_url)
            if previous != job_url:
                raise ValueError(f"JobConvo job {job_id!r} changed URL across pages")
        if len(jobs) > _MAX_JOBS:
            raise ValueError(f"JobConvo listing exceeds the {_MAX_JOBS}-job safety cap")

        for advertised in sorted(advertised_pages):
            if advertised not in visited and advertised not in queued:
                if len(queued) >= _MAX_PAGES:
                    raise ValueError("JobConvo listing exceeds the pagination safety cap")
                queued.add(advertised)
                queue.append(advertised)

    return set(jobs.values())


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    _ = pw
    metadata = board.get("metadata") or {}
    listing_url = metadata.get("listing_url") or board["board_url"]
    identity = _listing_identity(listing_url)
    if identity is None:
        raise ValueError(f"Unsupported JobConvo career-page URL: {listing_url!r}")

    configured_locale = metadata.get("locale")
    if configured_locale is not None and configured_locale.lower() != identity[0]:
        raise ValueError("Configured JobConvo locale does not match the listing URL")
    configured_page = metadata.get("career_page")
    if configured_page is not None and configured_page.lower() != identity[2]:
        raise ValueError("Configured JobConvo career_page does not match the listing URL")

    return await _collect_urls(listing_url, client, missing_is_gone=True)


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    _ = pw
    identity = _listing_identity(url)
    if identity is None:
        return None

    result: dict = {
        "listing_url": _canonical_listing_url(url),
        "locale": identity[0],
        "career_page": identity[2],
    }
    if client is None:
        return result
    try:
        result["jobs"] = len(await _collect_urls(url, client, missing_is_gone=False))
    except Exception:
        log.debug("jobconvo.probe_failed", url=url, exc_info=True)
        return None
    return result


register(
    "jobconvo",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=False,
)
