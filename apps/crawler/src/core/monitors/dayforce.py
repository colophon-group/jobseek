"""Dayforce public career-site monitor.

Dayforce's same-origin search BFF is public but rejects stateless HTTP replay.
The monitor therefore bootstraps the canonical listing over normal HTTP for a
strict board check, then reuses Jobseek's browser transport to stream complete
search records without per-job detail requests.
"""

from __future__ import annotations

import html as html_module
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.dom import _raise_if_bot_challenge
from src.shared.api_sniff import _fetch_page_with_retry, make_browser_fetcher
from src.shared.dayforce import (
    DayforceBoard,
    DayforceSite,
    dayforce_board_from_metadata,
    dayforce_board_from_url,
    dayforce_listing_culture_from_url,
    extract_dayforce_site,
    resolve_dayforce_listing_redirect,
)
from src.shared.html_normalize import normalize_description_html
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

PAGE_SIZE = 25
MAX_JOBS = 50_000
MAX_HTML_CHARS = 1_000_000
SEARCH_READY_TIMEOUT_MS = 30_000

_PAGE_PATTERNS = [
    re.compile(
        r"(https://jobs\.dayforcehcm\.com/"
        r"(?:[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+/)?"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?/"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,126}[A-Za-z0-9])?"
        r"(?:/jobs/[1-9]\d{0,18})?/?)(?=[?#\"'<\s]|$)",
        re.IGNORECASE,
    )
]
_GONE_STATUSES = frozenset({404, 410})
_HTML_TAG_RE = re.compile(r"<[A-Za-z/]")
_CSRF_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{32,512}$")


def _board_key(board: dict) -> DayforceBoard:
    metadata = board.get("metadata") or {}
    key = dayforce_board_from_metadata(metadata) or dayforce_board_from_url(board["board_url"])
    if key is None:
        raise ValueError(
            f"Cannot derive Dayforce tenant/portal from board URL "
            f"{board['board_url']!r} or metadata"
        )
    return key


async def _bootstrap(
    board: DayforceBoard,
    client: httpx.AsyncClient,
) -> tuple[str, DayforceSite]:
    url = board.listing_url()

    async def fetch(target: str) -> str:
        page = await fetch_text_page_with_retry(
            client,
            target,
            require_nonempty=True,
            max_chars=MAX_HTML_CHARS,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            retryable_statuses={202, 403, 429},
            log_event="dayforce.bootstrap_backoff",
        )
        if page is None:  # Strict status handling above makes this unreachable.
            raise RuntimeError(f"Dayforce listing fetch returned no page for {board!r}")
        return page

    target = url
    try:
        page = await fetch(target)
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError("Dayforce board no longer exists", url=target) from exc
        target = resolve_dayforce_listing_redirect(board, target, exc.last_location) or ""
        if exc.last_status not in {301, 302, 307, 308} or not target:
            raise
        try:
            page = await fetch(target)
        except PaginationFetchError as redirect_exc:
            if redirect_exc.last_status in _GONE_STATUSES:
                raise BoardGoneError(
                    "Dayforce board no longer exists",
                    url=target,
                ) from redirect_exc
            raise
    _raise_if_bot_challenge(target, page)
    if len(page) >= MAX_HTML_CHARS:
        raise ValueError("Dayforce listing exceeded the bootstrap HTML safety cap")
    site = extract_dayforce_site(page, board)
    redirected_culture = dayforce_listing_culture_from_url(target)
    if redirected_culture and redirected_culture.casefold() != site.culture.casefold():
        raise ValueError("Dayforce localized redirect does not match the listing culture")
    if site.disabled:
        raise BoardGoneError("Dayforce board is disabled", url=url)
    return page, site


def _search_body(board: DayforceBoard, site: DayforceSite, offset: int) -> str:
    return json.dumps(
        {
            "clientNamespace": board.tenant,
            "jobBoardCode": board.portal,
            "cultureCode": site.culture,
            "distanceUnit": 0,
            "paginationStart": offset,
        },
        separators=(",", ":"),
    )


def _offset_overlap(config: dict) -> int:
    """Return the configured offset overlap for unstable Dayforce ordering."""
    value = config.get("offset_overlap", 0)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < PAGE_SIZE:
        raise ValueError(f"Dayforce offset_overlap must be an integer from 0 to {PAGE_SIZE - 1}")
    return value


