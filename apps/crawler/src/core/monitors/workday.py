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

log = structlog.get_logger()

MAX_JOBS = 10_000
PAGE_SIZE = 20
_LIST_CONCURRENCY = 5  # Parallel site listing during multi-site discovery
_API_RESULT_CAP = 2000  # Workday caps list results at 2000 per query
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = (5.0, 15.0, 30.0)  # Backoff per attempt on 429

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


async def _paginate_query(
    list_url: str,
    body: dict,
    client: httpx.AsyncClient,
) -> tuple[list[str], int, list[dict]]:
    """Paginate a single list query. Returns (paths, total, facets)."""
    paths: list[str] = []
    total = 0
    facets: list[dict] = []
    offset = body.get("offset", 0)

    while True:
        payload = {**body, "limit": PAGE_SIZE, "offset": offset}
        data = None
        for attempt in range(_RETRY_ATTEMPTS):
            resp = await client.post(
                list_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 429:
                backoff = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                log.warning("workday.list_rate_limited", offset=offset, backoff_s=backoff)
                await asyncio.sleep(backoff)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        if data is None:
            log.warning("workday.list_exhausted", offset=offset)
            break

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
) -> list[str]:
    """Collect all externalPaths, splitting by facet if the 2000 cap is hit."""
    list_url = _api_list_url(company, wd_instance, site)

    # First, try unfaceted query
    paths, total, facets = await _paginate_query(list_url, {}, client)

    if total < _API_RESULT_CAP:
        # Under the cap — we got everything
        return paths[:MAX_JOBS]

    # Hit the cap — split by facet to get all jobs
    split = _pick_split_facet(facets)
    if not split:
        log.warning(
            "workday.cap_no_split_facet",
            company=company,
            site=site,
            total=total,
        )
        return paths[:MAX_JOBS]

    facet_param, facet_ids = split
    log.info(
        "workday.splitting_by_facet",
        company=company,
        site=site,
        facet=facet_param,
        values=len(facet_ids),
    )

    seen: set[str] = set()
    all_paths: list[str] = []

    for facet_id in facet_ids:
        body = {"appliedFacets": {facet_param: [facet_id]}}
        sub_paths, sub_total, _ = await _paginate_query(list_url, body, client)
        for p in sub_paths:
            if p not in seen:
                seen.add(p)
                all_paths.append(p)

        if len(all_paths) >= MAX_JOBS:
            log.warning(
                "workday.truncated",
                company=company,
                site=site,
                total=len(all_paths),
                cap=MAX_JOBS,
            )
            return all_paths[:MAX_JOBS]

    log.info("workday.faceted_total", company=company, site=site, jobs=len(all_paths))
    return all_paths


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
) -> list[tuple[str, str]]:
    """List jobs from all sites concurrently. Returns (site, path) pairs."""
    sem = asyncio.Semaphore(_LIST_CONCURRENCY)

    async def _list_one(site: str) -> list[tuple[str, str]]:
        async with sem:
            paths = await _api_list(company, wd_instance, site, client)
            return [(site, p) for p in paths]

    results = await asyncio.gather(*[_list_one(s) for s in sites], return_exceptions=True)

    site_paths: list[tuple[str, str]] = []
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            log.warning("workday.site_list_error", site=sites[i], error=str(result))
        else:
            site_paths.extend(result)
    return site_paths[:MAX_JOBS]


# ── Main discover entry point ────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover job URLs from the Workday list API.

    By default discovers all sites for the tenant via robots.txt and
    aggregates URLs from every site.  Set ``"all_sites": false`` in board
    metadata to monitor only the configured site.

    Returns a set of job URLs (no detail fetching — that's the scraper's job).
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

    if all_sites:
        sites = await _discover_sites(company, wd_instance, client)
        if not sites:
            log.warning("workday.no_sites_discovered", company=company, fallback=site)
            sites = [site]

        site_paths = await _list_all_sites(company, wd_instance, sites, client)
        log.info(
            "workday.listed_all",
            company=company,
            sites_total=len(sites),
            sites_with_jobs=len({s for s, _ in site_paths}),
            postings=len(site_paths),
        )
    else:
        paths = await _api_list(company, wd_instance, site, client)
        site_paths = [(site, p) for p in paths]
        log.info("workday.listed", company=company, site=site, postings=len(site_paths))

    return {_job_url(company, wd_instance, s, p) for s, p in site_paths}


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
                    return result

    return None


register("workday", discover, cost=10, can_handle=can_handle, rich=False)
