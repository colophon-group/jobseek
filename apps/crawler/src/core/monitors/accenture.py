"""Accenture career API monitor.

Endpoint: POST /api/accenture/elastic/findjobs (multipart form data)
  - Page size: maxResultSize up to 500
  - Pagination ceiling: 50,000 items (startIndex >= 50000 returns empty)
  - Filters: jobFilters field — JSON array, AND-combined, non-intersecting

FR/BR variant: POST /api/accenture/jobsearch/result
  - Different response format (minimal fields, URL in jobDetailUrl)

When total exceeds the 50k ceiling, the monitor partitions by businessArea.
If a single businessArea also exceeds 50k, it sub-partitions by careerLevel.
Partition values are discovered dynamically from fetched items.
"""

from __future__ import annotations

import asyncio
import json
import random
from contextlib import asynccontextmanager

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.api_sniff import FetchJsonFn, set_body_param
from src.shared.browser import DEFAULT_USER_AGENT, navigate, open_page
from src.shared.http_retry import PaginationFetchError, is_retryable_status

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FINDJOBS = "elastic/findjobs"
JOBSEARCH = "jobsearch/result"
PAGE_SIZE = 500
PAGINATION_CAP = 50_000
_CONCURRENCY = 10
_BOUNDARY = "----FormBoundary"
_API_BASE = "https://www.accenture.com/api/accenture"
_CONTENT_TYPE = f"multipart/form-data; boundary={_BOUNDARY}"


# ---------------------------------------------------------------------------
# Browser session
# ---------------------------------------------------------------------------


async def _extract_cookies(pw, site: str) -> str:
    """Launch browser, navigate to career page, return cookie header string."""
    url = f"https://www.accenture.com/{site}/careers/jobsearch"
    if pw is not None:
        async with open_page(pw) as page:
            await navigate(page, url)
            cookies = await page.context.cookies()
    else:
        from playwright.async_api import async_playwright

        async with async_playwright() as _pw, open_page(_pw) as page:
            await navigate(page, url)
            cookies = await page.context.cookies()

    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _make_http_fetcher(client: httpx.AsyncClient, cookie_header: str) -> FetchJsonFn:
    """Create a FetchJsonFn backed by httpx with pre-extracted cookies."""

    async def _fetch(method: str, url: str, headers: dict, body: str | None) -> object:
        req_headers = {
            **headers,
            "Cookie": cookie_header,
            "User-Agent": DEFAULT_USER_AGENT,
        }
        kw: dict = {"headers": req_headers, "timeout": 30}
        if method.upper() == "POST" and body:
            kw["content"] = body
        resp = await client.request(method.upper(), url, **kw)
        resp.raise_for_status()
        return resp.json()

    return _fetch


@asynccontextmanager
async def _browser_session(pw, site: str):
    """Navigate once to get cookies, then yield a fast httpx-based fetch_fn."""
    cookie_header = await _extract_cookies(pw, site)
    async with httpx.AsyncClient() as client:
        yield _make_http_fetcher(client, cookie_header)


@asynccontextmanager
async def _browser_session_jobsearch(pw, site: str):
    """Open browser for jobsearch/result endpoint.

    Intercepts the page's own API request to capture the body format,
    extracts cookies, then yields (fetch_fn, body_template, content_type)
    backed by httpx for fast pagination.
    """
    url = f"https://www.accenture.com/{site}/careers/jobsearch"
    captured: dict = {}
    captured_event = asyncio.Event()

    async def _intercept(route):
        body = route.request.post_data or ""
        ct = route.request.headers.get("content-type", "")
        body = set_body_param(body, "maxResultSize", PAGE_SIZE)
        if not captured_event.is_set():
            captured["body"] = body
            captured["content_type"] = ct
            captured_event.set()
        await route.continue_(post_data=body)

    async def _setup(page):
        await page.route("**/api/accenture/jobsearch/result*", _intercept)
        await navigate(page, url)
        try:
            await asyncio.wait_for(captured_event.wait(), timeout=15)
        except TimeoutError:
            log.warning("accenture.jobsearch_capture_timeout", site=site)
        cookies = await page.context.cookies()
        return "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    if pw is not None:
        async with open_page(pw) as page:
            cookie_header = await _setup(page)
    else:
        from playwright.async_api import async_playwright

        async with async_playwright() as _pw, open_page(_pw) as page:
            cookie_header = await _setup(page)

    async with httpx.AsyncClient() as client:
        yield (
            _make_http_fetcher(client, cookie_header),
            captured.get("body"),
            captured.get("content_type"),
        )


