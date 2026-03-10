"""Workday Job Board API monitor.

Public API:
  List:   POST https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs
  Detail: GET  https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/job/{externalPath}

The list endpoint returns metadata (title, locationsText, postedOn) but not the
full job description.  The detail endpoint adds ``jobDescription`` (HTML),
``location``, ``additionalLocations``, ``timeType``, ``remoteType``, etc.
So the monitor fetches each posting individually for full data (N+1 calls,
with concurrency).

Max ``limit`` per request is **20** (higher values return 400).

The API caps results at **2000** per query.  When `total` reaches 2000 the
monitor automatically splits into per-facet queries (e.g. by job category)
so that each sub-query stays below the cap, then deduplicates.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 10_000
PAGE_SIZE = 20
CONCURRENCY = 5
_API_RESULT_CAP = 2000  # Workday caps list results at 2000 per query
_DETAIL_DELAY = 0.2  # Seconds between detail requests (per-slot)
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = (5.0, 15.0, 30.0)  # Backoff per attempt on 429

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


def _api_detail_url(company: str, wd_instance: str, site: str, external_path: str) -> str:
    # external_path may already start with /job/ (e.g. /job/City/Title_ID)
    if external_path.startswith("/job/"):
        return f"{_api_base(company, wd_instance)}/{site}{external_path}"
    return f"{_api_base(company, wd_instance)}/{site}/job{external_path}"


def _job_url(company: str, wd_instance: str, site: str, external_path: str) -> str:
    return f"https://{company}.{wd_instance}.myworkdayjobs.com/{site}{external_path}"


def _parse_job_location_type(remote_type: str | None) -> str | None:
    """Map Workday remoteType to normalized job_location_type."""
    if not remote_type:
        return None
    lower = remote_type.lower()
    if "remote" in lower:
        return "remote"
    if "flexible" in lower or "hybrid" in lower:
        return "hybrid"
    return None


def _parse_job(
    detail_data: dict,
    company: str,
    wd_instance: str,
    site: str,
) -> DiscoveredJob | None:
    """Map a detail API response to a DiscoveredJob."""
    info = detail_data.get("jobPostingInfo")
    if not info:
        return None

    title = info.get("title")
    external_path = info.get("externalPath")
    url = info.get("externalUrl")
    if not url and external_path:
        url = _job_url(company, wd_instance, site, external_path)
    if not url:
        return None
    description = info.get("jobDescription")

    # Locations
    locations: list[str] = []
    primary = info.get("location")
    if primary and isinstance(primary, str):
        locations.append(primary)
    additional = info.get("additionalLocations")
    if isinstance(additional, list):
        for loc in additional:
            if isinstance(loc, str) and loc and loc not in locations:
                locations.append(loc)

    # Employment type
    employment_type = info.get("timeType")

    # Date posted
    date_posted = info.get("startDate")

    # Metadata
    metadata: dict = {}
    job_req_id = info.get("jobReqId")
    if job_req_id:
        metadata["jobReqId"] = job_req_id

    return DiscoveredJob(
        url=url,
        title=title,
        description=description,
        locations=locations or None,
        employment_type=employment_type,
        job_location_type=_parse_job_location_type(info.get("remoteType")),
        date_posted=date_posted,
        metadata=metadata or None,
    )


async def _fetch_detail(
    company: str,
    wd_instance: str,
    site: str,
    external_path: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Fetch a single posting's detail with rate limiting and retry on 429."""
    async with semaphore:
        url = _api_detail_url(company, wd_instance, site, external_path)
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                await asyncio.sleep(_DETAIL_DELAY)
                resp = await client.get(url)
                if resp.status_code == 429:
                    backoff = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    log.warning(
                        "workday.detail_rate_limited",
                        external_path=external_path,
                        attempt=attempt + 1,
                        backoff_s=backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                if resp.status_code != 200:
                    log.warning(
                        "workday.detail_failed",
                        external_path=external_path,
                        status=resp.status_code,
                    )
                    return None
                return resp.json()
            except Exception as exc:
                log.warning("workday.detail_error", external_path=external_path, error=str(exc))
                return None
        log.warning("workday.detail_exhausted", external_path=external_path)
        return None


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


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Workday public API.

    Paginates the list endpoint, then fetches each posting's detail
    concurrently to get full descriptions.
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

    # Step 1: Paginate list endpoint to collect all external paths
    external_paths = await _api_list(company, wd_instance, site, client)
    log.info("workday.listed", company=company, site=site, postings=len(external_paths))

    # Step 2: Fetch details concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        _fetch_detail(company, wd_instance, site, path, client, semaphore)
        for path in external_paths
    ]
    detail_results = await asyncio.gather(*tasks)

    # Step 3: Parse into DiscoveredJobs
    jobs: list[DiscoveredJob] = []
    for detail in detail_results:
        if detail is None:
            continue
        parsed_job = _parse_job(detail, company, wd_instance, site)
        if parsed_job:
            jobs.append(parsed_job)

    return jobs


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


register("workday", discover, cost=10, can_handle=can_handle, rich=True)
