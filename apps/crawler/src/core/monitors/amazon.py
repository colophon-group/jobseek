"""Amazon Jobs API monitor.

Public API: GET https://www.amazon.jobs/en/search.json

Parameters:
  - result_limit: page size (max 100)
  - offset: pagination offset (0-based)
  - base_query: text search
  - sort: "recent" (CREATED_DATE desc) or omit (SCORE desc)
  - category[]: job category slug (repeatable)
  - business_category[]: team/division slug (repeatable)
  - schedule_type_id[]: e.g. "Full-Time" (repeatable)
  - country: ISO 3166-1 alpha-3 code (e.g. "USA", "DEU")
  - city: city name

Max 100 results per page, max 10,000 results per query (offset >= 10000 errors).
When total exceeds 10k, the monitor partitions by country code.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import datetime

import httpx
import structlog

from src.core.enum_normalize import normalize_salary_unit
from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

API_URL = "https://www.amazon.jobs/en/search.json"
PAGE_SIZE = 100
MAX_JOBS = 50_000
_API_RESULT_CAP = 10_000
_CONCURRENCY = 5

# Fallback lists used when live fetching fails.
_FALLBACK_COUNTRY_CODES = [
    "USA",
    "CAN",
    "CRI",
    "MEX",
    "COL",
    "BRA",
    "GBR",
    "DEU",
    "FRA",
    "IRL",
    "ESP",
    "ITA",
    "NLD",
    "LUX",
    "POL",
    "ROU",
    "CZE",
    "BEL",
    "FIN",
    "CHE",
    "SVK",
    "IND",
    "CHN",
    "JPN",
    "SGP",
    "AUS",
    "KOR",
    "ISR",
    "ARE",
    "SAU",
    "ZAF",
    "EGY",
    "MYS",
    "PHL",
    "TWN",
    "TUR",
    "VNM",
    "NZL",
    "HKG",
    "THA",
    "IDN",
    "JOR",
    "BGD",
    "PER",
    "CHL",
    "ARG",
]

_FALLBACK_CATEGORY_SLUGS = [
    "administrative-support",
    "audio-video-photography-production",
    "business-intelligence-data-engineering",
    "business-merchant-development",
    "buying-planning-instock-management",
    "customer-service",
    "data-science",
    "database-administration",
    "design",
    "economics",
    "editorial-writing-content-management",
    "facilities-maintenance-real-estate",
    "fgbs",
    "fulfillment-center-warehouse-associate",
    "fulfillment-operations-management",
    "hardware-development",
    "human-resources",
    "investigation-loss-prevention",
    "leadership-development-training",
    "legal",
    "machine-learning-science",
    "marketing",
    "medical-health-safety",
    "operations-it-support-engineering",
    "project-program-product-management-non-tech",
    "project-program-product-management-technical",
    "public-policy",
    "public-relations-communications",
    "research-science",
    "sales-advertising-account-management",
    "software-development",
    "solutions-architecture",
    "supply-chain-transportation-management",
    "systems-quality-security-engineering",
]

_CATEGORIES_URL = "https://www.amazon.jobs/en/job_categories"
_CATEGORY_SLUG_RE = re.compile(r"/job-categories/([a-z0-9-]+)")


async def _fetch_category_slugs(client: httpx.AsyncClient) -> list[str]:
    """Scrape job category slugs from the Amazon Jobs categories page."""
    try:
        resp = await client.get(_CATEGORIES_URL, follow_redirects=True)
        if resp.status_code != 200:
            log.warning("amazon.categories_fetch_failed", status=resp.status_code)
            return _FALLBACK_CATEGORY_SLUGS
        slugs = list(dict.fromkeys(_CATEGORY_SLUG_RE.findall(resp.text)))
        if not slugs:
            log.warning("amazon.categories_parse_empty")
            return _FALLBACK_CATEGORY_SLUGS
        log.info("amazon.categories_fetched", count=len(slugs))
        return slugs
    except Exception as exc:
        log.warning("amazon.categories_fetch_error", error=str(exc))
        return _FALLBACK_CATEGORY_SLUGS


# "$151,300/year ... $261,500/year"
_SALARY_DOLLAR_RE = re.compile(
    r"\$([\d,]+(?:\.\d+)?)\s*/\s*(\w+).*?\$([\d,]+(?:\.\d+)?)\s*/\s*(\w+)"
)
# "91,000.00 - 136,500.00 USD annually"
_SALARY_RANGE_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*[-–—]\s*([\d,]+(?:\.\d+)?)\s+(\w{3})\s+"
    r"(annually|hourly|monthly|per hour|per year|per month)",
    re.IGNORECASE,
)


def _parse_salary(text: str | None) -> dict | None:
    if not text or not isinstance(text, str):
        return None

    # Try "$min/unit ... $max/unit" format first
    m = _SALARY_DOLLAR_RE.search(text)
    if m:
        sal_min = float(m.group(1).replace(",", ""))
        sal_max = float(m.group(3).replace(",", ""))
        # Amazon's regex captures bare ``year``/``hour``/``mo``/``yr``
        # tokens; preserve the lowercase raw token as a tail fallback so
        # any future regex extension lands here cleanly without
        # unintentionally dropping to ``None``.
        raw_unit = m.group(2).lower()
        unit = normalize_salary_unit(raw_unit) or raw_unit
        return {"currency": "USD", "min": sal_min, "max": sal_max, "unit": unit}

    # Try "min - max CURRENCY unit" format
    m = _SALARY_RANGE_RE.search(text)
    if m:
        sal_min = float(m.group(1).replace(",", ""))
        sal_max = float(m.group(2).replace(",", ""))
        currency = m.group(3).upper()
        raw_unit = m.group(4).lower()
        unit = normalize_salary_unit(raw_unit) or raw_unit
        return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}

    return None


def _parse_date(text: str | None) -> str | None:
    """Parse 'March  9, 2026' → '2026-03-09'."""
    if not text or not isinstance(text, str):
        return None
    # Normalize double spaces (Amazon uses "March  9, 2026" for single-digit days)
    normalized = re.sub(r"\s+", " ", text.strip())
    try:
        dt = datetime.strptime(normalized, "%B %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_job(raw: dict) -> DiscoveredJob | None:
    job_path = raw.get("job_path")
    if not job_path:
        return None

    url = f"https://www.amazon.jobs{job_path}"

    # Description: combine description + qualifications
    parts: list[str] = []
    desc = raw.get("description")
    if desc:
        parts.append(desc)
    basic = raw.get("basic_qualifications")
    if basic:
        parts.append(f"<h3>Basic Qualifications</h3>\n{basic}")
    preferred = raw.get("preferred_qualifications")
    if preferred:
        parts.append(f"<h3>Preferred Qualifications</h3>\n{preferred}")
    description = "\n".join(parts) if parts else None

    # Locations — use normalized_location for readability
    locations: list[str] | None = None
    norm_loc = raw.get("normalized_location")
    if norm_loc:
        locations = [norm_loc]
    elif raw.get("location"):
        locations = [raw["location"]]

    # Metadata
    metadata: dict = {}
    for key in (
        "id_icims",
        "job_category",
        "job_family",
        "business_category",
        "company_name",
        "country_code",
    ):
        val = raw.get(key)
        if val:
            metadata[key] = val

    return DiscoveredJob(
        url=url,
        title=raw.get("title"),
        description=description,
        locations=locations,
        employment_type=raw.get("job_schedule_type"),
        date_posted=_parse_date(raw.get("posted_date")),
        base_salary=_parse_salary(raw.get("salary")),
        metadata=metadata or None,
    )


async def _fetch_page(
    client: httpx.AsyncClient,
    params: dict,
    semaphore: asyncio.Semaphore,
) -> tuple[list[dict], int]:
    """Fetch a single page. Returns (raw_jobs, total_hits)."""
    async with semaphore:
        resp = await client.get(API_URL, params=params)
        if resp.status_code == 404 and params.get("offset", 0) == 0:
            raise BoardGoneError("Amazon Jobs API returned 404", url=str(resp.url))
        resp.raise_for_status()
        data = resp.json()

        error = data.get("error")
        if error:
            raise ValueError(f"Amazon API error: {error}")

        hits = data.get("hits", 0)
        jobs = data.get("jobs") or []
        return jobs, hits


async def _paginate_raw_query_stream(
    client: httpx.AsyncClient,
    base_params: dict | None = None,
) -> AsyncIterator[tuple[list[dict], int]]:
    """Yield one raw API page at a time with a bounded prefetch window.

    Amazon pages contain full descriptions and expand substantially when
    decoded into Python objects. Keeping every page task in one ``gather``
    retained both the complete raw payload and the parsed job list, which let
    one 10,000-result query consume hundreds of MiB. At most
    ``_CONCURRENCY`` decoded pages are now live inside this generator.
    """
    params = dict(base_params or {})
    params["result_limit"] = PAGE_SIZE
    params["sort"] = "recent"

    # First page — sequential to learn total
    params["offset"] = 0
    seq = asyncio.Semaphore(1)
    first_jobs, total = await _fetch_page(client, params, seq)
    yield first_jobs, total

    if total <= PAGE_SIZE:
        return

    # Paginate remaining — cap at API_RESULT_CAP
    page_cap = min(total, _API_RESULT_CAP)
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    offsets = range(PAGE_SIZE, page_cap, PAGE_SIZE)
    for window_start in range(0, len(offsets), _CONCURRENCY):
        window = offsets[window_start : window_start + _CONCURRENCY]
        tasks = [_fetch_page(client, {**params, "offset": offset}, semaphore) for offset in window]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                log.warning("amazon.page_error", error=str(result))
                continue
            page_jobs, _ = result
            yield page_jobs, total


async def _paginate_query_stream(
    client: httpx.AsyncClient,
    base_params: dict | None = None,
) -> AsyncIterator[tuple[list[DiscoveredJob], int]]:
    """Yield parsed jobs in API-page-sized batches."""

    async for raw_jobs, total in _paginate_raw_query_stream(client, base_params):
        parsed_jobs = _parse_jobs(raw_jobs)
        del raw_jobs
        yield parsed_jobs, total


def _parse_jobs(raw_jobs: list[dict]) -> list[DiscoveredJob]:
    jobs: list[DiscoveredJob] = []
    for raw in raw_jobs:
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)
    return jobs


def _deduplicate_jobs(
    jobs: list[DiscoveredJob],
    seen_urls: set[str],
    *,
    limit: int,
) -> list[DiscoveredJob]:
    """Return unseen jobs without allowing the retained URL set past ``limit``."""

    new_jobs: list[DiscoveredJob] = []
    for job in jobs:
        if job.url in seen_urls:
            continue
        if len(seen_urls) >= limit:
            break
        seen_urls.add(job.url)
        new_jobs.append(job)
    return new_jobs


async def _paginate_query(
    client: httpx.AsyncClient,
    base_params: dict | None = None,
) -> tuple[list[DiscoveredJob], int]:
    """Collect a query for non-streaming callers.

    Worker execution uses :func:`discover_stream`; this compatibility helper
    remains for probes and tests that explicitly request a complete list.
    """

    jobs: list[DiscoveredJob] = []
    _total = 0
    async for page_jobs, _total in _paginate_query_stream(client, base_params):
        jobs.extend(page_jobs)

    return jobs, _total


async def _partition_by_category(
    client: httpx.AsyncClient,
    base_params: dict,
    initial_jobs: list[DiscoveredJob],
    initial_total: int,
    category_slugs: list[str],
) -> list[DiscoveredJob]:
    """Split a query by job category when it exceeds the 10k API cap.

    Starts from the jobs already fetched in the capped query, then iterates
    through category slugs to collect the rest.
    """
    country = base_params.get("country", "?")
    log.info(
        "amazon.partitioning_by_category",
        country=country,
        initial_total=initial_total,
    )

    seen_urls: set[str] = set()
    all_jobs: list[DiscoveredJob] = []

    # Seed with what we already have from the capped query
    for job in initial_jobs:
        seen_urls.add(job.url)
        all_jobs.append(job)

    for slug in category_slugs:
        params = {**base_params, "category[]": slug}
        cat_jobs, cat_total = await _paginate_query(client, params)

        if cat_total == 0:
            continue

        new_count = 0
        for job in cat_jobs:
            if job.url not in seen_urls:
                seen_urls.add(job.url)
                all_jobs.append(job)
                new_count += 1

        if new_count > 0:
            log.info(
                "amazon.category",
                country=country,
                category=slug,
                total=cat_total,
                new=new_count,
            )

    log.info(
        "amazon.category_partition_done",
        country=country,
        jobs=len(all_jobs),
    )
    return all_jobs


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Amazon Jobs API.

    When total results exceed the 10k API cap, partitions by country code
    to retrieve all postings.
    """
    metadata = board.get("metadata") or {}

    # Build base params from config
    base_params: dict = {}
    if country := metadata.get("country"):
        base_params["country"] = country
    if category := metadata.get("category"):
        base_params["category[]"] = category
    if biz_cat := metadata.get("business_category"):
        base_params["business_category[]"] = biz_cat

    # Try unpartitioned first
    jobs, total = await _paginate_query(client, base_params)

    if total < _API_RESULT_CAP:
        log.info("amazon.discovered", total=total, jobs=len(jobs))
        return jobs

    # Already filtered by country — split further by category
    if "country" in base_params:
        if "category[]" in base_params:
            # Already filtered by both country and category — nothing more to split
            log.warning(
                "amazon.cap_country_category",
                country=base_params["country"],
                category=base_params["category[]"],
                jobs=len(jobs),
            )
            return jobs
        category_slugs = await _fetch_category_slugs(client)
        return await _partition_by_category(client, base_params, jobs, total, category_slugs)

    # Extract country codes from the initial results (each job has country_code)
    country_codes = sorted(
        {j.metadata["country_code"] for j in jobs if j.metadata and j.metadata.get("country_code")}
    )
    if not country_codes:
        country_codes = _FALLBACK_COUNTRY_CODES
        log.warning("amazon.countries_fallback")
    else:
        log.info("amazon.countries_from_api", count=len(country_codes))

    # Hit the 10k cap — partition by country
    log.info("amazon.partitioning_by_country", initial_total=total)

    # Lazily fetched when a country exceeds the cap
    category_slugs: list[str] | None = None

    seen_urls: set[str] = set()
    all_jobs: list[DiscoveredJob] = []

    for country_code in country_codes:
        params = {**base_params, "country": country_code}
        country_jobs, country_total = await _paginate_query(client, params)

        if country_total == 0:
            continue

        # Country itself hit the cap — split further by category
        if country_total >= _API_RESULT_CAP:
            if category_slugs is None:
                category_slugs = await _fetch_category_slugs(client)
            country_jobs = await _partition_by_category(
                client,
                params,
                country_jobs,
                country_total,
                category_slugs,
            )

        new_count = 0
        for job in country_jobs:
            if job.url not in seen_urls:
                seen_urls.add(job.url)
                all_jobs.append(job)
                new_count += 1

        log.info(
            "amazon.country",
            country=country_code,
            total=country_total,
            new=new_count,
        )

        if len(all_jobs) >= MAX_JOBS:
            log.warning("amazon.truncated", total=len(all_jobs), cap=MAX_JOBS)
            log.info("amazon.discovered", total=len(all_jobs), countries=len(country_codes))
            return truncated_rich_result(all_jobs)

    log.info("amazon.discovered", total=len(all_jobs), countries=len(country_codes))
    return all_jobs