# ---------------------------------------------------------------------------
# Body builder
# ---------------------------------------------------------------------------


def _build_body(
    start: int,
    country: str,
    lang: str,
    site: str,
    filters: list[dict] | None = None,
) -> str:
    """Build multipart/form-data POST body for the Accenture API."""
    fields: dict[str, str] = {
        "startIndex": str(start),
        "maxResultSize": str(PAGE_SIZE),
        "jobKeyword": "",
        "jobCountry": country,
        "jobLanguage": lang,
        "countrySite": site,
        "sortBy": "2",
        "totalHits": "true",
    }
    if filters:
        fields["jobFilters"] = json.dumps(filters)

    delim = f"--{_BOUNDARY}"
    parts: list[str] = []
    for name, value in fields.items():
        parts.append(f'{delim}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}')
    return "\r\n".join(parts) + f"\r\n{delim}--"


# ---------------------------------------------------------------------------
# Fetch / paginate
# ---------------------------------------------------------------------------


async def _fetch_page(
    fetch_fn: FetchJsonFn,
    body: str,
    endpoint: str,
    content_type: str = _CONTENT_TYPE,
) -> tuple[list[dict], int]:
    """Fetch one page. Returns (items, total_hits).

    Single-shot — does not retry. Callers should use
    :func:`_fetch_page_with_retry` for the pagination paths so transient
    upstream failures don't masquerade as end-of-pagination (#2735).
    """
    url = f"{_API_BASE}/{endpoint}"
    headers = {"Content-Type": content_type}
    data = await fetch_fn("POST", url, headers, body)

    if isinstance(data, dict):
        items = data.get("data") or []
        total = data.get("totalHits", 0)
        # Accenture returns {"total": N, "overMaxHits": "True"|"False"}
        if isinstance(total, dict):
            total = total.get("total") or total.get("value", 0)
        if isinstance(total, str):
            total = int(total) if total.isdigit() else 0
        return items, total
    return [], 0


# Retry budget for paginated Accenture fetches. Matches ``fetch_with_retry``
# defaults: 3 total attempts, exponential backoff with full jitter starting
# at 1s (slightly longer than dom's 0.5s — Accenture pages are heavier
# multipart POSTs and a thundering herd at sub-second cadence is
# counterproductive on a single tenant).
_ACCENTURE_FETCH_RETRIES = 3
_ACCENTURE_FETCH_BASE_DELAY = 1.0


async def _fetch_page_with_retry(
    fetch_fn: FetchJsonFn,
    body: str,
    endpoint: str,
    content_type: str = _CONTENT_TYPE,
    *,
    retries: int = _ACCENTURE_FETCH_RETRIES,
    base_delay: float = _ACCENTURE_FETCH_BASE_DELAY,
) -> tuple[list[dict], int]:
    """Fetch one page with bounded retries on transient failures (#2735).

    Wraps :func:`_fetch_page` to add the same retry-then-raise contract
    used by ``fetch_with_retry`` / dom / sitemap / PCSX:

    - Returns the page on success.
    - Retries on retryable HTTP statuses (5xx including Cloudflare
      520-526/530, plus 408/425/429) and arbitrary network exceptions
      (timeout, connection reset, JSON parse error). Backoff is
      exponential with full jitter: ``base_delay × 2^attempt × (0.5 + random())``.
    - Raises :class:`PaginationFetchError` after the budget is exhausted,
      OR immediately on non-retryable 4xx (auth, bad request) since those
      won't recover. The caller routes the run through
      ``_RECORD_FAILURE`` rather than silently truncating the partition
      output (the bug from PR #2722's NHS spike — same shape).
    """
    last_exc: BaseException | None = None
    last_status: int | None = None
    url = f"{_API_BASE}/{endpoint}"

    for attempt in range(retries):
        try:
            return await _fetch_page(fetch_fn, body, endpoint, content_type)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_status = status
            last_exc = exc
            if not is_retryable_status(status):
                # 4xx — not transient, won't recover. Fail fast so the
                # whole run is recorded as a failure rather than a
                # partial-success with one partition silently dropped.
                raise PaginationFetchError(
                    url,
                    attempts=attempt + 1,
                    last_status=status,
                ) from exc
        except Exception as exc:  # noqa: BLE001 — timeout, network, parse error
            last_exc = exc
            last_status = None

        if attempt < retries - 1:
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "accenture.fetch_backoff",
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                last_status=last_status,
                last_error=type(last_exc).__name__ if last_exc else None,
            )
            await asyncio.sleep(delay)

    raise PaginationFetchError(
        url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_exc).__name__ if last_exc else None,
    )


