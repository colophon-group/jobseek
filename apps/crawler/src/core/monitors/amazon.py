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
from datetime import datetime

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register

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

_UNIT_MAP = {
    "year": "year",
    "yr": "year",
    "annually": "year",
    "per year": "year",
    "hour": "hour",
    "hr": "hour",
    "hourly": "hour",
    "per hour": "hour",
    "month": "month",
    "mo": "month",
    "monthly": "month",
    "per month": "month",
}


def _parse_salary(text: str | None) -> dict | None:
    if not text or not isinstance(text, str):
        return None

    # Try "$min/unit ... $max/unit" format first
    m = _SALARY_DOLLAR_RE.search(text)
    if m:
        sal_min = float(m.group(1).replace(",", ""))
        sal_max = float(m.group(3).replace(",", ""))
        unit = _UNIT_MAP.get(m.group(2).lower(), m.group(2).lower())
        return {"currency": "USD", "min": sal_min, "max": sal_max, "unit": unit}

    # Try "min - max CURRENCY unit" format
    m = _SALARY_RANGE_RE.search(text)
    if m:
        sal_min = float(m.group(1).replace(",", ""))
        sal_max = float(m.group(2).replace(",", ""))
        currency = m.group(3).upper()
        unit = _UNIT_MAP.get(m.group(4).lower(), m.group(4).lower())
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
        resp.raise_for_status()
        data = resp.json()

        error = data.get("error")
        if error:
            raise ValueError(f"Amazon API error: {error}")

        hits = data.get("hits", 0)
        jobs = data.get("jobs") or []
        return jobs, hits


async def _paginate_query(
    client: httpx.AsyncClient,
    base_params: dict | None = None,
) -> tuple[list[DiscoveredJob], int]:
    """Paginate through a single query. Returns (jobs, total_hits)."""
    params = dict(base_params or {})
    params["result_limit"] = PAGE_SIZE
    params["sort"] = "recent"

    # First page — sequential to learn total
    params["offset"] = 0
    seq = asyncio.Semaphore(1)
    first_jobs, total = await _fetch_page(client, params, seq)

    jobs: list[DiscoveredJob] = []
    for raw in first_jobs:
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)

    if total <= PAGE_SIZE:
        return jobs, total

    # Paginate remaining — cap at API_RESULT_CAP
    page_cap = min(total, _API_RESULT_CAP)
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    tasks = []
    for offset in range(PAGE_SIZE, page_cap, PAGE_SIZE):
        page_params = {**params, "offset": offset}
        tasks.append(_fetch_page(client, page_params, semaphore))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            log.warning("amazon.page_error", error=str(result))
            continue
        page_jobs, _ = result
        for raw in page_jobs:
            parsed = _parse_job(raw)
            if parsed:
                jobs.append(parsed)

    return jobs, total


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
            break

    log.info("amazon.discovered", total=len(all_jobs), countries=len(country_codes))
    return all_jobs


register("amazon", discover, cost=10, rich=True)
