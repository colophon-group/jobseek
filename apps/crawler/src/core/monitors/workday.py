"""Workday Job Board API monitor.

Discovers job URLs via the Workday list API.  Does **not** fetch individual
job details — that is handled by the ``workday`` scraper which hits the
detail endpoint on a daily scrape schedule.

Public API:
  List: POST https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs

Max ``limit`` per request is **20** (higher values return 400).

The API caps results at **2000** per query.  When `total` reaches 2000 the
monitor automatically splits into per-facet queries (e.g. by job category)
so that each sub-query stays below the cap, then deduplicates.

Multi-site discovery
--------------------
Workday tenants expose all their job board sites in ``robots.txt`` as
``Sitemap:`` entries.  By default the monitor discovers **all** sites for
the tenant and aggregates jobs from every site in a single run.  To monitor
only the configured site, set ``"all_sites": false`` in board metadata.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog

from src.core.monitors import fetch_page_text, register
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
PAGE_SIZE = 20
_LIST_CONCURRENCY = 5  # Parallel site listing during multi-site discovery
_API_RESULT_CAP = 2000  # Workday caps list results at 2000 per query
# Pagination retry budget. Symmetric with the accenture monitor (#2735)
# and api_sniffer monitor (#2733): 3 total attempts, exponential backoff
# with full jitter starting at 1s. Slightly more relaxed than dom's
# 0.5s because Workday tenants do honour 429 Retry-After hints and a
# thundering herd of sub-second retries can entrench the rate limit.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0

# In-stream sentinel used by ``_api_list_stream`` and ``_list_all_sites_stream``
# to signal that the MAX_JOBS cap was hit (#3216). Distinct from any real
# Workday path; consumers drop it and flip the cycle to partial.
# The ``_api_list_stream`` (path-only) variant yields just the path string;
# ``_list_all_sites_stream`` yields the ``(site, path)`` tuple form.
_TRUNCATED_PATH = "__workday_truncated__"
_TRUNCATED_SENTINEL = ("__workday_truncated__", _TRUNCATED_PATH)

_SITEMAP_RE = re.compile(r"myworkdayjobs\.com/([^/]+)/siteMap")

# Matches Workday board URLs, optionally with locale prefix (e.g. /en-US/)
_URL_RE = re.compile(
    r"([\w-]+)\.wd(\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?"
    r"(.+?)/?$"
)

_PAGE_PATTERNS = [
    re.compile(r"([\w-]+)\.wd\d+\.myworkdayjobs\.com"),
    re.compile(r"window\.workday"),
    re.compile(r"workdaycdn\.com"),
]


def _parse_components(url: str) -> tuple[str, str, str] | None:
    """Extract (company, wd_instance, site) from a Workday board URL.

    Example: https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
      -> ("nvidia", "wd5", "NVIDIAExternalCareerSite")
    """
    match = _URL_RE.search(url)
    if not match:
        return None
    company = match.group(1)
    wd_instance = f"wd{match.group(2)}"
    site = match.group(3)
    return company, wd_instance, site


def _api_base(company: str, wd_instance: str) -> str:
    return f"https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}"


def _api_list_url(company: str, wd_instance: str, site: str) -> str:
    return f"{_api_base(company, wd_instance)}/{site}/jobs"


def _job_url(company: str, wd_instance: str, site: str, external_path: str) -> str:
    return f"https://{company}.{wd_instance}.myworkdayjobs.com/{site}{external_path}"


# ── List pagination ──────────────────────────────────────────────────


async def _post_page_with_retry(
    client: httpx.AsyncClient,
    list_url: str,
    payload: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> dict:
    """POST a Workday list-API page with bounded retries (#2748)."""
    return await fetch_json_page_with_retry(
        client,
        list_url,
        method="POST",
        json_body=payload,
        headers={"Content-Type": "application/json"},
        expect_shape=dict,
        retries=retries,
        base_delay=base_delay,
        log_event="workday.list_backoff",
        sleep=asyncio.sleep,
    )


async def _paginate_query(
    list_url: str,
    body: dict,
    client: httpx.AsyncClient,
    *,
    cap_abort: int = 0,
) -> tuple[list[str], int, list[dict]]:
    """Paginate a single list query. Returns (paths, total, facets).

    When *cap_abort* > 0 and ``total >= cap_abort`` after the first page,
    return immediately with only the first page's results.  This avoids
    fetching up to 100 pages that will be discarded when the caller is
    only interested in the total and facets for splitting.

    Failure semantics (#2748). Each page POST is wrapped by
    :func:`_post_page_with_retry`, which raises
    :class:`PaginationFetchError` on persistent transient failures or
    non-retryable 4xx. The exception propagates out of this function;
    callers (``_api_list``, ``_api_list_stream``,
    ``_list_all_sites``) do not have a try/except around the call,
    so the run surfaces in ``_process_one_board_streaming``'s generic
    ``except Exception`` and is recorded as a failure (no silent
    truncation — same shape of bug as #2722, #2737).
    """
    paths: list[str] = []
    total = 0
    facets: list[dict] = []
    offset = body.get("offset", 0)

    while True:
        payload = {**body, "limit": PAGE_SIZE, "offset": offset}
        data = await _post_page_with_retry(client, list_url, payload)

        if offset == 0:
            total = data.get("total", 0)
            facets = data.get("facets", [])

        postings = data.get("jobPostings", [])
        for item in postings:
            path = item.get("externalPath")
            if path:
                paths.append(path)

        offset += len(postings)
        if not postings or offset >= total:
            break

        # Early abort: we only needed total + facets from the first page
        if cap_abort and total >= cap_abort:
            log.info("workday.cap_abort", total=total, cap=cap_abort, fetched=len(paths))
            break

        if len(paths) >= MAX_JOBS:
            break

    return paths, total, facets


def _pick_split_facet(facets: list[dict]) -> tuple[str, list[str]] | None:
    """Choose a facet to split on when results hit the 2000 cap.

    Picks the facet with the most values where no single value >= cap,
    so each sub-query stays under the limit.
    """
    best: tuple[str, list[str]] | None = None
    best_count = 0

    for facet in facets:
        param = facet.get("facetParameter")
        values = facet.get("values", [])
        if not param or not values:
            continue
        # Skip facets where any single value is >= cap
        if any(v.get("count", 0) >= _API_RESULT_CAP for v in values):
            continue
        ids = [v["id"] for v in values if "id" in v]
        if len(ids) > best_count:
            best = (param, ids)
            best_count = len(ids)

    return best


async def _api_list(
    company: str,
    wd_instance: str,
    site: str,
    client: httpx.AsyncClient,
) -> tuple[list[str], bool]:
    """Collect all externalPaths, splitting by facet if the 2000 cap is hit.

    Returns ``(paths, truncated)``. ``truncated`` is True iff the stream
    yielded :data:`_TRUNCATED_PATH` — i.e. the MAX_JOBS cap was hit. The
    sentinel is stripped from ``paths`` so callers can ignore it; callers
    that care about the partial-cycle signal (e.g. the non-streaming
    ``discover``) consume the bool instead.
    """
    paths: list[str] = []
    truncated = False
    async for batch in _api_list_stream(company, wd_instance, site, client):
        for p in batch:
            if p == _TRUNCATED_PATH:
                truncated = True
            else:
                paths.append(p)
    return paths, truncated


async def _api_list_stream(
    company: str,
    wd_instance: str,
    site: str,
    client: httpx.AsyncClient,
):
    """Yield batches of externalPaths, splitting by facet if the 2000 cap is hit."""
    list_url = _api_list_url(company, wd_instance, site)

    # First, try unfaceted query (abort early if over cap — we only need facets)
    paths, total, facets = await _paginate_query(list_url, {}, client, cap_abort=_API_RESULT_CAP)

    if total < _API_RESULT_CAP:
        # Under the cap — we got everything
        yield paths[:MAX_JOBS]
        return

    # Hit the cap — split by facet to get all jobs
    split = _pick_split_facet(facets)
    if not split:
        log.warning(
            "workday.cap_no_split_facet",
            company=company,
            site=site,
            total=total,
        )
        yield paths[:MAX_JOBS]
        return

    facet_param, facet_ids = split
    log.info(
        "workday.splitting_by_facet",
        company=company,
        site=site,
        facet=facet_param,
        values=len(facet_ids),
    )

    seen: set[str] = set()
    total_count = 0

    for facet_id in facet_ids:
        body = {"appliedFacets": {facet_param: [facet_id]}}
        sub_paths, sub_total, _ = await _paginate_query(list_url, body, client)
        new_paths: list[str] = []
        for p in sub_paths:
            if p not in seen:
                seen.add(p)
                new_paths.append(p)
        total_count += len(new_paths)

        if new_paths:
            yield new_paths

        if total_count >= MAX_JOBS:
            log.warning(
                "workday.truncated",
                company=company,
                site=site,
                total=total_count,
                cap=MAX_JOBS,
            )
            yield [_TRUNCATED_PATH]
            return

    log.info("workday.faceted_total", company=company, site=site, jobs=total_count)


# ── Multi-site discovery ─────────────────────────────────────────────


async def _discover_sites(company: str, wd_instance: str, client: httpx.AsyncClient) -> list[str]:
    """Discover all job board sites for a Workday tenant via robots.txt."""
    url = f"https://{company}.{wd_instance}.myworkdayjobs.com/robots.txt"
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            log.warning("workday.robots_failed", company=company, status=resp.status_code)
            return []
    except Exception as exc:
        log.warning("workday.robots_error", company=company, error=str(exc))
        return []

    sites: list[str] = []
    for line in resp.text.splitlines():
        if line.startswith("Sitemap:"):
            m = _SITEMAP_RE.search(line)
            if m:
                sites.append(m.group(1))
    return sites


async def _list_all_sites(
    company: str,
    wd_instance: str,
    sites: list[str],
    client: httpx.AsyncClient,
) -> tuple[list[tuple[str, str]], bool]:
    """List jobs from all sites concurrently. Returns ``(site_paths, truncated)``.

    ``truncated`` is True iff the aggregate exceeded ``MAX_JOBS``. Caller
    (the non-streaming ``discover``) wraps the result in a partial
    ``MonitorResult`` so the pipeline suppresses gone-detection (#3216).
    """
    sem = asyncio.Semaphore(_LIST_CONCURRENCY)

    async def _list_one(site: str) -> tuple[list[tuple[str, str]], bool]:
        async with sem:
            paths, was_truncated = await _api_list(company, wd_instance, site, client)
            return [(site, p) for p in paths], was_truncated

    results = await asyncio.gather(*[_list_one(s) for s in sites], return_exceptions=True)

    site_paths: list[tuple[str, str]] = []
    any_site_truncated = False
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            log.warning("workday.site_list_error", site=sites[i], error=str(result))
        else:
            pairs, was_truncated = result
            site_paths.extend(pairs)
            if was_truncated:
                any_site_truncated = True
    truncated = any_site_truncated or len(site_paths) > MAX_JOBS
    return site_paths[:MAX_JOBS], truncated


async def _list_all_sites_stream(
    company: str,
    wd_instance: str,
    sites: list[str],
    client: httpx.AsyncClient,
):
    """Yield (site, path) batches per site for heartbeat-aware streaming.

    On reaching ``MAX_JOBS`` yields a final sentinel batch containing
    :data:`_TRUNCATED_SENTINEL` and stops. The outer ``discover_stream``
    detects the sentinel, drops it, and emits a flagged
    :class:`MonitorResult` so the pipeline marks the run partial and
    skips gone-detection (#3216).
    """
    sem = asyncio.Semaphore(_LIST_CONCURRENCY)
    total_count = 0

    for site in sites:
        async with sem:
            try:
                async for batch in _api_list_stream(company, wd_instance, site, client):
                    pairs = [(site, p) for p in batch]
                    total_count += len(pairs)
                    yield pairs
                    if total_count >= MAX_JOBS:
                        yield [_TRUNCATED_SENTINEL]
                        return
            except Exception as exc:
                log.warning("workday.site_list_error", site=site, error=str(exc))


# ── Main discover entry point ────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover job URLs from the Workday list API.

    By default discovers all sites for the tenant via robots.txt and
    aggregates URLs from every site.  Set ``"all_sites": false`` in board
    metadata to monitor only the configured site.

    Returns a set of job URLs (no detail fetching — that's the scraper's job),
    or a :class:`MonitorResult` with ``truncated=True`` when the MAX_JOBS
    cap was hit (#3216).
    """
    metadata = board.get("metadata") or {}
    company = metadata.get("company")
    wd_instance = metadata.get("wd_instance")
    site = metadata.get("site")

    if not (company and wd_instance and site):
        parsed = _parse_components(board["board_url"])
        if not parsed:
            raise ValueError(
                f"Cannot parse Workday components from board URL {board['board_url']!r} "
                "and no company/wd_instance/site in metadata"
            )
        company, wd_instance, site = parsed

    all_sites = metadata.get("all_sites", True)
    truncated = False

    if all_sites:
        sites = await _discover_sites(company, wd_instance, client)
        if not sites:
            log.warning("workday.no_sites_discovered", company=company, fallback=site)
            sites = [site]

        site_paths, truncated = await _list_all_sites(company, wd_instance, sites, client)
        log.info(
            "workday.listed_all",
            company=company,
            sites_total=len(sites),
            sites_with_jobs=len({s for s, _ in site_paths}),
            postings=len(site_paths),
        )
    else:
        paths, truncated = await _api_list(company, wd_instance, site, client)
        site_paths = [(site, p) for p in paths]
        log.info("workday.listed", company=company, site=site, postings=len(site_paths))

    urls = {_job_url(company, wd_instance, s, p) for s, p in site_paths}
    if truncated:
        return truncated_url_result(urls)
    return urls


async def discover_stream(board: dict, client: httpx.AsyncClient, pw=None):
    """Yield URL batches so the caller can pulse heartbeats on large boards.

    Same logic as discover() but yields intermediate sets of URLs after
    each site or facet sub-query completes, preventing worker pool timeouts.

    Strips the :data:`_TRUNCATED_PATH` / :data:`_TRUNCATED_SENTINEL` sentinels
    out of streamed batches and, on truncation, yields a final flagged
    :class:`MonitorResult` so the pipeline marks the cycle partial and
    skips gone-detection (#3216).
    """
    # Local import to avoid the top-level cycle with src.core.monitor.
    from src.core.monitor import MonitorResult as _MR

    metadata = board.get("metadata") or {}
    company = metadata.get("company")
    wd_instance = metadata.get("wd_instance")
    site = metadata.get("site")

    if not (company and wd_instance and site):
        parsed = _parse_components(board["board_url"])
        if not parsed:
            raise ValueError(
                f"Cannot parse Workday components from board URL {board['board_url']!r} "
                "and no company/wd_instance/site in metadata"
            )
        company, wd_instance, site = parsed

    all_sites = metadata.get("all_sites", True)
    truncated = False

    if all_sites:
        sites = await _discover_sites(company, wd_instance, client)
        if not sites:
            log.warning("workday.no_sites_discovered", company=company, fallback=site)
            sites = [site]

        total_urls = 0
        async for batch in _list_all_sites_stream(company, wd_instance, sites, client):
            clean: list[tuple[str, str]] = []
            for s, p in batch:
                if p == _TRUNCATED_PATH:
                    truncated = True
                else:
                    clean.append((s, p))
            if not clean:
                continue
            urls = {_job_url(company, wd_instance, s, p) for s, p in clean}
            total_urls += len(urls)
            yield urls

        log.info("workday.stream_done", company=company, total=total_urls)
    else:
        total_urls = 0
        async for batch in _api_list_stream(company, wd_instance, site, client):
            clean_paths = [p for p in batch if p != _TRUNCATED_PATH]
            if any(p == _TRUNCATED_PATH for p in batch):
                truncated = True
            if not clean_paths:
                continue
            urls = {_job_url(company, wd_instance, site, p) for p in clean_paths}
            total_urls += len(urls)
            yield urls

        log.info("workday.stream_done", company=company, site=site, total=total_urls)

    if truncated:
        yield _MR(urls=set(), truncated=True)


# ── Detection (used by ws probe) ─────────────────────────────────────


async def _fetch_job_count(
    company: str,
    wd_instance: str,
    site: str,
    client: httpx.AsyncClient,
) -> int | None:
    """Lightweight API call to get the job count.

    If ``total`` hits the 2000 cap, derives the true count from facet sums.
    """
    try:
        resp = await client.post(
            _api_list_url(company, wd_instance, site),
            json={"limit": 1, "offset": 0},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("total")
        if not isinstance(total, int):
            return None

        # If at the cap, derive true count from facet sums
        if total >= _API_RESULT_CAP:
            for facet in data.get("facets", []):
                values = facet.get("values", [])
                if values:
                    facet_sum = sum(v.get("count", 0) for v in values)
                    if facet_sum > total:
                        return facet_sum
        return total
    except Exception:
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Workday: URL pattern match -> page HTML scan.

    No slug-based probe fallback — Workday URLs are too specific to guess.
    """
    # Strategy 1: Direct URL pattern match
    parsed = _parse_components(url)
    if parsed:
        company, wd_instance, site = parsed
        result: dict = {"company": company, "wd_instance": wd_instance, "site": site}
        if client is not None:
            count = await _fetch_job_count(company, wd_instance, site, client)
            if count is not None:
                result["jobs"] = count
            elif "_" in company:
                # Python's ssl module rejects underscores in hostnames even
                # when the wildcard certificate is valid.  Retry without
                # SSL verification and flag the board so downstream
                # clients also disable verification.
                log.info("workday.ssl_retry", company=company)
                async with httpx.AsyncClient(
                    timeout=client.timeout,
                    follow_redirects=True,
                    verify=False,
                ) as insecure:
                    count = await _fetch_job_count(company, wd_instance, site, insecure)
                if count is not None:
                    result["jobs"] = count
                    result["ssl_verify"] = False
        return result

    if client is None:
        return None

    # Strategy 2: Scan page HTML for Workday markers
    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                # Found a Workday reference — try to extract full URL from the page
                full_match = _URL_RE.search(html)
                if full_match:
                    company = full_match.group(1)
                    wd_instance = f"wd{full_match.group(2)}"
                    site = full_match.group(3)
                    log.info(
                        "workday.detected_in_page",
                        url=url,
                        company=company,
                        site=site,
                    )
                    result = {"company": company, "wd_instance": wd_instance, "site": site}
                    count = await _fetch_job_count(company, wd_instance, site, client)
                    if count is not None:
                        result["jobs"] = count
                    elif "_" in company:
                        async with httpx.AsyncClient(
                            timeout=client.timeout,
                            follow_redirects=True,
                            verify=False,
                        ) as insecure:
                            count = await _fetch_job_count(company, wd_instance, site, insecure)
                        if count is not None:
                            result["jobs"] = count
                            result["ssl_verify"] = False
                    return result

    return None


register("workday", discover, cost=10, can_handle=can_handle, rich=False, stream=discover_stream)