async def _paginate(
    fetch_fn: FetchJsonFn,
    country: str,
    lang: str,
    site: str,
    endpoint: str = FINDJOBS,
    filters: list[dict] | None = None,
) -> tuple[list[dict], bool]:
    """Paginate up to PAGINATION_CAP. Returns (raw_items, hit_ceiling).

    Failure semantics (#2735). The seed page and every parallel
    pagination task use :func:`_fetch_page_with_retry`, which raises
    :class:`PaginationFetchError` on persistent transient failures or
    non-retryable 4xx. ``asyncio.gather`` runs without
    ``return_exceptions``: the first failed sub-task cancels the
    remaining pending tasks and propagates the exception out of this
    function — the caller (``_collect_with_partitioning`` /
    ``discover_stream``) does not have a try/except, so the run
    surfaces in ``_process_one_board_streaming``'s generic
    ``except Exception`` and is recorded as a failure (no silent
    partition truncation).
    """
    body = _build_body(0, country, lang, site, filters)
    items, total = await _fetch_page_with_retry(fetch_fn, body, endpoint)

    all_items = list(items)
    if not items:
        return all_items, False

    # totalHits caps at 10k cosmetically; pagination works up to 50k.
    max_offset = total if total < 10_000 else PAGINATION_CAP

    if max_offset <= PAGE_SIZE:
        return all_items, False

    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def _get(offset: int) -> list[dict]:
        async with semaphore:
            b = _build_body(offset, country, lang, site, filters)
            page_items, _ = await _fetch_page_with_retry(fetch_fn, b, endpoint)
            return page_items

    tasks = [_get(off) for off in range(PAGE_SIZE, max_offset, PAGE_SIZE)]
    # No ``return_exceptions=True``: a persistent failure on any page
    # raises ``PaginationFetchError`` here, cancels the remaining
    # pending tasks, and propagates so the run is recorded as a
    # failure rather than silently truncating to whatever pages
    # happened to succeed.
    results = await asyncio.gather(*tasks)
    for result in results:
        all_items.extend(result)

    hit_ceiling = len(all_items) >= PAGINATION_CAP
    return all_items, hit_ceiling


async def _paginate_jobsearch(
    fetch_fn: FetchJsonFn,
    body_template: str,
    content_type: str,
) -> list[dict]:
    """Paginate the jobsearch/result endpoint using captured body template.

    Uses set_body_param to modify startIndex in the captured body format.
    No partitioning — FR/BR boards are small enough.

    Same strict failure semantics as :func:`_paginate` (#2735): a
    persistent transient on any page raises ``PaginationFetchError``
    rather than silently truncating to the surviving subset.
    """
    body = set_body_param(body_template, "startIndex", 0)
    body = set_body_param(body, "maxResultSize", PAGE_SIZE)
    items, total = await _fetch_page_with_retry(fetch_fn, body, JOBSEARCH, content_type)

    all_items = list(items)
    if not items:
        return all_items

    max_offset = total if total < 10_000 else PAGINATION_CAP

    if max_offset <= PAGE_SIZE:
        return all_items

    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def _get(offset: int) -> list[dict]:
        async with semaphore:
            b = set_body_param(body_template, "startIndex", offset)
            b = set_body_param(b, "maxResultSize", PAGE_SIZE)
            page_items, _ = await _fetch_page_with_retry(fetch_fn, b, JOBSEARCH, content_type)
            return page_items

    tasks = [_get(off) for off in range(PAGE_SIZE, max_offset, PAGE_SIZE)]
    results = await asyncio.gather(*tasks)
    for result in results:
        all_items.extend(result)

    log.info("accenture.paginated", items=len(all_items))
    return all_items


# ---------------------------------------------------------------------------
# Partition helpers
# ---------------------------------------------------------------------------


def _discover_values(raw_items: list[dict], field: str) -> set[str]:
    """Extract unique partition values from already-fetched items."""
    return {item[field] for item in raw_items if item.get(field)}


def _make_filter(field: str, value: str) -> dict:
    """Build a single jobFilters entry."""
    return {
        "fieldName": f"{field}.keyword",
        "items": [value],
        "multiSelect": False,
    }


def _url_key_for(endpoint: str) -> str:
    return "guid" if endpoint == FINDJOBS else "jobDetailUrl"


# ---------------------------------------------------------------------------
# Collection with auto-partitioning
# ---------------------------------------------------------------------------