async def _fetch_search_page(
    fetch,
    board: DayforceBoard,
    site: DayforceSite,
    offset: int,
    request_headers: dict[str, str],
) -> dict:
    payload = await _fetch_page_with_retry(
        fetch,
        "POST",
        board.search_url(),
        request_headers,
        _search_body(board, site, offset),
    )
    if not isinstance(payload, dict):
        raise ValueError(f"Dayforce {board.tenant!r} search returned a non-object payload")
    return payload


def _csrf_headers(request_headers: dict[str, str]) -> dict[str, str]:
    csrf_token = next(
        (value for name, value in request_headers.items() if name.lower() == "x-csrf-token"),
        None,
    )
    if not isinstance(csrf_token, str) or _CSRF_TOKEN_RE.fullmatch(csrf_token) is None:
        raise ValueError("Dayforce public search omitted a valid CSRF token")
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "x-csrf-token": csrf_token,
    }


async def _navigate_and_capture_headers(page, board: DayforceBoard) -> dict[str, str]:
    """Wait for Dayforce's own first search and retain its public CSRF token."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    from src.shared.browser import navigate

    try:
        async with page.expect_response(
            lambda response: (
                response.url == board.search_url() and response.request.method == "POST"
            ),
            timeout=SEARCH_READY_TIMEOUT_MS,
        ) as response_info:
            await navigate(
                page,
                board.listing_url(),
                {"wait": "domcontentloaded", "timeout": 60_000},
            )
        response = await response_info.value
    except PlaywrightTimeoutError as exc:
        raise PaginationFetchError(
            board.search_url(),
            attempts=1,
            last_error="DayforceSearchNotReady",
        ) from exc
    if response.status != 200:
        raise PaginationFetchError(
            board.search_url(),
            attempts=1,
            last_status=response.status,
        )
    return _csrf_headers(dict(response.request.headers))


def _page_rows(
    payload: dict,
    board: DayforceBoard,
    site: DayforceSite,
    requested_offset: int,
) -> tuple[int, list[object]]:
    total = payload.get("maxCount")
    offset = payload.get("offset")
    count = payload.get("count")
    rows = payload.get("jobPostings")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError(f"Dayforce {board.tenant!r} search omitted a valid count")
    if offset != requested_offset:
        raise ValueError(f"Dayforce {board.tenant!r} search returned an unexpected offset")
    if not isinstance(rows, list):
        raise ValueError(f"Dayforce {board.tenant!r} search omitted its job postings")
    if (
        count != len(rows)
        or len(rows) > PAGE_SIZE
        or (rows and requested_offset + len(rows) > total)
    ):
        raise ValueError(f"Dayforce {board.tenant!r} returned an inconsistent search page")
    for raw in rows:
        if isinstance(raw, dict) and raw.get("jobBoardId") != site.job_board_id:
            raise ValueError(f"Dayforce {board.tenant!r} returned a foreign job-board record")
        if isinstance(raw, dict) and str(raw.get("clientNamespace", "")).casefold() != board.tenant:
            raise ValueError(f"Dayforce {board.tenant!r} returned a foreign tenant record")
    return total, rows


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _positive_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or not 1 <= value <= 9_223_372_036_854_775_807:
        return None
    return value


def _description_html(value: object) -> str | None:
    raw = _clean_string(value)
    if raw is None:
        return None
    if _HTML_TAG_RE.search(raw):
        return normalize_description_html(raw)

    text = html_module.unescape(raw).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [part for part in re.split(r"\n\s*\n+", text) if part.strip()]
    rendered: list[str] = []
    for paragraph in paragraphs:
        lines = [html_module.escape(line.strip(), quote=False) for line in paragraph.split("\n")]
        content = "<br>\n".join(line for line in lines if line)
        if content:
            rendered.append(f"<p>{content}</p>")
    return normalize_description_html("\n".join(rendered))


def _locations(raw: object, has_virtual: object) -> list[str] | None:
    locations: list[str] = []
    seen: set[str] = set()
    if has_virtual is True:
        locations.append("Virtual")
        seen.add("virtual")
    if isinstance(raw, list):
        for item in raw:
            location = (
                _clean_string(item.get("formattedAddress")) if isinstance(item, dict) else None
            )
            key = location.casefold() if location else None
            if location and key not in seen:
                seen.add(key)
                locations.append(location)
    return locations or None


def _date_posted(value: object) -> str | None:
    raw = _clean_string(value)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _parse_job(raw: object, board: DayforceBoard, site: DayforceSite) -> DiscoveredJob | None:
    if not isinstance(raw, dict):
        return None
    job_id = _positive_id(raw.get("jobPostingId"))
    title = _clean_string(raw.get("jobTitle"))
    if job_id is None or title is None:
        return None

    req_id = _positive_id(raw.get("jobReqId"))
    metadata: dict[str, object] = {"job_posting_id": job_id}
    if req_id is not None:
        metadata["job_req_id"] = req_id
    if isinstance(raw.get("isEvergreen"), bool):
        metadata["evergreen"] = raw["isEvergreen"]
    expires_at = _clean_string(raw.get("postingExpiryTimestampUTC"))
    if expires_at is not None:
        metadata["expires_at"] = expires_at

    language = site.culture.split("-", 1)[0].lower()
    has_virtual = raw.get("hasVirtualLocation")
    return DiscoveredJob(
        url=board.job_url(site.culture, job_id),
        title=title,
        description=_description_html(raw.get("jobDescription")),
        locations=_locations(raw.get("postingLocations"), has_virtual),
        job_location_type="remote" if has_virtual is True else None,
        date_posted=_date_posted(raw.get("postingStartTimestampUTC")),
        language=language if len(language) == 2 else None,
        metadata=metadata,
    )


def _rich_result(jobs: list[DiscoveredJob], *, truncated: bool = False) -> MonitorResult:
    from src.core.monitor import MonitorResult

    return MonitorResult(
        urls={job.url for job in jobs},
        jobs_by_url={job.url: job for job in jobs},
        truncated=truncated,
    )


@asynccontextmanager
async def _open_dayforce_page(pw, browser_config: dict, *, use_proxy: bool):
    """Open a shared browser page and drain Dayforce network state on exit."""
    from src.shared.browser import open_page

    async with open_page(pw, browser_config, use_proxy=use_proxy) as page:
        try:
            yield page
        finally:
            try:
                await page.goto("about:blank", wait_until="commit", timeout=5_000)
            except Exception:
                log.debug("dayforce.browser_drain_failed", exc_info=True)


async def stream(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> AsyncIterator[MonitorResult]:
    """Stream complete public search pages through Jobseek's browser transport."""
    if pw is None:
        raise RuntimeError("Dayforce monitor requires a Playwright browser")

    from src.shared.browser import BROWSER_KEYS, safe_content

    config = board.get("metadata") or {}
    offset_overlap = _offset_overlap(config)
    offset_step = PAGE_SIZE - offset_overlap
    key = _board_key(board)
    _page, bootstrap_site = await _bootstrap(key, client)
    browser_config = {name: value for name, value in config.items() if name in BROWSER_KEYS}

    async with _open_dayforce_page(
        pw,
        browser_config,
        use_proxy=bool(config.get("proxy")),
    ) as page:
        request_headers = await _navigate_and_capture_headers(page, key)
        browser_site = extract_dayforce_site(await safe_content(page), key)
        if browser_site.disabled:
            raise BoardGoneError("Dayforce board is disabled", url=key.listing_url())
        if browser_site != bootstrap_site:
            raise ValueError(f"Dayforce {key.tenant!r} bootstrap changed during browser startup")

        fetch = make_browser_fetcher(page)
        expected_total: int | None = None
        count_changed = False
        offset = 0
        raw_seen = 0
        invalid = 0
        duplicates = 0
        within_page_duplicates = 0
        seen_urls: set[str] = set()

        max_pages = max(1, (MAX_JOBS + offset_step - 1) // offset_step)
        for _page_number in range(max_pages):
            payload = await _fetch_search_page(
                fetch,
                key,
                browser_site,
                offset,
                request_headers,
            )
            total, rows = _page_rows(payload, key, browser_site, offset)
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                log.warning(
                    "dayforce.count_changed",
                    tenant=key.tenant,
                    portal=key.portal,
                    previous=expected_total,
                    current=total,
                )
                expected_total = total
                count_changed = True

            if not rows and offset < total:
                payload = await _fetch_search_page(
                    fetch,
                    key,
                    browser_site,
                    offset,
                    request_headers,
                )
                retry_total, rows = _page_rows(payload, key, browser_site, offset)
                if retry_total != expected_total:
                    log.warning(
                        "dayforce.count_changed",
                        tenant=key.tenant,
                        portal=key.portal,
                        previous=expected_total,
                        current=retry_total,
                    )
                    expected_total = retry_total
                    count_changed = True
                if not rows:
                    raise PaginationFetchError(
                        key.search_url(),
                        attempts=2,
                        last_status=200,
                        last_error="PrematureEmptyDayforcePage",
                    )

            page_jobs: list[DiscoveredJob] = []
            page_urls: set[str] = set()
            for raw in rows:
                raw_seen += 1
                job = _parse_job(raw, key, browser_site)
                if job is None:
                    invalid += 1
                    continue
                if job.url in page_urls:
                    duplicates += 1
                    within_page_duplicates += 1
                    continue
                page_urls.add(job.url)
                if job.url in seen_urls:
                    duplicates += 1
                    continue
                seen_urls.add(job.url)
                page_jobs.append(job)

            coverage_end = offset + len(rows)
            if coverage_end < total and len(rows) < PAGE_SIZE:
                raise PaginationFetchError(
                    key.search_url(),
                    attempts=1,
                    last_status=200,
                    last_error="PrematurePartialDayforcePage",
                )

            done = coverage_end >= total or coverage_end >= MAX_JOBS
            truncated = done and (
                count_changed
                or invalid > 0
                or within_page_duplicates > 0
                or (total <= MAX_JOBS and len(seen_urls) != total)
                or total > MAX_JOBS
            )
            if page_jobs or done:
                yield _rich_result(page_jobs, truncated=truncated)
            if done:
                if expected_total and not seen_urls:
                    raise ValueError(f"Dayforce {key.tenant!r} returned no valid job postings")
                log_method = log.warning if truncated else log.info
                log_method(
                    "dayforce.discovered",
                    tenant=key.tenant,
                    portal=key.portal,
                    jobs=len(seen_urls),
                    raw_seen=raw_seen,
                    expected_total=expected_total,
                    invalid=invalid,
                    duplicates=duplicates,
                    within_page_duplicates=within_page_duplicates,
                    offset_overlap=offset_overlap,
                    count_changed=count_changed,
                    truncated=truncated,
                )
                return
            offset += offset_step
        else:
            yield _rich_result([], truncated=True)


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Materialized form used by single-board debug commands and tests."""
    jobs: list[DiscoveredJob] = []
    truncated = False
    async for result in stream(board, client, pw=pw):
        if result.jobs_by_url:
            jobs.extend(result.jobs_by_url.values())
        truncated = truncated or result.truncated
    return truncated_rich_result(jobs) if truncated else jobs


async def _probe_listing_url(
    listing_url: str,
    client: httpx.AsyncClient,
) -> tuple[bool, DayforceSite | None]:
    board = dayforce_board_from_url(listing_url)
    if board is None:
        return False, None
    try:
        _page, site = await _bootstrap(board, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("dayforce.probe_failed", listing_url=listing_url, exc_info=True)
        return False, None
    return True, site


async def _fetch_job_count(
    listing_url: str,
    client: httpx.AsyncClient,
    context: DayforceSite | None,
) -> ProbeCount | None:
    _ = listing_url, client, context
    return None


async def _probe_candidate(
    listing_url: str,
    client: httpx.AsyncClient,
    context: DayforceSite | None,
) -> ProbeResult:
    _ = context
    found, _site = await _probe_listing_url(listing_url, client)
    return found, None


async def _resolve_direct(
    url: str,
    listing_url: str,
    client: httpx.AsyncClient,
    context: DayforceSite | None,
) -> tuple[str, DayforceSite | None] | None:
    _ = url, context
    found, site = await _probe_listing_url(listing_url, client)
    return (listing_url, site) if found else None


def _listing_url_from_url(url: str) -> str | None:
    board = dayforce_board_from_url(url)
    return board.listing_url() if board is not None else None


def _build_result(
    listing_url: str,
    count: ProbeCount | None,
    context: DayforceSite | None,
) -> dict:
    _ = count, context
    board = dayforce_board_from_url(listing_url)
    if board is None:
        raise ValueError("Dayforce result builder received an invalid listing URL")
    return {"tenant": board.tenant, "portal": board.portal}


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked canonical Dayforce boards."""
    _ = pw
    if (
        dayforce_board_from_url(url) is None
        and dayforce_board_from_url(url, validate_query=False) is not None
    ):
        return None
    return await ats_can_handle(
        url,
        client,
        monitor_name="dayforce",
        token_from_url=_listing_url_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=frozenset(),
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_candidate,
        initial_context=None,
        result_builder=_build_result,
        direct_token_resolver=_resolve_direct,
        page_token_probe=_probe_candidate,
        require_direct_count=False,
        allow_slug_guess=False,
        log_token_field="listing_url",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    board = dayforce_board_from_metadata(metadata) or dayforce_board_from_url(board_url)
    if board is None:
        return
    page, _site = await _bootstrap(board, client)
    (artifact_dir / "dayforce-listing.html").write_text(page, encoding="utf-8")


register(
    "dayforce",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    stream=stream,
    save_raw=save_raw,
)