async def discover_stream(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover Amazon jobs while retaining only a bounded page window."""
    metadata = board.get("metadata") or {}

    # Build base params from config
    base_params: dict = {}
    if country := metadata.get("country"):
        base_params["country"] = country
    if category := metadata.get("category"):
        base_params["category[]"] = category
    if biz_cat := metadata.get("business_category"):
        base_params["business_category[]"] = biz_cat

    # Local import to avoid a top-level cycle with src.core.monitor.
    from src.core.monitor import MonitorResult as _MR

    seen_urls: set[str] = set()

    def bounded_page(jobs: list[DiscoveredJob]) -> list[DiscoveredJob]:
        return _deduplicate_jobs(jobs, seen_urls, limit=MAX_JOBS)

    def truncated() -> bool:
        return len(seen_urls) >= MAX_JOBS

    def trailing_truncation():
        log.warning("amazon.truncated", total=len(seen_urls), cap=MAX_JOBS)
        # The pipeline marks the cycle partial and skips gone-detection for
        # the unseen tail beyond MAX_JOBS (#3216).
        return _MR(urls=set(), jobs_by_url={}, truncated=True)

    # Start with the unpartitioned query. If it fits under Amazon's API cap,
    # its pages can flow directly downstream. Otherwise, scan only raw page
    # dictionaries for country codes; do not construct or retain 10,000 rich
    # jobs that will immediately be fetched again by country.
    raw_pages = _paginate_raw_query_stream(client, base_params)
    first_raw, total = await anext(raw_pages)

    if total < _API_RESULT_CAP:
        first_batch = bounded_page(_parse_jobs(first_raw))
        del first_raw
        if first_batch:
            yield first_batch
        if truncated():
            yield trailing_truncation()
            return
        async for raw_jobs, _ in raw_pages:
            batch = bounded_page(_parse_jobs(raw_jobs))
            if batch:
                yield batch
            if truncated():
                yield trailing_truncation()
                return
        log.info("amazon.discovered", total=total, jobs=len(seen_urls))
        return

    if "country" in base_params:
        # A configured country query is itself useful output. Stream its
        # capped pages, then split by category if that dimension is available.
        batch = bounded_page(_parse_jobs(first_raw))
        del first_raw
        if batch:
            yield batch
        if truncated():
            yield trailing_truncation()
            return
        async for raw_jobs, _ in raw_pages:
            batch = bounded_page(_parse_jobs(raw_jobs))
            if batch:
                yield batch
            if truncated():
                yield trailing_truncation()
                return

        if "category[]" in base_params:
            log.warning(
                "amazon.cap_country_category",
                country=base_params["country"],
                category=base_params["category[]"],
                jobs=len(seen_urls),
            )
            return

        category_slugs = await _fetch_category_slugs(client)
        for slug in category_slugs:
            _category_total = 0
            new_count = 0
            params = {**base_params, "category[]": slug}
            async for jobs, _category_total in _paginate_query_stream(client, params):
                batch = bounded_page(jobs)
                new_count += len(batch)
                if batch:
                    yield batch
                if truncated():
                    yield trailing_truncation()
                    return
            if new_count:
                log.info(
                    "amazon.category",
                    country=base_params["country"],
                    category=slug,
                    total=_category_total,
                    new=new_count,
                )
        log.info("amazon.discovered", total=len(seen_urls), countries=1)
        return

    country_codes: set[str] = {
        str(raw["country_code"]) for raw in first_raw if raw.get("country_code")
    }
    del first_raw
    async for raw_jobs, _ in raw_pages:
        country_codes.update(
            str(raw["country_code"]) for raw in raw_jobs if raw.get("country_code")
        )

    if not country_codes:
        ordered_country_codes = _FALLBACK_COUNTRY_CODES
        log.warning("amazon.countries_fallback")
    else:
        ordered_country_codes = sorted(country_codes)
        log.info("amazon.countries_from_api", count=len(ordered_country_codes))

    log.info("amazon.partitioning_by_country", initial_total=total)
    category_slugs: list[str] | None = None

    for country_code in ordered_country_codes:
        params = {**base_params, "country": country_code}
        _country_total = 0
        country_new = 0
        async for jobs, _country_total in _paginate_query_stream(client, params):
            batch = bounded_page(jobs)
            country_new += len(batch)
            if batch:
                yield batch
            if truncated():
                yield trailing_truncation()
                return

        if _country_total >= _API_RESULT_CAP:
            if category_slugs is None:
                category_slugs = await _fetch_category_slugs(client)
            for slug in category_slugs:
                _category_total = 0
                category_new = 0
                category_params = {**params, "category[]": slug}
                async for jobs, _category_total in _paginate_query_stream(client, category_params):
                    batch = bounded_page(jobs)
                    category_new += len(batch)
                    country_new += len(batch)
                    if batch:
                        yield batch
                    if truncated():
                        yield trailing_truncation()
                        return
                if category_new:
                    log.info(
                        "amazon.category",
                        country=country_code,
                        category=slug,
                        total=_category_total,
                        new=category_new,
                    )

        if _country_total:
            log.info(
                "amazon.country",
                country=country_code,
                total=_country_total,
                new=country_new,
            )

    log.info("amazon.discovered", total=len(seen_urls), countries=len(ordered_country_codes))


register("amazon", discover, cost=10, rich=True, stream=discover_stream)