async def _collect_with_partitioning(
    fetch_fn: FetchJsonFn,
    country: str,
    lang: str,
    site: str,
    endpoint: str = FINDJOBS,
) -> list[dict]:
    """Paginate, auto-partition if ceiling hit, dedup by URL key."""
    items, hit_ceiling = await _paginate(fetch_fn, country, lang, site, endpoint)

    if not hit_ceiling:
        log.info("accenture.paginated", items=len(items))
        return items

    # Discover businessArea values from items already fetched
    areas = _discover_values(items, "businessArea")
    if not areas:
        log.warning("accenture.no_areas_found", items=len(items))
        return items

    log.info("accenture.partitioning_by_area", areas=len(areas), initial=len(items))

    key = _url_key_for(endpoint)
    seen: set[str] = set()
    all_items: list[dict] = []

    # Seed with initial items
    for item in items:
        k = item.get(key)
        if k and k not in seen:
            seen.add(k)
            all_items.append(item)

    for area in sorted(areas):
        filters = [_make_filter("businessArea", area)]
        area_items, area_ceiling = await _paginate(fetch_fn, country, lang, site, endpoint, filters)

        if area_ceiling:
            # Sub-partition by careerLevel
            levels = _discover_values(area_items, "careerLevel")
            if levels:
                log.info("accenture.sub_partitioning", area=area, levels=len(levels))
                # Add new items from the capped area query
                for item in area_items:
                    k = item.get(key)
                    if k and k not in seen:
                        seen.add(k)
                        all_items.append(item)

                for level in sorted(levels):
                    sub_filters = [
                        _make_filter("businessArea", area),
                        _make_filter("careerLevel", level),
                    ]
                    sub_items, _ = await _paginate(
                        fetch_fn, country, lang, site, endpoint, sub_filters
                    )
                    new = 0
                    for item in sub_items:
                        k = item.get(key)
                        if k and k not in seen:
                            seen.add(k)
                            all_items.append(item)
                            new += 1
                    if new:
                        log.info(
                            "accenture.sub_partition",
                            area=area,
                            level=level,
                            new=new,
                        )
                continue

        new = 0
        for item in area_items:
            k = item.get(key)
            if k and k not in seen:
                seen.add(k)
                all_items.append(item)
                new += 1
        if new:
            log.info("accenture.partition", area=area, new=new)

    log.info("accenture.partition_done", total=len(all_items))
    return all_items


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_findjobs_job(raw: dict, site: str) -> DiscoveredJob | None:
    """Parse a findjobs API item into DiscoveredJob."""
    guid = raw.get("guid")
    if not guid:
        return None

    url = f"https://www.accenture.com/{site}/careers/jobdetails?id={guid}"

    locations: list[str] | None = None
    loc = raw.get("location")
    if loc:
        locations = [loc] if isinstance(loc, str) else list(loc)

    metadata: dict = {}
    for field in ("businessArea", "careerLevel", "guid"):
        val = raw.get(field)
        if val:
            metadata[field] = val

    return DiscoveredJob(
        url=url,
        title=raw.get("title"),
        description=raw.get("jobDescription"),
        locations=locations,
        job_location_type=raw.get("remoteType"),
        date_posted=raw.get("postedDate"),
        metadata=metadata or None,
    )


def _parse_jobsearch_job(raw: dict) -> DiscoveredJob | None:
    """Parse a jobsearch/result API item into DiscoveredJob (minimal)."""
    url = raw.get("jobDetailUrl")
    if not url:
        return None

    if url.startswith("/"):
        url = f"https://www.accenture.com{url}"

    locations: list[str] | None = None
    loc = raw.get("jobCityState")
    if loc:
        locations = [loc] if isinstance(loc, str) else list(loc)

    return DiscoveredJob(
        url=url,
        title=raw.get("title"),
        locations=locations,
        date_posted=raw.get("postedDate"),
    )


def _parse_items(raw_items: list[dict], endpoint: str, site: str) -> list[DiscoveredJob]:
    """Parse raw API items into DiscoveredJob list."""
    jobs: list[DiscoveredJob] = []
    for raw in raw_items:
        job = _parse_findjobs_job(raw, site) if endpoint == FINDJOBS else _parse_jobsearch_job(raw)
        if job:
            jobs.append(job)
    return jobs


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def discover(board: dict, client, pw=None) -> list[DiscoveredJob]:
    """Discover Accenture jobs for a single board."""
    config = board.get("metadata") or {}
    country = config["country"]
    lang = config["language"]
    site = config["site"]
    endpoint = config.get("endpoint", FINDJOBS)

    if endpoint == JOBSEARCH:
        async with _browser_session_jobsearch(pw, site) as (fetch_fn, body_tpl, ct):
            if not body_tpl:
                log.warning("accenture.jobsearch_no_body_captured", site=site)
                return []
            raw_items = await _paginate_jobsearch(fetch_fn, body_tpl, ct)
    else:
        async with _browser_session(pw, site) as fetch_fn:
            raw_items = await _collect_with_partitioning(fetch_fn, country, lang, site, endpoint)

    jobs = _parse_items(raw_items, endpoint, site)
    log.info("accenture.discovered", board=board.get("board_slug"), jobs=len(jobs))
    return jobs


async def discover_stream(board: dict, client, pw=None):
    """Yield batches of DiscoveredJob after each partition.

    Same logic as discover() but yields intermediate batches so the caller
    can refresh timeouts and start uploads while discovery continues.
    """
    config = board.get("metadata") or {}
    country = config["country"]
    lang = config["language"]
    site = config["site"]
    endpoint = config.get("endpoint", FINDJOBS)
    key = _url_key_for(endpoint)

    # jobsearch/result: simple paginate, no partitioning
    if endpoint == JOBSEARCH:
        async with _browser_session_jobsearch(pw, site) as (fetch_fn, body_tpl, ct):
            if not body_tpl:
                log.warning("accenture.jobsearch_no_body_captured", site=site)
                return
            raw_items = await _paginate_jobsearch(fetch_fn, body_tpl, ct)
            jobs = _parse_items(raw_items, endpoint, site)
            log.info(
                "accenture.discovered",
                board=board.get("board_slug"),
                jobs=len(jobs),
            )
            yield jobs
        return

    # findjobs: paginate with auto-partitioning
    async with _browser_session(pw, site) as fetch_fn:
        items, hit_ceiling = await _paginate(fetch_fn, country, lang, site, endpoint)

        if not hit_ceiling:
            jobs = _parse_items(items, endpoint, site)
            log.info(
                "accenture.discovered",
                board=board.get("board_slug"),
                jobs=len(jobs),
            )
            yield jobs
            return

        # Partitioning needed
        areas = _discover_values(items, "businessArea")
        if not areas:
            log.warning("accenture.no_areas_found", items=len(items))
            yield _parse_items(items, endpoint, site)
            return

        log.info(
            "accenture.partitioning_by_area",
            areas=len(areas),
            initial=len(items),
        )

        seen: set[str] = set()

        # Yield initial (pre-partition) batch
        seed: list[dict] = []
        for item in items:
            k = item.get(key)
            if k and k not in seen:
                seen.add(k)
                seed.append(item)
        if seed:
            yield _parse_items(seed, endpoint, site)

        for area in sorted(areas):
            filters = [_make_filter("businessArea", area)]
            area_items, area_ceiling = await _paginate(
                fetch_fn, country, lang, site, endpoint, filters
            )

            if area_ceiling:
                levels = _discover_values(area_items, "careerLevel")
                if levels:
                    log.info(
                        "accenture.sub_partitioning",
                        area=area,
                        levels=len(levels),
                    )
                    # Yield new items from area query
                    new_area: list[dict] = []
                    for item in area_items:
                        k = item.get(key)
                        if k and k not in seen:
                            seen.add(k)
                            new_area.append(item)
                    if new_area:
                        yield _parse_items(new_area, endpoint, site)

                    for level in sorted(levels):
                        sub_filters = [
                            _make_filter("businessArea", area),
                            _make_filter("careerLevel", level),
                        ]
                        sub_items, _ = await _paginate(
                            fetch_fn, country, lang, site, endpoint, sub_filters
                        )
                        new_sub: list[dict] = []
                        for item in sub_items:
                            k = item.get(key)
                            if k and k not in seen:
                                seen.add(k)
                                new_sub.append(item)
                        if new_sub:
                            log.info(
                                "accenture.sub_partition",
                                area=area,
                                level=level,
                                new=len(new_sub),
                            )
                            yield _parse_items(new_sub, endpoint, site)
                    continue

            new: list[dict] = []
            for item in area_items:
                k = item.get(key)
                if k and k not in seen:
                    seen.add(k)
                    new.append(item)
            if new:
                log.info("accenture.partition", area=area, new=len(new))
                yield _parse_items(new, endpoint, site)

        log.info("accenture.partition_done", total=len(seen))


register("accenture", discover, cost=10, rich=True, stream=discover_stream)
