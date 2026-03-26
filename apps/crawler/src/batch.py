"""Batch processor — Layer 2.

Claims due work from the DB, runs single jobs concurrently, writes results back.
Portable across all deployment environments.

Concurrency model: domain-parallel pipelines.  Boards sharing a rate-limit
domain (same ATS API or hostname) are processed serially to respect politeness.
Different domains run fully concurrently for maximum throughput.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime
from time import monotonic
from urllib.parse import urlparse

import asyncpg
import httpx
import structlog

from src.config import settings
from src.core.description_store import content_hash
from src.core.enum_normalize import normalize_employment_type
from src.core.experience_extract import extract_experience
from src.core.location_resolve import LocationResolver
from src.core.monitor import monitor_one, monitor_one_stream
from src.core.monitors import api_monitor_types, get_stream_fn, monitor_needs_browser
from src.core.occupation_resolve import load_occupation_ids, match_occupation
from src.core.salary_extract import extract_salary_unified
from src.core.scrape import scrape_one
from src.core.scrapers import (
    JobContent,
    enrich_description,
    get_scraper,
    get_scraper_type,
    scraper_needs_browser,
)
from src.core.seniority_resolve import load_seniority_ids, match_seniority
from src.core.technology_resolve import load_technology_ids, match_technologies
from src.shared.html_normalize import normalize_description_html
from src.shared.langdetect import detect_all_languages, detect_language
from src.shared.redis import get_redis

log = structlog.get_logger()


# ── Constants ────────────────────────────────────────────────────────

# API monitor types share a single API host per type (throttle-domain keys).
_API_MONITOR_TYPES = api_monitor_types()

# Lazy-loaded singletons (populated once per batch run)
_location_resolver: LocationResolver | None = None
_technology_id_map: dict[str, int] | None = None
_occupation_id_map: dict[str, int] | None = None
_seniority_id_map: dict[str, int] | None = None
_currency_rates: dict[str, float] | None = None

# Max R2 backfill uploads per board run (touched postings without hashes).
# Prevents huge first-time runs from timing out. Backfill completes incrementally.
_R2_BACKFILL_LIMIT = 500

# Sentinel value used to signal workers to shut down.
_SENTINEL = None


@dataclass
class JobCPUResult:
    """CPU-processed job data ready for INSERT."""

    url: str
    insert_record: tuple  # positional params for _INSERT_RICH_JOB
    r2_staging_args: dict  # kwargs for _stage_r2_pending
    tech_ids: list[int] | None


@dataclass
class BoardBatch:
    """One batch from a board -> DB writer."""

    board_id: str
    company_id: str
    board_url: str
    enrich_fields: list[str] | None
    urls: set[str]
    jobs_by_url: dict | None  # DiscoveredJob dict, or None for URL-only
    cpu_results: dict[str, JobCPUResult]  # keyed by URL
    delist_threshold: int


@dataclass
class BoardDone:
    """Final signal for a board -> DB writer runs mark_gone + record_success."""

    board_id: str
    board_url: str
    all_urls: set[str]
    delist_threshold: int
    total_new: int
    total_relisted: int


@dataclass
class BoardError:
    """Worker error -> DB writer runs _RECORD_FAILURE."""

    board_id: str
    board_url: str
    error_msg: str


@dataclass
class ScrapeResult:
    """Scrape result -> DB writer runs _UPDATE_JOB_CONTENT or _UPDATE_ENRICH_CONTENT."""

    job_posting_id: str
    params: tuple  # positional args for the SQL query
    is_enrich: bool


@dataclass
class ScrapeError:
    """Scrape error -> DB writer runs _RECORD_SCRAPE_FAILURE."""

    job_posting_id: str


@dataclass
class _ScrapeWorkItem:
    """Bundle of ScrapeItem + resolved scraper config for the pipeline worker."""

    item: ScrapeItem
    scraper_type: str
    scraper_config: dict | None
    enrich_fields: list[str] | None
    ssl_verify: bool = True


# Titles that indicate a broken scrape (auth wall, CAPTCHA, etc.)
_GARBAGE_TITLES = frozenset(
    s.lower()
    for s in (
        "Not Logged In",
        "Log in to Career Profile",
        "Access Denied",
        "Just a moment...",
        "Page Not Found",
        "404",
        "403 Forbidden",
        "Sign In",
        "Login",
        "Redirecting",
    )
)


def _is_garbage_title(title: str) -> bool:
    """Return True if the title is a known broken-scrape artifact."""
    return title.strip().lower() in _GARBAGE_TITLES


async def _get_location_resolver(pool: asyncpg.Pool) -> LocationResolver:
    """Get or create the location resolver singleton."""
    global _location_resolver
    if _location_resolver is None:
        _location_resolver = LocationResolver()
        await _location_resolver.load(pool)
        log.info("batch.location_resolver.loaded", entries=_location_resolver.entry_count)
    return _location_resolver


async def _flush_location_misses(
    resolver: LocationResolver,
    pool: asyncpg.Pool,
) -> None:
    """Drain location misses from the resolver and upsert to taxonomy_miss."""
    raw_misses = resolver.drain_location_misses()
    if not raw_misses:
        return
    seen: set[str] = set()
    deduped_raw: list[str] = []
    deduped_sample: list[str] = []
    for raw, sample in raw_misses:
        if raw not in seen:
            seen.add(raw)
            deduped_raw.append(raw)
            deduped_sample.append(sample)
    await pool.execute(_UPSERT_LOCATION_MISSES, deduped_raw, deduped_sample)


async def _get_technology_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Get or load the technology slug -> id mapping."""
    global _technology_id_map
    if _technology_id_map is None:
        _technology_id_map = await load_technology_ids(pool)
        log.info("batch.technology_ids.loaded", count=len(_technology_id_map))
    return _technology_id_map


async def _get_occupation_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Get or load the occupation slug -> id mapping."""
    global _occupation_id_map
    if _occupation_id_map is None:
        _occupation_id_map = await load_occupation_ids(pool)
        log.info("batch.occupation_ids.loaded", count=len(_occupation_id_map))
    return _occupation_id_map


async def _get_seniority_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Get or load the seniority slug -> id mapping."""
    global _seniority_id_map
    if _seniority_id_map is None:
        _seniority_id_map = await load_seniority_ids(pool)
        log.info("batch.seniority_ids.loaded", count=len(_seniority_id_map))
    return _seniority_id_map


def _resolve_occupation_seniority(
    titles: list[str] | str | None,
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
) -> tuple[int | None, int | None]:
    """Resolve occupation_id and seniority_id from job title(s).

    Tries each title individually and returns the first match for each.
    This handles multilingual titles correctly (e.g. German title may
    match seniority while English title matches occupation).
    """
    if not titles:
        return None, None
    if isinstance(titles, str):
        titles = [titles]

    occ_id: int | None = None
    sen_id: int | None = None
    for title in titles:
        if not title or not isinstance(title, str):
            continue
        title = title.strip()
        if not title:
            continue
        if occ_id is None:
            slug = match_occupation(title)
            if slug:
                occ_id = occ_ids.get(slug)
        if sen_id is None:
            slug = match_seniority(title)
            if slug:
                sen_id = sen_ids.get(slug)
        if occ_id is not None and sen_id is not None:
            break
    return occ_id, sen_id


def _resolve_technology_ids(description: str | None, tech_ids: dict[str, int]) -> list[int] | None:
    """Extract technology IDs from description text. Returns None if no matches."""
    if not description:
        return None
    slugs = match_technologies(description)
    if not slugs:
        return None
    ids = sorted({tech_ids[s] for s in slugs if s in tech_ids})
    return ids or None


async def _get_currency_rates(pool: asyncpg.Pool) -> dict[str, float]:
    """Get or load the currency -> to_eur rate mapping."""
    global _currency_rates
    if _currency_rates is None:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT currency, to_eur FROM currency_rate")
        _currency_rates = {r["currency"]: float(r["to_eur"]) for r in rows}
        log.info("batch.currency_rates.loaded", count=len(_currency_rates))
    return _currency_rates


def _extract_salary_fields(
    html: str | None,
    rates: dict[str, float],
) -> tuple[int | None, int | None, str | None, str | None, int | None]:
    """Extract salary from HTML and store raw values.

    Returns (salary_min, salary_max, salary_currency, salary_period, salary_eur).
    salary_min/max are the raw values in the original period and currency.
    salary_eur is the annualized EUR equivalent for index-based filtering;
    refreshed daily by refresh_currency_rates.py when exchange rates change.
    """
    if not html:
        return None, None, None, None, None
    sr = extract_salary_unified(html)
    if sr is None:
        return None, None, None, None, None

    # Store raw values in original period (always integers for DB)
    # Hourly values are stored in cents (e.g. $25.50/hr → 2550)
    sal_min = sr.min
    sal_max = sr.max

    # Annualize only for the EUR filter column
    if sr.period == "hourly":
        annual_min = round(sr.min / 100 * 2080)
    elif sr.period == "monthly":
        annual_min = sr.min * 12
    else:
        annual_min = sr.min

    to_eur = rates.get(sr.currency, 0)
    salary_eur = round(annual_min * to_eur) if to_eur > 0 else None

    return sal_min, sal_max, sr.currency, sr.period, salary_eur


def _extract_experience_fields(html: str | None) -> tuple[int | None, int | None]:
    """Extract experience requirement from HTML.

    Returns (experience_min, experience_max). max is None for open-ended ("5+ years").
    """
    if not html:
        return None, None
    result = extract_experience(html)
    if result is None:
        return None, None
    return result.min_years, result.max_years


async def _resolve_locations(
    resolver: LocationResolver,
    locations: list[str] | None,
    job_location_type: str | None,
    posting_language: str | None = None,
) -> tuple[list[int] | None, list[str] | None]:
    """Resolve locations to parallel arrays of (location_ids, location_types).

    Uses the in-memory core-locale cache first.  On cache misses (non-core
    locale names), batch-queries the DB and retries.
    """
    results = resolver.resolve(locations, job_location_type, posting_language)

    # DB fallback for non-core locale names (rare path).
    # Clear location_misses before retry — only misses from the final attempt matter.
    if await resolver.backfill_misses():
        resolver.drain_location_misses()
        results = resolver.resolve(locations, job_location_type, posting_language)

    if not results:
        return None, None

    # Build parallel arrays — only entries with location_ids
    loc_ids = []
    loc_types = []
    for r in results:
        if r.location_id is not None:
            loc_ids.append(r.location_id)
            loc_types.append(r.location_type)

    return loc_ids or None, loc_types or None


def _resolve_locations_sync(
    resolver: LocationResolver,
    locations: list[str] | None,
    job_location_type: str | None,
    posting_language: str | None = None,
) -> tuple[list[int] | None, list[str] | None]:
    """Synchronous location resolution (cache only, no DB backfill).

    Used by threaded batch processing.  Call ``resolver.backfill_misses()``
    after the thread completes to handle cache misses.
    """
    results = resolver.resolve(locations, job_location_type, posting_language)
    if not results:
        return None, None
    loc_ids = []
    loc_types = []
    for r in results:
        if r.location_id is not None:
            loc_ids.append(r.location_id)
            loc_types.append(r.location_type)
    return loc_ids or None, loc_types or None


# ── SQL Queries ──────────────────────────────────────────────────────

_FETCH_DUE_BOARDS = """
WITH ranked AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY throttle_key
           ORDER BY next_check_at, id
         ) AS domain_rank,
         next_check_at
  FROM job_board
  WHERE is_enabled = true
    AND board_status IN ('active', 'suspect')
    AND next_check_at <= now()
    AND (leased_until IS NULL OR leased_until < now())
),
picked AS (
  SELECT id
  FROM ranked
  ORDER BY domain_rank, next_check_at, id
  LIMIT $1
  FOR UPDATE SKIP LOCKED
)
UPDATE job_board b
SET lease_owner   = $2,
    leased_until  = now() + interval '10 minutes',
    last_checked_at = now(),
    next_check_at = now() + (check_interval_minutes || ' minutes')::interval
FROM picked
WHERE b.id = picked.id
RETURNING b.*
"""

_RELEASE_BOARD_LEASE = """
UPDATE job_board
SET lease_owner = NULL, leased_until = NULL
WHERE id = $1
"""

_RELEASE_BOARD_LEASES = """
UPDATE job_board
SET lease_owner = NULL, leased_until = NULL
WHERE id = ANY($1::uuid[])
"""

_RELEASE_POSTING_LEASES = """
UPDATE job_posting
SET lease_owner = NULL, leased_until = NULL
WHERE id = ANY($1::uuid[])
"""

_DIFF_URLS = """
WITH discovered AS (
  SELECT unnest($1::text[]) AS url
),
touched AS (
  UPDATE job_posting
  SET last_seen_at = now(), missing_count = 0
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.is_active = true
    AND job_posting.source_url = d.url
  RETURNING job_posting.id, job_posting.source_url, job_posting.description_r2_hash
),
relisted AS (
  UPDATE job_posting
  SET is_active = true, missing_count = 0,
      last_seen_at = now(),
      next_scrape_at = CASE WHEN $4::boolean THEN NULL ELSE now() END
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.is_active = false
    AND job_posting.source_url = d.url
  RETURNING job_posting.id, job_posting.source_url, job_posting.description_r2_hash
),
gone AS (
  UPDATE job_posting
  SET missing_count = missing_count + 1,
      is_active = CASE
          WHEN missing_count + 1 >= $3 THEN false
          ELSE is_active
      END,
      next_scrape_at = CASE
          WHEN missing_count + 1 >= $3 THEN NULL
          ELSE next_scrape_at
      END
  WHERE job_posting.board_id = $2
    AND job_posting.is_active = true
    AND job_posting.source_url NOT IN (SELECT url FROM discovered)
  RETURNING job_posting.id, job_posting.source_url
),
new_urls AS (
  SELECT d.url
  FROM discovered d
  LEFT JOIN job_posting jp
    ON jp.source_url = d.url AND jp.board_id = $2
  WHERE jp.id IS NULL
)
SELECT 'touched' AS action, id::text, source_url AS url, description_r2_hash FROM touched
UNION ALL
SELECT 'relisted' AS action, id::text, source_url AS url, description_r2_hash FROM relisted
UNION ALL
SELECT 'gone', id::text, source_url, NULL::bigint FROM gone
UNION ALL
SELECT 'new', NULL, url, NULL::bigint FROM new_urls
"""

# Delist threshold: API monitors are authoritative (1 miss = delist),
# URL-only monitors are fragile (2 misses before delist).
_DELIST_THRESHOLD_AUTHORITATIVE = 1
_DELIST_THRESHOLD_FRAGILE = 2

_DELIST_BOARD_POSTINGS = """
UPDATE job_posting
SET is_active = false, next_scrape_at = NULL
WHERE board_id = $1 AND is_active = true
"""

_RECORD_BOARD_GONE = """
UPDATE job_board
SET board_status = 'gone', gone_at = now(),
    is_enabled = false,
    lease_owner = NULL, leased_until = NULL,
    updated_at = now()
WHERE id = $1
"""

_RECORD_SUCCESS_NONEMPTY = """
UPDATE job_board
SET consecutive_failures = 0,
    last_error = NULL,
    last_success_at = now(),
    next_check_at = now() + (check_interval_minutes || ' minutes')::interval,
    empty_check_count = 0,
    board_status = 'active',
    last_non_empty_at = now(),
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE id = $1
"""

_RECORD_EMPTY_CHECK = """
UPDATE job_board
SET consecutive_failures = 0,
    last_error = NULL,
    last_success_at = now(),
    next_check_at = now() + (check_interval_minutes || ' minutes')::interval,
    empty_check_count = empty_check_count + 1,
    board_status = CASE
        WHEN last_non_empty_at IS NOT NULL AND empty_check_count + 1 >= 6
        THEN 'gone'
        WHEN last_non_empty_at IS NOT NULL AND empty_check_count + 1 >= 3
        THEN 'suspect'
        ELSE board_status
    END,
    gone_at = CASE
        WHEN last_non_empty_at IS NOT NULL AND empty_check_count + 1 >= 6
        THEN now()
        ELSE gone_at
    END,
    is_enabled = CASE
        WHEN last_non_empty_at IS NOT NULL AND empty_check_count + 1 >= 6
        THEN false
        ELSE is_enabled
    END,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE id = $1
RETURNING board_status
"""

_RECORD_FAILURE = """
UPDATE job_board
SET consecutive_failures = consecutive_failures + 1,
    last_error = $2,
    next_check_at = now() + LEAST(
        (5 * pow(2, consecutive_failures)) || ' minutes',
        '1440 minutes'
    )::interval,
    is_enabled = CASE WHEN consecutive_failures + 1 >= 5 THEN false ELSE is_enabled END,
    board_status = CASE WHEN consecutive_failures + 1 >= 5 THEN 'disabled' ELSE board_status END,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE id = $1
"""

_DIFF_BATCH = """
WITH discovered AS (
  SELECT unnest($1::text[]) AS url
),
touched AS (
  UPDATE job_posting
  SET last_seen_at = now(), missing_count = 0
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.is_active = true
    AND job_posting.source_url = d.url
  RETURNING job_posting.id, job_posting.source_url, job_posting.description_r2_hash
),
relisted AS (
  UPDATE job_posting
  SET is_active = true, missing_count = 0,
      last_seen_at = now(),
      next_scrape_at = CASE WHEN $3::boolean THEN NULL ELSE now() END
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.is_active = false
    AND job_posting.source_url = d.url
  RETURNING job_posting.id, job_posting.source_url, job_posting.description_r2_hash
),
new_urls AS (
  SELECT d.url
  FROM discovered d
  LEFT JOIN job_posting jp
    ON jp.source_url = d.url AND jp.board_id = $2
  WHERE jp.id IS NULL
)
SELECT 'touched' AS action, id::text, source_url AS url, description_r2_hash FROM touched
UNION ALL
SELECT 'relisted' AS action, id::text, source_url AS url, description_r2_hash FROM relisted
UNION ALL
SELECT 'new', NULL, url, NULL::bigint FROM new_urls
"""

_MARK_GONE = """
WITH discovered AS (
  SELECT unnest($1::text[]) AS url
)
UPDATE job_posting
SET missing_count = missing_count + 1,
    is_active = CASE
        WHEN missing_count + 1 >= $3 THEN false
        ELSE is_active
    END,
    next_scrape_at = CASE
        WHEN missing_count + 1 >= $3 THEN NULL
        ELSE next_scrape_at
    END
WHERE job_posting.board_id = $2
  AND job_posting.is_active = true
  AND job_posting.source_url NOT IN (SELECT url FROM discovered)
RETURNING job_posting.id, job_posting.source_url
"""

_EXTEND_BOARD_LEASE = """
UPDATE job_board
SET leased_until = now() + interval '10 minutes'
WHERE id = $1
"""

_EXTEND_SCRAPE_LEASE = """
UPDATE job_posting
SET leased_until = now() + interval '10 minutes'
WHERE id = $1
"""

_INSERT_RICH_JOB = """
INSERT INTO job_posting
    (company_id, board_id,
     employment_type, source_url,
     first_seen_at, last_seen_at,
     is_active, titles, locales,
     location_ids, location_types,
     salary_min, salary_max, salary_currency, salary_period, salary_eur,
     experience_min, experience_max, technology_ids,
     occupation_id, seniority_id)
VALUES ($1, $2, $3, $4,
        now(), now(),
        true, $5, $6,
        $7, $8,
        $9, $10, $11, $12, $13,
        $14, $15, $16,
        $17, $18)
RETURNING id
"""

_INSERT_RICH_JOB_ENRICH = """
INSERT INTO job_posting
    (company_id, board_id,
     employment_type, source_url,
     first_seen_at, last_seen_at, next_scrape_at,
     is_active, titles, locales,
     location_ids, location_types,
     salary_min, salary_max, salary_currency, salary_period, salary_eur,
     experience_min, experience_max, technology_ids,
     occupation_id, seniority_id)
VALUES ($1, $2, $3, $4,
        now(), now(), now(),
        true, $5, $6,
        $7, $8,
        $9, $10, $11, $12, $13,
        $14, $15, $16,
        $17, $18)
RETURNING id
"""

_CREATE_RICH_UPDATES_TEMP = """
CREATE TEMP TABLE _rich_updates (
    id uuid,
    employment_type text,
    titles text[], locales text[],
    location_ids integer[], location_types text[],
    salary_min integer, salary_max integer,
    salary_currency text, salary_period text, salary_eur integer,
    experience_min integer, experience_max integer,
    technology_ids integer[],
    occupation_id integer, seniority_id integer
) ON COMMIT DROP
"""

_BATCH_UPDATE_RICH_CONTENT = """
UPDATE job_posting AS jp
SET employment_type = u.employment_type,
    titles = u.titles, locales = u.locales,
    location_ids = u.location_ids, location_types = u.location_types,
    salary_min = u.salary_min, salary_max = u.salary_max,
    salary_currency = u.salary_currency, salary_period = u.salary_period,
    salary_eur = u.salary_eur,
    experience_min = u.experience_min, experience_max = u.experience_max,
    technology_ids = COALESCE(u.technology_ids, jp.technology_ids),
    occupation_id = COALESCE(u.occupation_id, jp.occupation_id),
    seniority_id = COALESCE(u.seniority_id, jp.seniority_id)
FROM _rich_updates u
WHERE jp.id = u.id
"""

_INSERT_URL_ONLY_JOBS = """
INSERT INTO job_posting (company_id, board_id, source_url,
                         first_seen_at, last_seen_at, next_scrape_at,
                         is_active, titles, locales)
SELECT $1, $2, u.url, now(), now(), now(),
       true, '{}', '{}'
FROM unnest($3::text[]) AS u(url)
RETURNING id, source_url
"""

_UPDATE_METADATA = """
UPDATE job_board
SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
    updated_at = now()
WHERE id = $1
"""

_UPDATE_JOB_CONTENT = """
UPDATE job_posting
SET employment_type = $2,
    titles = $3, locales = $4,
    location_ids = $5, location_types = $6,
    description_pending = $7,
    r2_pending_meta = $8::jsonb,
    technology_ids = COALESCE($9, technology_ids),
    salary_min = $10, salary_max = $11,
    salary_currency = $12, salary_period = $13, salary_eur = $14,
    experience_min = $15, experience_max = $16,
    occupation_id = COALESCE($17, occupation_id),
    seniority_id = COALESCE($18, seniority_id),
    to_be_enriched = true
WHERE id = $1
"""

_STAGE_R2_PENDING = """
UPDATE job_posting
SET description_pending = COALESCE($2, description_pending),
    r2_pending_meta = $3::jsonb
WHERE id = $1::uuid
"""

_UPSERT_LOCATION_MISSES = """
INSERT INTO taxonomy_miss (taxonomy, raw_value, sample_value)
SELECT 'location', * FROM unnest($1::text[], $2::text[])
ON CONFLICT (taxonomy, raw_value) DO UPDATE SET
    hit_count = taxonomy_miss.hit_count + 1,
    last_seen_at = now()
WHERE taxonomy_miss.status = 'pending'
"""

_FETCH_DUE_JOB_POSTINGS = """
WITH candidates AS (
    SELECT id, split_part(split_part(source_url, '://', 2), '/', 1) AS domain,
           next_scrape_at,
           (description_r2_hash IS NULL
            AND description_pending IS NULL)::int AS needs_initial_scrape
    FROM job_posting
    WHERE is_active = true
      AND next_scrape_at IS NOT NULL
      AND next_scrape_at <= now()
      AND (leased_until IS NULL OR leased_until < now())
    FOR UPDATE SKIP LOCKED
),
ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY domain
               ORDER BY needs_initial_scrape DESC, next_scrape_at
           ) AS domain_rank,
           needs_initial_scrape,
           next_scrape_at
    FROM candidates
)
UPDATE job_posting
SET leased_until = now() + interval '10 minutes',
    lease_owner = $2
FROM (
    SELECT id AS rid
    FROM ranked
    ORDER BY domain_rank, needs_initial_scrape DESC, next_scrape_at
    LIMIT $1
) pick
WHERE job_posting.id = pick.rid
RETURNING job_posting.id, source_url, board_id,
          split_part(split_part(source_url, '://', 2), '/', 1) AS scrape_domain,
          description_r2_hash
"""

_RECORD_SCRAPE_SUCCESS = """
UPDATE job_posting jp
SET scrape_failures  = 0,
    last_scraped_at  = now(),
    next_scrape_at   = CASE
        WHEN jp.is_active
        THEN now() + (COALESCE(
            (SELECT scrape_interval_hours FROM job_board WHERE id = jp.board_id),
            24
        ) || ' hours')::interval
        ELSE NULL
    END,
    leased_until     = NULL,
    lease_owner      = NULL
WHERE jp.id = $1
"""

_RECORD_SCRAPE_FAILURE = """
UPDATE job_posting
SET scrape_failures   = scrape_failures + 1,
    last_scraped_at   = now(),
    next_scrape_at    = CASE
        WHEN scrape_failures + 1 >= 3 THEN NULL
        ELSE now() + (30 * pow(2, scrape_failures)) * interval '1 minute'
    END,
    leased_until = NULL,
    lease_owner = NULL
WHERE id = $1
"""

_CLEAR_SCRAPE_FOR_RICH = """
UPDATE job_posting
SET next_scrape_at = NULL, leased_until = NULL, lease_owner = NULL
WHERE id = ANY($1::uuid[])
"""

_UPDATE_ENRICH_CONTENT = """
UPDATE job_posting
SET employment_type = COALESCE($2, employment_type),
    titles = COALESCE($3, titles),
    locales = COALESCE($4, locales),
    location_ids = COALESCE($5, location_ids),
    location_types = COALESCE($6, location_types),
    description_pending = COALESCE($7, description_pending),
    r2_pending_meta = COALESCE($8::jsonb, r2_pending_meta),
    technology_ids = COALESCE($9, technology_ids),
    salary_min = COALESCE($10, salary_min),
    salary_max = COALESCE($11, salary_max),
    salary_currency = COALESCE($12, salary_currency),
    salary_period = COALESCE($13, salary_period),
    salary_eur = COALESCE($14, salary_eur),
    experience_min = COALESCE($15, experience_min),
    experience_max = COALESCE($16, experience_max),
    occupation_id = COALESCE($17, occupation_id),
    seniority_id = COALESCE($18, seniority_id),
    to_be_enriched = CASE
        WHEN $7 IS NOT NULL THEN true
        ELSE to_be_enriched
    END
WHERE id = $1
"""

_FETCH_POSTING_FOR_ENRICH = """
SELECT titles, locales, location_ids, location_types
FROM job_posting
WHERE id = $1
"""

_FETCH_BOARD_SCRAPERS = """
SELECT id::text AS id, metadata, crawler_type
FROM job_board
WHERE id::text = ANY($1::text[])
"""


# ── Helpers ──────────────────────────────────────────────────────────


def _jsonb(val: dict | None) -> str | None:
    return json.dumps(val) if val is not None else None


def _error_message(exc: Exception, max_len: int = 500) -> str:
    """Return a non-empty, bounded error message for logs/DB fields."""
    text = str(exc).strip()
    message = text or type(exc).__name__
    if len(message) > max_len:
        return message[:max_len]
    return message


def _coerce_text(val: object | None) -> str | None:
    """Normalize scalars/lists to a single text value for Postgres text columns."""
    if val is None:
        return None
    if isinstance(val, str):
        stripped = val.strip()
        return stripped or None
    if isinstance(val, (list, tuple, set)):
        parts: list[str] = []
        for item in val:
            part = _coerce_text(item)
            if part:
                parts.append(part)
        if not parts:
            return None
        return ", ".join(dict.fromkeys(parts))
    if isinstance(val, dict):
        return json.dumps(val, sort_keys=True)
    return str(val)


def _coerce_locations(val: object | None) -> list[str] | None:
    """Normalize values to a Postgres text[] payload."""
    if val is None:
        return None
    if isinstance(val, str):
        text = val.strip()
        return [text] if text else None
    if isinstance(val, (list, tuple, set)):
        parts: list[str] = []
        for item in val:
            part = _coerce_text(item)
            if part:
                parts.append(part)
        if not parts:
            return None
        return list(dict.fromkeys(parts))
    text = _coerce_text(val)
    return [text] if text else None


def _coerce_datetime(val: object | None) -> datetime | None:
    """Normalize common monitor/scraper timestamp formats for timestamptz columns."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=UTC)
    if isinstance(val, date):
        return datetime.combine(val, time.min, tzinfo=UTC)
    if isinstance(val, (int, float)):
        with contextlib.suppress(Exception):
            return datetime.fromtimestamp(val, tz=UTC)
        return None
    if not isinstance(val, str):
        return None

    raw = val.strip()
    if not raw:
        return None

    candidates = [raw]
    if raw.endswith("Z"):
        candidates.append(raw[:-1] + "+00:00")
    if raw.endswith(" UTC"):
        candidates.append(raw[:-4] + "+00:00")

    for candidate in candidates:
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    with contextlib.suppress(ValueError):
        parsed_date = date.fromisoformat(raw)
        return datetime.combine(parsed_date, time.min, tzinfo=UTC)

    with contextlib.suppress(Exception):
        parsed = parsedate_to_datetime(raw)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    return None


def _parse_metadata(raw: object) -> dict:
    """Normalize job_board.metadata values from asyncpg to plain dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
    return {}


def _parse_update_count(result: object) -> int:
    """Extract rowcount from asyncpg command status (e.g. ``UPDATE 1``)."""
    if not isinstance(result, str):
        return 0
    parts = result.rsplit(" ", 1)
    if len(parts) != 2:
        return 0
    with contextlib.suppress(ValueError):
        return int(parts[1])
    return 0


def _build_titles(title: str | None, localizations: dict | None) -> list[str]:
    """Build titles array from primary title + localizations."""
    titles: list[str] = []
    if title:
        titles.append(title)
    if localizations and isinstance(localizations, dict):
        for loc_data in localizations.values():
            if isinstance(loc_data, dict):
                loc_title = loc_data.get("title")
                if loc_title and loc_title not in titles:
                    titles.append(loc_title)
    return titles


def _build_locales(
    language: str | None,
    localizations: dict | None,
    *,
    detected_languages: list[str] | None = None,
) -> list[str]:
    """Build locales array from primary language + localization keys + detected."""
    locales: list[str] = []
    primary = language or "en"
    locales.append(primary)
    if localizations and isinstance(localizations, dict):
        for locale in localizations:
            if locale not in locales:
                locales.append(locale)
    if detected_languages:
        for lang in detected_languages:
            if lang not in locales:
                locales.append(lang)
    return locales


def _stable_date(val: object | None) -> str | None:
    """Coerce a date to a stable ISO 8601 date-only string (YYYY-MM-DD).

    Strips time components and timezone offsets so the hash doesn't churn
    when the source alternates between date-only and datetime formats.
    """
    dt = _coerce_datetime(val)
    if dt is None:
        return None
    return dt.date().isoformat()


def _deep_sort(obj: object) -> object:
    """Recursively sort dicts by key and lists of strings for stable JSON."""
    if isinstance(obj, dict):
        return {k: _deep_sort(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_deep_sort(item) for item in obj]
    return obj


# Fields that are volatile across cycles and should be excluded from the
# R2 content hash to avoid spurious re-uploads.  They are still stored in
# extras (visible in history.json) but changes to them alone don't trigger
# a write.  Checked at top-level extras AND inside nested metadata dict.
_HASH_VOLATILE_FIELDS = frozenset(
    {
        "valid_through",
        "expiration_date",
    }
)


def _build_r2_extras(
    *,
    title: str | None,
    locations: list[str] | None,
    extras: dict | None,
    metadata: dict | None,
    date_posted: object | None,
    base_salary: dict | None,
    employment_type: str | None,
    job_location_type: str | None,
) -> dict:
    """Build the merged extras dict for R2 upload."""
    merged: dict = {}
    if extras and isinstance(extras, dict):
        merged.update(extras)
    # Explicit fields overwrite anything from extras
    if title is not None:
        merged["title"] = title
    if locations:
        merged["locations"] = locations
    if metadata and isinstance(metadata, dict):
        merged["metadata"] = metadata
    if date_posted is not None:
        stable = _stable_date(date_posted)
        if stable is not None:
            merged["date_posted"] = stable
    if base_salary is not None:
        merged["base_salary"] = base_salary
    if employment_type is not None:
        merged["raw_employment_type"] = employment_type
    if job_location_type is not None:
        merged["raw_job_location_type"] = job_location_type
    return merged


def _compute_r2_hash(description: str | None, merged_extras: dict) -> int:
    """Compute a combined hash of all R2-bound content.

    Uses deep-sorted JSON serialization so nested dicts (metadata,
    base_salary, extras) produce a stable hash regardless of key order.
    Excludes volatile fields (valid_through, expiration_date) that change
    frequently but don't represent meaningful content updates.
    """
    parts = description or ""
    if merged_extras:
        hashable = {}
        for k, v in merged_extras.items():
            if k in _HASH_VOLATILE_FIELDS:
                continue
            if k == "metadata" and isinstance(v, dict):
                v = {mk: mv for mk, mv in v.items() if mk not in _HASH_VOLATILE_FIELDS}
            hashable[k] = v
        parts += "\0" + json.dumps(_deep_sort(hashable), sort_keys=True, ensure_ascii=False)
    return content_hash(parts)


def _serialize_localizations(
    localizations: dict | None,
    primary_locale: str,
) -> dict[str, str] | None:
    """Flatten localizations to ``{locale: html_string}`` for JSON storage."""
    if not localizations or not isinstance(localizations, dict):
        return None
    result: dict[str, str] = {}
    for loc_locale, loc_data in localizations.items():
        if loc_locale == primary_locale:
            continue
        if isinstance(loc_data, dict):
            desc = loc_data.get("description")
        elif isinstance(loc_data, str):
            desc = loc_data
        else:
            continue
        if desc:
            result[loc_locale] = desc
    return result or None


def _stage_r2_pending(
    *,
    title: str | None,
    description: str | None,
    language: str | None,
    locations: list[str] | None,
    localizations: dict | None,
    extras: dict | None,
    metadata: dict | None,
    date_posted: object | None,
    base_salary: dict | None,
    employment_type: str | None,
    job_location_type: str | None,
    current_hash: int | None = None,
    source: str = "monitor",
    tech_ids: list[int] | None = None,
) -> tuple[str | None, str | None, int] | None:
    """Compute R2 pending data without any network I/O.

    Returns ``(description_pending, r2_pending_meta_json, new_hash)``
    or ``None`` if nothing changed (hash match) or no description.
    """
    if not description:
        return None

    locale = language or "en"
    merged = _build_r2_extras(
        title=title,
        locations=locations,
        extras=extras,
        metadata=metadata,
        date_posted=date_posted,
        base_salary=base_salary,
        employment_type=employment_type,
        job_location_type=job_location_type,
    )
    new_hash = _compute_r2_hash(description, merged)

    if current_hash is not None and current_hash == new_hash:
        return None

    meta = {
        "locale": locale,
        "extras": merged,
        "tech_ids": tech_ids,
        "localizations": _serialize_localizations(localizations, locale),
        "source": source,
        "retry_count": 0,
        "new_hash": new_hash,
    }
    return (description, json.dumps(meta), new_hash)


# ── R2 queue cap ─────────────────────────────────────────────────────

_r2_queue_depth: int | None = None
_r2_queue_depth_ts: float = 0

_COUNT_R2_PENDING = """
SELECT count(*) FROM job_posting
WHERE description_pending IS NOT NULL
   OR r2_pending_meta IS NOT NULL
"""


async def _get_r2_queue_depth(pool) -> int:
    """Return cached R2 queue depth (refreshed every 30s)."""
    global _r2_queue_depth, _r2_queue_depth_ts
    now = monotonic()
    if _r2_queue_depth is None or now - _r2_queue_depth_ts > 30:
        _r2_queue_depth = await pool.fetchval(_COUNT_R2_PENDING) or 0
        _r2_queue_depth_ts = now
    return _r2_queue_depth


def _board_has_enrich(metadata: dict) -> list[str] | None:
    """Extract the ``enrich`` list from ``metadata["scraper_config"]``, or None."""
    sc = metadata.get("scraper_config")
    if not isinstance(sc, dict):
        return None
    enrich = sc.get("enrich")
    if isinstance(enrich, list) and enrich:
        return enrich
    return None


@dataclass
class BoardScraperConfig:
    """Scraper settings for a board (fallback chain lives inside scraper_config)."""

    scraper_type: str
    scraper_config: dict | None
    ssl_verify: bool = True


@dataclass
class _BoardScraperInfo:
    """Scraper info plus whether the board is a rich monitor (no scraping needed)."""

    scrapers: dict[str, BoardScraperConfig]
    rich_board_ids: set[str]  # boards from rich monitors with no explicit scraper


async def _load_board_scrapers(
    pool: asyncpg.Pool,
    board_ids: set[str],
) -> _BoardScraperInfo:
    """Load scraper type/config by board id from job_board metadata."""
    if not board_ids:
        return _BoardScraperInfo(scrapers={}, rich_board_ids=set())

    rows = await pool.fetch(_FETCH_BOARD_SCRAPERS, list(board_ids))
    resolved: dict[str, BoardScraperConfig] = {}
    rich_board_ids: set[str] = set()

    for row in rows:
        board_id = row["id"]
        metadata = _parse_metadata(row["metadata"])
        crawler_type = row["crawler_type"]
        explicit_scraper = metadata.get("scraper_type")

        enrich_fields = _board_has_enrich(metadata)

        # Determine scraper: explicit > auto-configured > default (json-ld)
        if not explicit_scraper:
            # Check if monitor auto-configures a scraper
            from src.workspace._compat import auto_scraper_type

            auto = auto_scraper_type(crawler_type, metadata)
            if auto and auto[0] == "skip":
                if enrich_fields:
                    # Enrich boards need a scraper — use json-ld as default
                    scraper_type = "json-ld"
                    auto_config = None
                else:
                    rich_board_ids.add(board_id)
                    continue
            else:
                scraper_type = auto[0] if auto else "json-ld"
                auto_config = auto[1] if auto else None
        elif explicit_scraper == "skip":
            if enrich_fields:
                scraper_type = "json-ld"
                auto_config = None
            else:
                rich_board_ids.add(board_id)
                continue
        else:
            scraper_type = explicit_scraper
            auto_config = None
        scraper_config = metadata.get("scraper_config")
        if not isinstance(scraper_config, dict):
            scraper_config = auto_config

        try:
            get_scraper(scraper_type)
        except Exception:
            log.warning(
                "batch.scrape.invalid_scraper_type",
                board_id=board_id,
                scraper_type=scraper_type,
            )
            scraper_type = "json-ld"
            scraper_config = None

        resolved[board_id] = BoardScraperConfig(
            scraper_type=scraper_type,
            scraper_config=scraper_config,
            ssl_verify=metadata.get("ssl_verify", True),
        )

    return _BoardScraperInfo(scrapers=resolved, rich_board_ids=rich_board_ids)


def _throttle_key(board: asyncpg.Record) -> str:
    """Return the rate-limit domain for a board.

    API monitors share an API host per type (e.g. all greenhouse boards
    hit boards-api.greenhouse.io), so crawler_type is the key.
    URL-only monitors each hit their own company domain.
    """
    crawler_type = board["crawler_type"]
    if crawler_type in _API_MONITOR_TYPES:
        return crawler_type
    return urlparse(board["board_url"]).hostname or board["board_url"]


@dataclass
class ScrapeItem:
    """A job posting claimed from Postgres for scraping."""

    job_posting_id: str
    url: str
    board_id: str = ""
    description_r2_hash: int | None = None


_SLOW_MONITOR_SECONDS = 30.0
_SLOW_SCRAPE_SECONDS = 15.0


@dataclass
class BatchResult:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    duration_s: float = 0.0
    slow_items: int = 0
    item_durations: list[float] = field(default_factory=list)


class DeadlineExtender:
    """Shared between work item and pool to extend the timeout deadline.

    The streaming processor calls ``pulse()`` after each batch.  The pool
    loop checks the event to decide whether to renew the deadline or
    declare a true timeout.
    """

    def __init__(self):
        self._event = asyncio.Event()

    def pulse(self):
        """Signal that the work item is still making progress."""
        self._event.set()


@dataclass
class WorkItem:
    """A single unit of work for the continuous worker pool."""

    domain: str
    kind: str  # "monitor" | "scrape"
    run: Callable[[], Awaitable[tuple[bool, float]]]
    id: str = ""  # board ID or posting ID — used for lease release
    on_timeout: Callable[[], Awaitable[None]] | None = None
    deadline_extender: DeadlineExtender | None = None
    needs_browser: bool = False


# ── Claim Queries (Worker Pool) ──────────────────────────────────────

_CLAIM_MONITORS = """
WITH ranked AS (
  SELECT id,
         (last_success_at IS NULL)::int AS is_first_crawl,
         row_number() OVER (
           PARTITION BY throttle_key
           ORDER BY (last_success_at IS NULL) DESC, next_check_at, id
         ) AS domain_rank
  FROM job_board
  WHERE is_enabled = true
    AND board_status IN ('active', 'suspect')
    AND next_check_at <= now()
    AND (leased_until IS NULL OR leased_until < now())
    AND throttle_key != ALL($3::text[])
    AND monitor_needs_browser = $4
),
picked AS (
  SELECT id
  FROM ranked
  ORDER BY domain_rank, is_first_crawl DESC, id
  LIMIT $1
  FOR UPDATE SKIP LOCKED
)
UPDATE job_board b
SET lease_owner = $2, leased_until = now() + interval '10 minutes',
    last_checked_at = now(),
    next_check_at = now() + (check_interval_minutes || ' minutes')::interval
FROM picked WHERE b.id = picked.id
RETURNING b.*
"""

_CLAIM_SCRAPES = """
WITH candidates AS (
    SELECT p.id,
           split_part(split_part(p.source_url, '://', 2), '/', 1) AS domain,
           p.next_scrape_at,
           (p.titles = '{}')::int AS needs_initial_scrape
    FROM job_posting p
    JOIN job_board b ON b.id = p.board_id
    WHERE p.is_active = true
      AND p.next_scrape_at IS NOT NULL
      AND p.next_scrape_at <= now()
      AND (p.leased_until IS NULL OR p.leased_until < now())
      AND split_part(split_part(p.source_url, '://', 2), '/', 1) != ALL($3::text[])
      AND b.scraper_needs_browser = $4
    FOR UPDATE OF p SKIP LOCKED
),
ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY domain
               ORDER BY needs_initial_scrape DESC, next_scrape_at
           ) AS domain_rank,
           needs_initial_scrape, next_scrape_at
    FROM candidates
)
UPDATE job_posting
SET leased_until = now() + interval '10 minutes',
    lease_owner = $2
FROM (
    SELECT id AS rid
    FROM ranked
    ORDER BY domain_rank, needs_initial_scrape DESC, next_scrape_at
    LIMIT $1
) pick
WHERE job_posting.id = pick.rid
RETURNING job_posting.id, source_url, board_id,
          split_part(split_part(source_url, '://', 2), '/', 1) AS scrape_domain,
          description_r2_hash
"""


async def claim_monitor_work(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int,
    worker_id: str,
    exclude_domains: list[str] | None = None,
    *,
    browser: bool = False,
) -> list[WorkItem]:
    """Claim due boards (interleaved across domains) and return WorkItems.

    When *browser* is True, only boards with ``monitor_needs_browser = true``
    are claimed; when False, only non-browser boards.
    """
    if limit <= 0:
        return []

    rows = await pool.fetch(_CLAIM_MONITORS, limit, worker_id, exclude_domains or [], browser)
    items: list[WorkItem] = []
    for board in rows:
        domain = board["throttle_key"]
        board_id = str(board["id"])
        on_timeout = functools.partial(_record_timeout, board_id, pool)
        stream_fn = get_stream_fn(board["crawler_type"])
        if stream_fn is not None:
            extender = DeadlineExtender()
            items.append(
                WorkItem(
                    domain=domain,
                    kind="monitor",
                    run=functools.partial(
                        _process_one_board_streaming,
                        board,
                        pool,
                        http,
                        extender,
                    ),
                    id=board_id,
                    on_timeout=on_timeout,
                    deadline_extender=extender,
                    needs_browser=browser,
                )
            )
        else:
            items.append(
                WorkItem(
                    domain=domain,
                    kind="monitor",
                    run=functools.partial(_process_one_board, board, pool, http),
                    id=board_id,
                    on_timeout=on_timeout,
                    needs_browser=browser,
                )
            )
    return items


async def claim_scrape_work(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int,
    worker_id: str,
    exclude_domains: list[str] | None = None,
    *,
    browser: bool = False,
) -> list[WorkItem]:
    """Claim due job postings (interleaved across domains) and return WorkItems.

    When *browser* is True, only postings whose board has
    ``scraper_needs_browser = true`` are claimed; when False, only non-browser.
    """
    if limit <= 0:
        return []

    rows = await pool.fetch(_CLAIM_SCRAPES, limit, worker_id, exclude_domains or [], browser)
    if not rows:
        return []

    board_ids = {str(row["board_id"]) for row in rows if row["board_id"]}
    info = await _load_board_scrapers(pool, board_ids)

    # Clear next_scrape_at for postings from rich monitors (they shouldn't be scraped)
    rich_posting_ids = [
        str(row["id"]) for row in rows if str(row["board_id"]) in info.rich_board_ids
    ]
    if rich_posting_ids:
        await pool.execute(_CLEAR_SCRAPE_FOR_RICH, rich_posting_ids)
        log.info("batch.scrape.cleared_rich", count=len(rich_posting_ids))

    items: list[WorkItem] = []
    for row in rows:
        domain = row["scrape_domain"] or urlparse(row["source_url"]).hostname or "unknown"
        board_id = str(row["board_id"]) if row["board_id"] else ""

        # Skip rich-monitor postings
        if board_id in info.rich_board_ids:
            continue

        scraper_type = "json-ld"
        scraper_config: dict | None = None
        ssl_verify = True
        if board_id and board_id in info.scrapers:
            cfg = info.scrapers[board_id]
            scraper_type = cfg.scraper_type
            scraper_config = cfg.scraper_config
            ssl_verify = cfg.ssl_verify

        r2_hash = row["description_r2_hash"]
        item = ScrapeItem(
            job_posting_id=str(row["id"]),
            url=row["source_url"],
            board_id=board_id,
            description_r2_hash=int(r2_hash) if r2_hash is not None else None,
        )

        if ssl_verify:
            run_fn = functools.partial(
                _process_one_scrape,
                item,
                pool,
                http,
                scraper_type,
                scraper_config,
            )
        else:
            run_fn = functools.partial(
                _process_one_scrape_insecure,
                item,
                pool,
                scraper_type,
                scraper_config,
            )

        items.append(
            WorkItem(
                domain=domain,
                kind="scrape",
                run=run_fn,
                id=str(row["id"]),
                needs_browser=browser,
            )
        )
    return items


async def release_rejected(pool: asyncpg.Pool, items: list[WorkItem]) -> None:
    """Release leases for WorkItems that were not accepted by the pool."""
    board_ids = [i.id for i in items if i.kind == "monitor" and i.id]
    posting_ids = [i.id for i in items if i.kind == "scrape" and i.id]
    if board_ids:
        await pool.execute(_RELEASE_BOARD_LEASES, board_ids)
        log.info("batch.release_rejected.boards", count=len(board_ids))
    if posting_ids:
        await pool.execute(_RELEASE_POSTING_LEASES, posting_ids)
        log.info("batch.release_rejected.postings", count=len(posting_ids))


# ── Monitor Batch ────────────────────────────────────────────────────


async def _record_timeout(board_id: str, pool: asyncpg.Pool) -> None:
    """Record a timeout failure for a board (called from WorkItem.on_timeout)."""
    with contextlib.suppress(Exception):
        async with pool.acquire() as conn:
            await conn.execute(_RECORD_FAILURE, board_id, "WorkerPool timeout")
            await conn.execute(_RELEASE_BOARD_LEASE, board_id)


async def _process_one_board_streaming(
    board: asyncpg.Record,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    extender: object,
) -> tuple[bool, float]:
    """Run a streaming monitor cycle for a single board. Returns (success, duration_s).

    Yields batches from the monitor, processing each incrementally:
    - Extends the DB lease and WorkerPool deadline on each batch
    - Runs _DIFF_BATCH (new/touched/relisted only) per batch
    - Fires R2 uploads as background tasks overlapping with discovery
    - Runs _MARK_GONE once after all batches complete
    """
    board_id = str(board["id"])
    company_id = str(board["company_id"])
    board_url = board["board_url"]
    crawler_type = board["crawler_type"]

    board_log = log.bind(board_id=board_id, board_url=board_url, crawler_type=crawler_type)
    t0 = monotonic()

    try:
        metadata = board["metadata"] if board["metadata"] else {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        enrich_fields = _board_has_enrich(metadata)

        # Pre-load lookup tables once
        loc_resolver = await _get_location_resolver(pool)
        rates = await _get_currency_rates(pool)
        tech_id_map = await _get_technology_ids(pool)
        occ_ids = await _get_occupation_ids(pool)
        sen_ids = await _get_seniority_ids(pool)

        all_urls: set[str] = set()
        total_new = 0
        total_relisted = 0
        batch_count = 0

        async for result in monitor_one_stream(board_url, crawler_type, metadata, http):
            batch_count += 1
            all_urls.update(result.urls)
            is_rich = result.jobs_by_url is not None

            # Pulse heartbeat + extend DB lease (shielded to avoid
            # destroying the pool connection on task cancellation)
            extender.pulse()
            with contextlib.suppress(Exception):
                await asyncio.shield(pool.execute(_EXTEND_BOARD_LEASE, board_id))

            if not result.urls:
                continue

            async with pool.acquire() as conn, conn.transaction():
                is_rich_no_scrape = is_rich and not enrich_fields
                rows = await conn.fetch(
                    _DIFF_BATCH,
                    list(result.urls),
                    board_id,
                    is_rich_no_scrape,
                )

                new_urls: list[str] = []
                relisted: list[dict] = []
                touched: list[dict] = []

                for row in rows:
                    action = row["action"]
                    if action == "new":
                        new_urls.append(row["url"])
                    elif action == "relisted":
                        r2h = row["description_r2_hash"]
                        relisted.append(
                            {
                                "id": row["id"],
                                "url": row["url"],
                                "r2_hash": int(r2h) if r2h is not None else None,
                            }
                        )
                    elif action == "touched":
                        r2h = row["description_r2_hash"]
                        touched.append(
                            {
                                "id": row["id"],
                                "url": row["url"],
                                "r2_hash": int(r2h) if r2h is not None else None,
                            }
                        )

                total_new += len(new_urls)
                total_relisted += len(relisted)

                if result.jobs_by_url:
                    new_jobs = [result.jobs_by_url[u] for u in new_urls if u in result.jobs_by_url]

                    if new_jobs:
                        # CPU-heavy per-job processing — run off the event loop
                        def _process_new_jobs_cpu(jobs):
                            """Pure CPU: normalize, detect language, resolve, extract."""
                            records = []
                            r2_staging = []
                            for j in jobs:
                                j.description = normalize_description_html(j.description)
                                enrich_description(j)
                                if not j.language and j.description:
                                    j.language = detect_language(j.description)

                                loc_ids_r, loc_types_r = _resolve_locations_sync(
                                    loc_resolver,
                                    _coerce_locations(j.locations),
                                    _coerce_text(j.job_location_type),
                                    _coerce_text(j.language),
                                )
                                desc_text = _coerce_text(j.description)
                                s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(
                                    desc_text, rates
                                )
                                exp_min, exp_max = _extract_experience_fields(desc_text)
                                t_ids = _resolve_technology_ids(desc_text, tech_id_map)
                                title_text = _coerce_text(j.title)
                                all_titles = _build_titles(title_text, j.localizations)
                                occ_id, sen_id = _resolve_occupation_seniority(
                                    all_titles, occ_ids, sen_ids
                                )
                                detected_langs = (
                                    detect_all_languages(j.description) if j.description else []
                                )
                                records.append(
                                    (
                                        company_id,
                                        board_id,
                                        normalize_employment_type(_coerce_text(j.employment_type)),
                                        j.url,
                                        all_titles,
                                        _build_locales(
                                            _coerce_text(j.language),
                                            j.localizations,
                                            detected_languages=detected_langs,
                                        ),
                                        loc_ids_r,
                                        loc_types_r,
                                        s_min,
                                        s_max,
                                        s_cur,
                                        s_per,
                                        s_eur,
                                        exp_min,
                                        exp_max,
                                        t_ids,
                                        occ_id,
                                        sen_id,
                                    )
                                )
                                r2_staging.append((j, t_ids))
                            return records, r2_staging

                        records, r2_staging = await asyncio.to_thread(
                            _process_new_jobs_cpu, new_jobs
                        )

                        # DB backfill for location cache misses (rare)
                        if await loc_resolver.backfill_misses():
                            loc_resolver.drain_location_misses()

                        # Batch insert all new jobs
                        insert_sql = _INSERT_RICH_JOB_ENRICH if enrich_fields else _INSERT_RICH_JOB
                        inserted_ids = []
                        for rec in records:
                            row = await conn.fetchrow(insert_sql, *rec)
                            if row:
                                inserted_ids.append(str(row["id"]))

                        # Batch R2 staging for inserted jobs
                        for (j, t_ids), posting_id in zip(r2_staging, inserted_ids, strict=False):
                            staged = _stage_r2_pending(
                                title=_coerce_text(j.title),
                                description=_coerce_text(j.description),
                                language=_coerce_text(j.language),
                                locations=_coerce_locations(j.locations),
                                localizations=j.localizations,
                                extras=j.extras,
                                metadata=j.metadata,
                                date_posted=j.date_posted,
                                base_salary=j.base_salary,
                                employment_type=_coerce_text(j.employment_type),
                                job_location_type=_coerce_text(j.job_location_type),
                                source="monitor",
                                tech_ids=t_ids,
                            )
                            if staged:
                                await conn.execute(
                                    _STAGE_R2_PENDING,
                                    posting_id,
                                    staged[0],
                                    staged[1],
                                )

                    # Update content for relisted and touched
                    update_triples = [
                        (item["id"], result.jobs_by_url[item["url"]], item.get("r2_hash"))
                        for item in relisted + touched
                        if item["url"] in result.jobs_by_url
                    ]
                    if update_triples:
                        for _, j, _ in update_triples:
                            j.description = normalize_description_html(j.description)
                            enrich_description(j)
                            if not j.language and j.description:
                                j.language = detect_language(j.description)

                        await conn.execute(_CREATE_RICH_UPDATES_TEMP)
                        records = []
                        for pid, j, _ in update_triples:
                            loc_ids, loc_types = await _resolve_locations(
                                loc_resolver,
                                _coerce_locations(j.locations),
                                _coerce_text(j.job_location_type),
                                _coerce_text(j.language),
                            )
                            desc_text = _coerce_text(j.description)
                            s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(
                                desc_text, rates
                            )
                            exp_min, exp_max = _extract_experience_fields(desc_text)
                            t_ids = _resolve_technology_ids(desc_text, tech_id_map)
                            title_text = _coerce_text(j.title)
                            all_titles = _build_titles(title_text, j.localizations)
                            occ_id, sen_id = _resolve_occupation_seniority(
                                all_titles, occ_ids, sen_ids
                            )
                            detected_langs = (
                                detect_all_languages(j.description) if j.description else []
                            )
                            records.append(
                                (
                                    pid,
                                    normalize_employment_type(_coerce_text(j.employment_type)),
                                    all_titles,
                                    _build_locales(
                                        _coerce_text(j.language),
                                        j.localizations,
                                        detected_languages=detected_langs,
                                    ),
                                    loc_ids,
                                    loc_types,
                                    s_min,
                                    s_max,
                                    s_cur,
                                    s_per,
                                    s_eur,
                                    exp_min,
                                    exp_max,
                                    t_ids,
                                    occ_id,
                                    sen_id,
                                )
                            )
                        await conn.copy_records_to_table("_rich_updates", records=records)
                        await conn.execute(_BATCH_UPDATE_RICH_CONTENT)

                        backfill_count = 0
                        for pid, j, existing_hash in update_triples:
                            if existing_hash is None:
                                backfill_count += 1
                                if backfill_count > _R2_BACKFILL_LIMIT:
                                    continue
                            staged = _stage_r2_pending(
                                title=_coerce_text(j.title),
                                description=_coerce_text(j.description),
                                language=_coerce_text(j.language),
                                locations=_coerce_locations(j.locations),
                                localizations=j.localizations,
                                extras=j.extras,
                                metadata=j.metadata,
                                date_posted=j.date_posted,
                                base_salary=j.base_salary,
                                employment_type=_coerce_text(j.employment_type),
                                job_location_type=_coerce_text(j.job_location_type),
                                current_hash=existing_hash,
                                source="monitor",
                                tech_ids=_resolve_technology_ids(
                                    _coerce_text(j.description), tech_id_map
                                ),
                            )
                            if staged:
                                is_first = existing_hash is None
                                depth = await _get_r2_queue_depth(pool)
                                queue_ok = is_first or depth < settings.r2_queue_max
                                if queue_ok:
                                    await conn.execute(
                                        _STAGE_R2_PENDING,
                                        str(pid),
                                        staged[0],
                                        staged[1],
                                    )

                # URL-only path — insert stubs with next_scrape_at
                if result.jobs_by_url is None and new_urls:
                    inserted = await conn.fetch(
                        _INSERT_URL_ONLY_JOBS,
                        company_id,
                        board_id,
                        new_urls,
                    )
                    board_log.info("batch.inserted_for_scrape", count=len(inserted))

            board_log.info(
                "batch.monitor.stream_batch",
                batch=batch_count,
                discovered=len(result.urls),
                new=len(new_urls),
            )

        # After all batches: mark gone postings
        if not all_urls:
            # No URLs discovered at all (or all filtered out) — treat as empty check
            elapsed = monotonic() - t0
            board_log.warning("batch.monitor.empty", duration_s=round(elapsed, 2))
            async with pool.acquire() as conn:
                rows = await conn.fetch(_RECORD_EMPTY_CHECK, board_id)
                if rows and rows[0]["board_status"] == "gone":
                    await conn.execute(_DELIST_BOARD_POSTINGS, board_id)
                    board_log.warning("batch.monitor.board_gone")
            return True, elapsed

        # Run gone marking with the full URL set (all_urls is guaranteed non-empty here)
        gone_count = 0
        delist_threshold = _DELIST_THRESHOLD_AUTHORITATIVE
        async with pool.acquire() as conn, conn.transaction():
            gone_rows = await conn.fetch(
                _MARK_GONE,
                list(all_urls),
                board_id,
                delist_threshold,
            )
            gone_count = len(gone_rows)
            await conn.execute(_RECORD_SUCCESS_NONEMPTY, board_id)

        # Flush location misses to taxonomy_miss table
        await _flush_location_misses(loc_resolver, pool)

        elapsed = monotonic() - t0
        board_log.info(
            "batch.monitor.success",
            discovered=len(all_urls),
            new=total_new,
            relisted=total_relisted,
            gone=gone_count,
            batches=batch_count,
            duration_s=round(elapsed, 2),
        )
        if elapsed >= _SLOW_MONITOR_SECONDS:
            board_log.warning("batch.monitor.slow", duration_s=round(elapsed, 2))

        if total_new or gone_count:
            with contextlib.suppress(Exception):
                await get_redis().delete("cache:platform-stats")

        return True, elapsed

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        board_log.exception("batch.monitor.error", error=error_msg, duration_s=round(elapsed, 2))
        # Discard stale location misses from this failed board
        loc_resolver.drain_location_misses()
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_FAILURE, board_id, error_msg)
        return False, elapsed


async def _process_one_board(
    board: asyncpg.Record,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
) -> tuple[bool, float]:
    """Run a full monitor cycle for a single board. Returns (success, duration_s)."""
    board_id = str(board["id"])
    company_id = str(board["company_id"])
    board_url = board["board_url"]
    crawler_type = board["crawler_type"]

    board_log = log.bind(board_id=board_id, board_url=board_url, crawler_type=crawler_type)
    t0 = monotonic()

    try:
        # Build monitor config from board metadata
        metadata = board["metadata"] if board["metadata"] else {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        # Use a per-board insecure client when ssl_verify is disabled
        ssl_verify = metadata.get("ssl_verify", True)
        effective_http = http
        if not ssl_verify:
            from src.shared.http import create_http_client

            effective_http = create_http_client(verify=False)

        # Start Playwright if this monitor needs a browser (e.g. api_sniffer replay)
        pw = None
        pw_ctx = None
        if monitor_needs_browser(crawler_type, metadata):
            try:
                from playwright.async_api import async_playwright

                pw_ctx = async_playwright()
                pw = await pw_ctx.start()
                board_log.info("batch.monitor.playwright_started")
            except Exception:
                board_log.warning("batch.monitor.playwright_unavailable", exc_info=True)

        try:
            result = await monitor_one(board_url, crawler_type, metadata, effective_http, pw=pw)
        finally:
            if pw:
                await pw.stop()
            if effective_http is not http:
                await effective_http.aclose()

        enrich_fields = _board_has_enrich(metadata)

        if not result.urls:
            elapsed = monotonic() - t0
            board_log.warning("batch.monitor.empty", duration_s=round(elapsed, 2))
            async with pool.acquire() as conn:
                rows = await conn.fetch(_RECORD_EMPTY_CHECK, board_id)
                # If board transitioned to 'gone', delist all its active postings
                if rows and rows[0]["board_status"] == "gone":
                    await conn.execute(_DELIST_BOARD_POSTINGS, board_id)
                    board_log.warning("batch.monitor.board_gone")
            return True, elapsed

        async with pool.acquire() as conn, conn.transaction():
            # Persist newly discovered sitemap URL
            if result.new_sitemap_url:
                await conn.execute(
                    _UPDATE_METADATA,
                    board_id,
                    json.dumps({"sitemap_url": result.new_sitemap_url}),
                )

            # Run diff — authoritative monitors delist on 1st miss, fragile on 2nd
            delist_threshold = (
                _DELIST_THRESHOLD_AUTHORITATIVE
                if crawler_type in _API_MONITOR_TYPES
                else _DELIST_THRESHOLD_FRAGILE
            )
            is_rich = result.jobs_by_url is not None
            # Rich + enrich → relisted jobs get next_scrape_at = now()
            is_rich_no_scrape = is_rich and not enrich_fields
            rows = await conn.fetch(
                _DIFF_URLS,
                list(result.urls),
                board_id,
                delist_threshold,
                is_rich_no_scrape,
            )

            new_urls: list[str] = []
            relisted: list[dict] = []
            touched: list[dict] = []
            gone: list[dict] = []

            for row in rows:
                action = row["action"]
                if action == "new":
                    new_urls.append(row["url"])
                elif action == "relisted":
                    r2h = row["description_r2_hash"]
                    relisted.append(
                        {
                            "id": row["id"],
                            "url": row["url"],
                            "r2_hash": int(r2h) if r2h is not None else None,
                        }
                    )
                elif action == "touched":
                    r2h = row["description_r2_hash"]
                    touched.append(
                        {
                            "id": row["id"],
                            "url": row["url"],
                            "r2_hash": int(r2h) if r2h is not None else None,
                        }
                    )
                elif action == "gone":
                    gone.append({"id": row["id"], "url": row["url"]})

            # Rich data path
            if result.jobs_by_url:
                new_jobs = [result.jobs_by_url[u] for u in new_urls if u in result.jobs_by_url]

                # Enrich descriptions + detect language for jobs that don't already have it
                for j in new_jobs:
                    j.description = normalize_description_html(j.description)
                    enrich_description(j)
                    if not j.language and j.description:
                        j.language = detect_language(j.description)

                # Resolve locations
                loc_resolver = await _get_location_resolver(pool)
                rates = await _get_currency_rates(pool)
                tech_id_map = await _get_technology_ids(pool)
                occ_ids = await _get_occupation_ids(pool)
                sen_ids = await _get_seniority_ids(pool)

                if new_jobs:
                    for j in new_jobs:
                        loc_ids, loc_types = await _resolve_locations(
                            loc_resolver,
                            _coerce_locations(j.locations),
                            _coerce_text(j.job_location_type),
                            _coerce_text(j.language),
                        )
                        desc_text = _coerce_text(j.description)
                        s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
                        exp_min, exp_max = _extract_experience_fields(desc_text)
                        t_ids = _resolve_technology_ids(desc_text, tech_id_map)
                        title_text = _coerce_text(j.title)
                        all_titles = _build_titles(title_text, j.localizations)
                        occ_id, sen_id = _resolve_occupation_seniority(all_titles, occ_ids, sen_ids)
                        detected_langs = (
                            detect_all_languages(j.description) if j.description else []
                        )
                        insert_sql = _INSERT_RICH_JOB_ENRICH if enrich_fields else _INSERT_RICH_JOB
                        row = await conn.fetchrow(
                            insert_sql,
                            company_id,
                            board_id,
                            normalize_employment_type(_coerce_text(j.employment_type)),
                            j.url,
                            all_titles,
                            _build_locales(
                                _coerce_text(j.language),
                                j.localizations,
                                detected_languages=detected_langs,
                            ),
                            loc_ids,
                            loc_types,
                            s_min,
                            s_max,
                            s_cur,
                            s_per,
                            s_eur,
                            exp_min,
                            exp_max,
                            t_ids,
                            occ_id,
                            sen_id,
                        )
                        if row:
                            staged = _stage_r2_pending(
                                title=_coerce_text(j.title),
                                description=_coerce_text(j.description),
                                language=_coerce_text(j.language),
                                locations=_coerce_locations(j.locations),
                                localizations=j.localizations,
                                extras=j.extras,
                                metadata=j.metadata,
                                date_posted=j.date_posted,
                                base_salary=j.base_salary,
                                employment_type=_coerce_text(j.employment_type),
                                job_location_type=_coerce_text(j.job_location_type),
                                source="monitor",
                                tech_ids=t_ids,
                            )
                            if staged:
                                await conn.execute(
                                    _STAGE_R2_PENDING,
                                    str(row["id"]),
                                    staged[0],
                                    staged[1],
                                )

                # Update content for relisted and existing active jobs
                update_triples = [
                    (item["id"], result.jobs_by_url[item["url"]], item.get("r2_hash"))
                    for item in relisted + touched
                    if item["url"] in result.jobs_by_url
                ]
                if update_triples:
                    for _, j, _ in update_triples:
                        j.description = normalize_description_html(j.description)
                        enrich_description(j)
                        if not j.language and j.description:
                            j.language = detect_language(j.description)

                    await conn.execute(_CREATE_RICH_UPDATES_TEMP)
                    records = []
                    for pid, j, _ in update_triples:
                        loc_ids, loc_types = await _resolve_locations(
                            loc_resolver,
                            _coerce_locations(j.locations),
                            _coerce_text(j.job_location_type),
                            _coerce_text(j.language),
                        )
                        desc_text = _coerce_text(j.description)
                        s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
                        exp_min, exp_max = _extract_experience_fields(desc_text)
                        t_ids = _resolve_technology_ids(desc_text, tech_id_map)
                        title_text = _coerce_text(j.title)
                        all_titles = _build_titles(title_text, j.localizations)
                        occ_id, sen_id = _resolve_occupation_seniority(all_titles, occ_ids, sen_ids)
                        detected_langs = (
                            detect_all_languages(j.description) if j.description else []
                        )
                        records.append(
                            (
                                pid,
                                normalize_employment_type(_coerce_text(j.employment_type)),
                                all_titles,
                                _build_locales(
                                    _coerce_text(j.language),
                                    j.localizations,
                                    detected_languages=detected_langs,
                                ),
                                loc_ids,
                                loc_types,
                                s_min,
                                s_max,
                                s_cur,
                                s_per,
                                s_eur,
                                exp_min,
                                exp_max,
                                t_ids,
                                occ_id,
                                sen_id,
                            )
                        )
                    await conn.copy_records_to_table(
                        "_rich_updates",
                        records=records,
                    )
                    await conn.execute(_BATCH_UPDATE_RICH_CONTENT)

                    # Stage R2 pending for updated postings:
                    # - With existing hash: always check for content changes
                    # - Without hash (backfill): cap to avoid overwhelming R2
                    backfill_count = 0
                    for pid, j, existing_hash in update_triples:
                        if existing_hash is None:
                            backfill_count += 1
                            if backfill_count > _R2_BACKFILL_LIMIT:
                                continue
                        staged = _stage_r2_pending(
                            title=_coerce_text(j.title),
                            description=_coerce_text(j.description),
                            language=_coerce_text(j.language),
                            locations=_coerce_locations(j.locations),
                            localizations=j.localizations,
                            extras=j.extras,
                            metadata=j.metadata,
                            date_posted=j.date_posted,
                            base_salary=j.base_salary,
                            employment_type=_coerce_text(j.employment_type),
                            job_location_type=_coerce_text(j.job_location_type),
                            current_hash=existing_hash,
                            source="monitor",
                            tech_ids=_resolve_technology_ids(
                                _coerce_text(j.description), tech_id_map
                            ),
                        )
                        if staged:
                            is_first = existing_hash is None
                            depth = await _get_r2_queue_depth(pool)
                            queue_ok = is_first or depth < settings.r2_queue_max
                            if queue_ok:
                                await conn.execute(
                                    _STAGE_R2_PENDING,
                                    str(pid),
                                    staged[0],
                                    staged[1],
                                )
                    if backfill_count > _R2_BACKFILL_LIMIT:
                        board_log.info(
                            "batch.r2_backfill.capped",
                            total=backfill_count,
                            limit=_R2_BACKFILL_LIMIT,
                        )

            # URL-only path — insert stubs with next_scrape_at for Postgres scheduler
            if result.jobs_by_url is None and new_urls:
                inserted = await conn.fetch(
                    _INSERT_URL_ONLY_JOBS,
                    company_id,
                    board_id,
                    new_urls,
                )
                board_log.info("batch.inserted_for_scrape", count=len(inserted))

            await conn.execute(_RECORD_SUCCESS_NONEMPTY, board_id)

        # Flush location misses to taxonomy_miss table
        if result.jobs_by_url:
            await _flush_location_misses(loc_resolver, pool)

        elapsed = monotonic() - t0
        board_log.info(
            "batch.monitor.success",
            discovered=len(result.urls),
            new=len(new_urls),
            relisted=len(relisted),
            gone=len(gone),
            duration_s=round(elapsed, 2),
        )
        if elapsed >= _SLOW_MONITOR_SECONDS:
            board_log.warning("batch.monitor.slow", duration_s=round(elapsed, 2))

        # Invalidate stats cache when job counts change
        if new_urls or gone:
            with contextlib.suppress(Exception):
                await get_redis().delete("cache:platform-stats")

        # Free large temporaries (jobs_by_url) before next item
        del result

        return True, elapsed

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        board_log.exception("batch.monitor.error", error=error_msg, duration_s=round(elapsed, 2))
        # Discard stale location misses from this failed board
        if _location_resolver is not None:
            _location_resolver.drain_location_misses()
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_FAILURE, board_id, error_msg)
        return False, elapsed


# ── Producer-Consumer Monitor Pipeline ────────────────────────────────


def _process_jobs_cpu(
    jobs_by_url: dict,
    company_id: str,
    board_id: str,
    loc_resolver: LocationResolver,
    rates: dict[str, float],
    tech_id_map: dict[str, int],
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
) -> dict[str, JobCPUResult]:
    """Pure CPU work: normalize, detect language, resolve, extract.

    Runs in a thread via ``asyncio.to_thread``.  No async, no DB.
    Returns a dict of ``{url: JobCPUResult}``.
    """
    results: dict[str, JobCPUResult] = {}
    for url, j in jobs_by_url.items():
        j.description = normalize_description_html(j.description)
        enrich_description(j)
        if not j.language and j.description:
            j.language = detect_language(j.description)

        loc_ids_r, loc_types_r = _resolve_locations_sync(
            loc_resolver,
            _coerce_locations(j.locations),
            _coerce_text(j.job_location_type),
            _coerce_text(j.language),
        )
        desc_text = _coerce_text(j.description)
        s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
        exp_min, exp_max = _extract_experience_fields(desc_text)
        t_ids = _resolve_technology_ids(desc_text, tech_id_map)
        title_text = _coerce_text(j.title)
        all_titles = _build_titles(title_text, j.localizations)
        occ_id, sen_id = _resolve_occupation_seniority(all_titles, occ_ids, sen_ids)
        detected_langs = detect_all_languages(j.description) if j.description else []

        insert_record = (
            company_id,
            board_id,
            normalize_employment_type(_coerce_text(j.employment_type)),
            j.url,
            all_titles,
            _build_locales(
                _coerce_text(j.language),
                j.localizations,
                detected_languages=detected_langs,
            ),
            loc_ids_r,
            loc_types_r,
            s_min,
            s_max,
            s_cur,
            s_per,
            s_eur,
            exp_min,
            exp_max,
            t_ids,
            occ_id,
            sen_id,
        )

        r2_staging_args = dict(
            title=_coerce_text(j.title),
            description=_coerce_text(j.description),
            language=_coerce_text(j.language),
            locations=_coerce_locations(j.locations),
            localizations=j.localizations,
            extras=j.extras,
            metadata=j.metadata,
            date_posted=j.date_posted,
            base_salary=j.base_salary,
            employment_type=_coerce_text(j.employment_type),
            job_location_type=_coerce_text(j.job_location_type),
            source="monitor",
            tech_ids=t_ids,
        )

        results[url] = JobCPUResult(
            url=url,
            insert_record=insert_record,
            r2_staging_args=r2_staging_args,
            tech_ids=t_ids,
        )
    return results


async def _monitor_worker(
    http: httpx.AsyncClient,
    pool: asyncpg.Pool,
    fetch_buffer: asyncio.Queue,
    write_buffer: asyncio.Queue,
    loc_resolver: LocationResolver,
    rates: dict[str, float],
    tech_id_map: dict[str, int],
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
    worker_id: int,
) -> int:
    """Consume boards from fetch_buffer, run monitor + CPU work, put results in write_buffer.

    Returns the number of boards processed.
    """
    boards_processed = 0
    while True:
        board = await fetch_buffer.get()
        if board is _SENTINEL:
            fetch_buffer.task_done()
            break

        board_id = str(board["id"])
        company_id = str(board["company_id"])
        board_url = board["board_url"]
        crawler_type = board["crawler_type"]
        board_log = log.bind(
            board_id=board_id,
            board_url=board_url,
            crawler_type=crawler_type,
            pipeline_worker=worker_id,
        )

        try:
            metadata = board["metadata"] if board["metadata"] else {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            enrich_fields = _board_has_enrich(metadata)
            delist_threshold = (
                _DELIST_THRESHOLD_AUTHORITATIVE
                if crawler_type in _API_MONITOR_TYPES
                else _DELIST_THRESHOLD_FRAGILE
            )

            # Detect if this board needs a browser
            needs_browser = monitor_needs_browser(crawler_type, metadata)
            pw = None
            pw_ctx = None
            if needs_browser:
                try:
                    from playwright.async_api import async_playwright

                    pw_ctx = async_playwright()
                    pw = await pw_ctx.start()
                    board_log.info("pipeline.worker.playwright_started")
                except Exception:
                    board_log.warning("pipeline.worker.playwright_unavailable", exc_info=True)

            all_urls: set[str] = set()
            total_new = 0
            total_relisted = 0
            batch_count = 0

            try:
                # Handle SSL verification
                ssl_verify = metadata.get("ssl_verify", True)
                effective_http = http
                if not ssl_verify:
                    from src.shared.http import create_http_client

                    effective_http = create_http_client(verify=False)

                try:
                    async for result in monitor_one_stream(
                        board_url, crawler_type, metadata, effective_http
                    ):
                        batch_count += 1
                        all_urls.update(result.urls)

                        if not result.urls:
                            continue

                        # Extend DB lease (shielded — fire-and-forget)
                        with contextlib.suppress(Exception):
                            await asyncio.shield(pool.execute(_EXTEND_BOARD_LEASE, board_id))

                        # CPU work in thread
                        cpu_results: dict[str, JobCPUResult] = {}
                        if result.jobs_by_url:
                            cpu_results = await asyncio.to_thread(
                                _process_jobs_cpu,
                                result.jobs_by_url,
                                company_id,
                                board_id,
                                loc_resolver,
                                rates,
                                tech_id_map,
                                occ_ids,
                                sen_ids,
                            )

                        await write_buffer.put(
                            BoardBatch(
                                board_id=board_id,
                                company_id=company_id,
                                board_url=board_url,
                                enrich_fields=enrich_fields,
                                urls=result.urls,
                                jobs_by_url=result.jobs_by_url,
                                cpu_results=cpu_results,
                                delist_threshold=delist_threshold,
                            )
                        )

                        board_log.info(
                            "pipeline.worker.batch",
                            batch=batch_count,
                            discovered=len(result.urls),
                        )
                finally:
                    if effective_http is not http:
                        await effective_http.aclose()
            finally:
                if pw:
                    await pw.stop()
                if pw_ctx:
                    with contextlib.suppress(Exception):
                        await pw_ctx.__aexit__(None, None, None)

            # Send done signal
            if not all_urls:
                # Empty check — let the writer handle it
                await write_buffer.put(
                    BoardDone(
                        board_id=board_id,
                        board_url=board_url,
                        all_urls=set(),
                        delist_threshold=delist_threshold,
                        total_new=0,
                        total_relisted=0,
                    )
                )
            else:
                await write_buffer.put(
                    BoardDone(
                        board_id=board_id,
                        board_url=board_url,
                        all_urls=all_urls,
                        delist_threshold=delist_threshold,
                        total_new=total_new,
                        total_relisted=total_relisted,
                    )
                )

            boards_processed += 1

        except Exception as exc:
            error_msg = _error_message(exc)
            board_log.exception("pipeline.worker.error", error=error_msg)
            await write_buffer.put(
                BoardError(
                    board_id=board_id,
                    board_url=board_url,
                    error_msg=error_msg,
                )
            )
            boards_processed += 1

        fetch_buffer.task_done()

    return boards_processed


async def _do_one_enrich_scrape(
    work: _ScrapeWorkItem,
    http: httpx.AsyncClient,
    pool: asyncpg.Pool,
    loc_resolver: LocationResolver,
    rates: dict[str, float],
    tech_id_map: dict[str, int],
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
) -> ScrapeResult | ScrapeError:
    """Scrape + enrich inline (no threading). Returns ScrapeResult or ScrapeError."""
    item = work.item
    enrich_fields = work.enrich_fields or []
    cfg = work.scraper_config or {}

    content = await scrape_one(item.url, work.scraper_type, work.scraper_config, http)
    content = await _apply_fallback_chain(content, item.url, work.scraper_type, cfg, http)
    content = _apply_defaults(content, cfg)

    # Normalize before checking
    content.description = normalize_description_html(content.description)

    # Success check: at least one enriched field is non-empty
    has_data = any(getattr(content, f, None) is not None for f in enrich_fields)
    if not has_data:
        return ScrapeError(job_posting_id=item.job_posting_id)

    # Detect language if not already set
    language = content.language
    if not language and content.description:
        language = detect_language(content.description)

    # Default all params to None (COALESCE preserves existing)
    norm_emp_type = None
    all_titles = None
    locales = None
    loc_ids = None
    loc_types = None
    desc_pending = None
    meta_pending = None
    tech_ids = None
    s_min = s_max = s_cur = s_per = s_eur = None
    exp_min = exp_max = None
    occ_id = sen_id = None

    if "employment_type" in enrich_fields:
        norm_emp_type = normalize_employment_type(_coerce_text(content.employment_type))

    if "title" in enrich_fields:
        title_text = _coerce_text(content.title)
        all_titles = _build_titles(title_text, None) or None
        occ_id, sen_id = _resolve_occupation_seniority(all_titles, occ_ids, sen_ids)
        lang_text = _coerce_text(language)
        if lang_text or content.description:
            detected_langs = (
                detect_all_languages(content.description) if content.description else []
            )
            built = _build_locales(lang_text, None, detected_languages=detected_langs)
            if lang_text or detected_langs:
                locales = built

    if "locations" in enrich_fields:
        lang_text = _coerce_text(language)
        loc_ids, loc_types = _resolve_locations_sync(
            loc_resolver,
            _coerce_locations(content.locations),
            _coerce_text(content.job_location_type),
            posting_language=lang_text,
        )

    if "description" in enrich_fields:
        desc_text = _coerce_text(content.description)
        tech_ids = _resolve_technology_ids(desc_text, tech_id_map)
        s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
        exp_min, exp_max = _extract_experience_fields(desc_text)

        # Fetch existing posting data for R2 extras
        existing = await pool.fetchrow(_FETCH_POSTING_FOR_ENRICH, item.job_posting_id)
        r2_title = None
        if existing:
            titles_arr = existing["titles"]
            if titles_arr:
                r2_title = titles_arr[0]
        r2_title = r2_title or _coerce_text(content.title)
        r2_locations = _coerce_locations(content.locations)

        staged = _stage_r2_pending(
            title=r2_title,
            description=desc_text,
            language=_coerce_text(language),
            locations=r2_locations,
            localizations=None,
            extras=content.extras,
            metadata=content.metadata,
            date_posted=content.date_posted,
            base_salary=content.base_salary,
            employment_type=_coerce_text(content.employment_type),
            job_location_type=_coerce_text(content.job_location_type),
            current_hash=item.description_r2_hash,
            source="scrape",
            tech_ids=tech_ids,
        )
        desc_pending = staged[0] if staged else None
        meta_pending = staged[1] if staged else None
        # Queue cap: skip re-upload staging when queue is full
        if (
            staged
            and item.description_r2_hash is not None
            and await _get_r2_queue_depth(pool) >= settings.r2_queue_max
        ):
            desc_pending = None
            meta_pending = None

    params = (
        item.job_posting_id,
        norm_emp_type,
        all_titles,
        locales,
        loc_ids,
        loc_types,
        desc_pending,
        meta_pending,
        tech_ids,
        s_min,
        s_max,
        s_cur,
        s_per,
        s_eur,
        exp_min,
        exp_max,
        occ_id,
        sen_id,
    )

    return ScrapeResult(
        job_posting_id=item.job_posting_id,
        params=params,
        is_enrich=True,
    )


async def _do_one_scrape(
    work: _ScrapeWorkItem,
    http: httpx.AsyncClient,
    pool: asyncpg.Pool,
    loc_resolver: LocationResolver,
    rates: dict[str, float],
    tech_id_map: dict[str, int],
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
) -> ScrapeResult | ScrapeError:
    """Scrape + CPU work inline (no threading). Returns ScrapeResult or ScrapeError.

    Modeled on ``_process_one_scrape()`` but returns a result for the DB
    writer instead of writing directly.
    """
    item = work.item
    cfg = work.scraper_config or {}

    # Early dispatch for enrich-only scrapes
    enrich_fields = cfg.get("enrich")
    if isinstance(enrich_fields, list) and enrich_fields:
        return await _do_one_enrich_scrape(
            work, http, pool, loc_resolver, rates, tech_id_map, occ_ids, sen_ids
        )

    content = await scrape_one(item.url, work.scraper_type, work.scraper_config, http)
    content = await _apply_fallback_chain(content, item.url, work.scraper_type, cfg, http)
    content = _apply_defaults(content, cfg)

    if not content.title or _is_garbage_title(content.title):
        if content.title:
            log.info("pipeline.scrape.garbage_title", url=item.url, title=content.title)
        return ScrapeError(job_posting_id=item.job_posting_id)

    content.description = normalize_description_html(content.description)

    # Detect language if not already set
    language = content.language
    if not language and content.description:
        language = detect_language(content.description)

    detected_langs = detect_all_languages(content.description) if content.description else []

    title_text = _coerce_text(content.title)
    desc_text = _coerce_text(content.description)
    lang_text = _coerce_text(language)
    raw_emp_type = _coerce_text(content.employment_type)
    norm_emp_type = normalize_employment_type(raw_emp_type)

    # Resolve locations (sync — no threading, no DB backfill)
    loc_ids, loc_types = _resolve_locations_sync(
        loc_resolver,
        _coerce_locations(content.locations),
        _coerce_text(content.job_location_type),
        posting_language=lang_text,
    )

    # Resolve technologies from description
    tech_ids = _resolve_technology_ids(desc_text, tech_id_map)

    # Resolve occupation + seniority from title
    occ_id, sen_id = _resolve_occupation_seniority(title_text, occ_ids, sen_ids)

    # Extract salary + experience from description
    s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
    exp_min, exp_max = _extract_experience_fields(desc_text)

    # Stage R2 pending data (pure computation, no I/O)
    staged = _stage_r2_pending(
        title=title_text,
        description=desc_text,
        language=lang_text,
        locations=_coerce_locations(content.locations),
        localizations=None,
        extras=content.extras,
        metadata=content.metadata,
        date_posted=content.date_posted,
        base_salary=content.base_salary,
        employment_type=raw_emp_type,
        job_location_type=_coerce_text(content.job_location_type),
        current_hash=item.description_r2_hash,
        source="scrape",
        tech_ids=tech_ids,
    )
    desc_pending = staged[0] if staged else None
    meta_pending = staged[1] if staged else None
    # Queue cap: skip re-upload staging when queue is full
    if (
        staged
        and item.description_r2_hash is not None
        and await _get_r2_queue_depth(pool) >= settings.r2_queue_max
    ):
        desc_pending = None
        meta_pending = None

    params = (
        item.job_posting_id,
        norm_emp_type,
        _build_titles(title_text, None),
        _build_locales(lang_text, None, detected_languages=detected_langs),
        loc_ids,
        loc_types,
        desc_pending,
        meta_pending,
        tech_ids,
        s_min,
        s_max,
        s_cur,
        s_per,
        s_eur,
        exp_min,
        exp_max,
        occ_id,
        sen_id,
    )

    return ScrapeResult(
        job_posting_id=item.job_posting_id,
        params=params,
        is_enrich=False,
    )


async def _scrape_pipeline_worker(
    http: httpx.AsyncClient,
    pool: asyncpg.Pool,
    scrape_fetch_buffer: asyncio.Queue,
    write_buffer: asyncio.Queue,
    loc_resolver: LocationResolver,
    rates: dict[str, float],
    tech_id_map: dict[str, int],
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
    worker_id: int,
) -> int:
    """Consume _ScrapeWorkItem from scrape_fetch_buffer, scrape, put result in write_buffer.

    Returns the number of scrapes processed.
    """
    scrapes_processed = 0
    while True:
        work_item = await scrape_fetch_buffer.get()
        if work_item is _SENTINEL:
            scrape_fetch_buffer.task_done()
            break

        try:
            # Handle SSL verification — create insecure client if needed
            effective_http = http
            insecure_http = None
            if not work_item.ssl_verify:
                from src.shared.http import create_http_client

                insecure_http = create_http_client(verify=False)
                effective_http = insecure_http

            try:
                result = await _do_one_scrape(
                    work_item,
                    effective_http,
                    pool,
                    loc_resolver,
                    rates,
                    tech_id_map,
                    occ_ids,
                    sen_ids,
                )
                await write_buffer.put(result)
            finally:
                if insecure_http is not None:
                    await insecure_http.aclose()

            scrapes_processed += 1

        except Exception as exc:
            log.error(
                "pipeline.scrape_worker.error",
                url=work_item.item.url,
                error=_error_message(exc),
                worker_id=worker_id,
            )
            await write_buffer.put(ScrapeError(job_posting_id=work_item.item.job_posting_id))
            scrapes_processed += 1

        scrape_fetch_buffer.task_done()

    return scrapes_processed


async def _scrape_pipeline_producer(
    pool: asyncpg.Pool,
    scrape_fetch_buffer: asyncio.Queue,
    shutdown_event: asyncio.Event,
    num_workers: int,
    worker_id: str,
    browser: bool = False,
) -> None:
    """Claim scrape work from DB and feed _ScrapeWorkItem into scrape_fetch_buffer.

    Sends _SENTINEL to all workers on shutdown.
    """
    backoff = 1.0
    max_backoff = 30.0

    try:
        while not shutdown_event.is_set():
            budget = scrape_fetch_buffer.maxsize - scrape_fetch_buffer.qsize()
            if budget <= 0:
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=0.5,
                    )
                    break  # shutdown
                except TimeoutError:
                    continue

            claim_limit = min(budget, num_workers * 2)
            try:
                rows = await pool.fetch(
                    _CLAIM_SCRAPES,
                    claim_limit,
                    worker_id,
                    [],  # no domain exclusions in pipeline mode
                    browser,
                )
            except Exception:
                log.exception("pipeline.scrape_producer.claim_error")
                rows = []

            if not rows:
                # Adaptive backoff when no work available
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=backoff,
                    )
                    break  # shutdown
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, max_backoff)
                continue

            backoff = 1.0

            # Load board scraper configs for claimed items
            board_ids = {str(row["board_id"]) for row in rows if row["board_id"]}
            try:
                info = await _load_board_scrapers(pool, board_ids)
            except Exception:
                log.exception("pipeline.scrape_producer.load_scrapers_error")
                # Release leases for claimed items
                posting_ids = [str(row["id"]) for row in rows]
                with contextlib.suppress(Exception):
                    await pool.execute(_RELEASE_POSTING_LEASES, posting_ids)
                continue

            # Clear next_scrape_at for postings from rich monitors
            rich_posting_ids = [
                str(row["id"]) for row in rows if str(row["board_id"]) in info.rich_board_ids
            ]
            if rich_posting_ids:
                with contextlib.suppress(Exception):
                    await pool.execute(_CLEAR_SCRAPE_FOR_RICH, rich_posting_ids)
                log.info("pipeline.scrape_producer.cleared_rich", count=len(rich_posting_ids))

            fed = 0
            for row in rows:
                board_id = str(row["board_id"]) if row["board_id"] else ""

                # Skip rich-monitor postings
                if board_id in info.rich_board_ids:
                    continue

                scraper_type = "json-ld"
                scraper_config: dict | None = None
                ssl_verify = True
                enrich_fields: list[str] | None = None
                if board_id and board_id in info.scrapers:
                    bsc = info.scrapers[board_id]
                    scraper_type = bsc.scraper_type
                    scraper_config = bsc.scraper_config
                    ssl_verify = bsc.ssl_verify

                # Resolve enrich fields from scraper_config
                if scraper_config and isinstance(scraper_config, dict):
                    ef = scraper_config.get("enrich")
                    if isinstance(ef, list) and ef:
                        enrich_fields = ef

                r2_hash = row["description_r2_hash"]
                item = ScrapeItem(
                    job_posting_id=str(row["id"]),
                    url=row["source_url"],
                    board_id=board_id,
                    description_r2_hash=int(r2_hash) if r2_hash is not None else None,
                )

                work = _ScrapeWorkItem(
                    item=item,
                    scraper_type=scraper_type,
                    scraper_config=scraper_config,
                    enrich_fields=enrich_fields,
                    ssl_verify=ssl_verify,
                )
                await scrape_fetch_buffer.put(work)
                fed += 1

            if fed:
                log.info("pipeline.scrape_producer.claimed", count=fed)

    finally:
        # Always send sentinels so workers can exit
        for _ in range(num_workers):
            await scrape_fetch_buffer.put(_SENTINEL)
        log.info("pipeline.scrape_producer.shutdown", sentinels_sent=num_workers)


async def _db_writer(
    pool: asyncpg.Pool,
    write_buffer: asyncio.Queue,
    loc_resolver: LocationResolver,
) -> tuple[int, int, int, int, int]:
    """Drain write_buffer and perform all DB writes.

    Returns (boards_succeeded, total_new, total_gone, scrapes_succeeded, scrapes_failed).
    """
    boards_succeeded = 0
    total_new = 0
    total_gone = 0
    scrapes_succeeded = 0
    scrapes_failed = 0

    while True:
        item = await write_buffer.get()
        if item is _SENTINEL:
            write_buffer.task_done()
            break

        try:
            if isinstance(item, BoardError):
                with contextlib.suppress(Exception):
                    async with pool.acquire() as conn:
                        await conn.execute(_RECORD_FAILURE, item.board_id, item.error_msg)
                log.warning(
                    "pipeline.writer.board_error",
                    board_id=item.board_id,
                    board_url=item.board_url,
                    error=item.error_msg,
                )

            elif isinstance(item, BoardDone):
                if not item.all_urls:
                    # Empty check
                    async with pool.acquire() as conn:
                        rows = await conn.fetch(_RECORD_EMPTY_CHECK, item.board_id)
                        if rows and rows[0]["board_status"] == "gone":
                            await conn.execute(_DELIST_BOARD_POSTINGS, item.board_id)
                            log.warning(
                                "pipeline.writer.board_gone",
                                board_id=item.board_id,
                                board_url=item.board_url,
                            )
                else:
                    # Mark gone + record success
                    gone_count = 0
                    async with pool.acquire() as conn, conn.transaction():
                        gone_rows = await conn.fetch(
                            _MARK_GONE,
                            list(item.all_urls),
                            item.board_id,
                            item.delist_threshold,
                        )
                        gone_count = len(gone_rows)
                        await conn.execute(_RECORD_SUCCESS_NONEMPTY, item.board_id)
                    total_gone += gone_count

                    log.info(
                        "pipeline.writer.board_done",
                        board_id=item.board_id,
                        board_url=item.board_url,
                        all_urls=len(item.all_urls),
                        gone=gone_count,
                        new=item.total_new,
                        relisted=item.total_relisted,
                    )

                    # Invalidate Redis cache when job counts change
                    if item.total_new or gone_count:
                        with contextlib.suppress(Exception):
                            await get_redis().delete("cache:platform-stats")

                boards_succeeded += 1

            elif isinstance(item, BoardBatch):
                # BoardBatch — run diff + insert + update
                batch: BoardBatch = item
                batch_new = 0
                batch_relisted = 0

                async with pool.acquire() as conn, conn.transaction():
                    is_rich = batch.jobs_by_url is not None
                    is_rich_no_scrape = is_rich and not batch.enrich_fields
                    rows = await conn.fetch(
                        _DIFF_BATCH,
                        list(batch.urls),
                        batch.board_id,
                        is_rich_no_scrape,
                    )

                    new_urls: list[str] = []
                    relisted: list[dict] = []
                    touched: list[dict] = []

                    for row in rows:
                        action = row["action"]
                        if action == "new":
                            new_urls.append(row["url"])
                        elif action == "relisted":
                            r2h = row["description_r2_hash"]
                            relisted.append(
                                {
                                    "id": row["id"],
                                    "url": row["url"],
                                    "r2_hash": int(r2h) if r2h is not None else None,
                                }
                            )
                        elif action == "touched":
                            r2h = row["description_r2_hash"]
                            touched.append(
                                {
                                    "id": row["id"],
                                    "url": row["url"],
                                    "r2_hash": int(r2h) if r2h is not None else None,
                                }
                            )

                    batch_new = len(new_urls)
                    batch_relisted = len(relisted)

                    if batch.jobs_by_url:
                        # Insert new rich jobs
                        if new_urls and batch.cpu_results:
                            insert_sql = (
                                _INSERT_RICH_JOB_ENRICH
                                if batch.enrich_fields
                                else _INSERT_RICH_JOB
                            )
                            inserted_ids: list[str] = []
                            r2_staging_list: list[tuple[str, dict]] = []
                            for url in new_urls:
                                if url not in batch.cpu_results:
                                    continue
                                cpu_r = batch.cpu_results[url]
                                row = await conn.fetchrow(insert_sql, *cpu_r.insert_record)
                                if row:
                                    posting_id = str(row["id"])
                                    inserted_ids.append(posting_id)
                                    r2_staging_list.append((posting_id, cpu_r.r2_staging_args))

                            # Stage R2 pending for inserted jobs
                            for posting_id, r2_args in r2_staging_list:
                                staged = _stage_r2_pending(**r2_args)
                                if staged:
                                    await conn.execute(
                                        _STAGE_R2_PENDING,
                                        posting_id,
                                        staged[0],
                                        staged[1],
                                    )

                        # Update content for relisted and touched
                        update_triples = [
                            (
                                item_d["id"],
                                batch.jobs_by_url[item_d["url"]],
                                item_d.get("r2_hash"),
                            )
                            for item_d in relisted + touched
                            if item_d["url"] in batch.jobs_by_url
                        ]
                        if update_triples:
                            # CPU work for relisted/touched (normalize, enrich, detect lang)
                            for _, j, _ in update_triples:
                                j.description = normalize_description_html(j.description)
                                enrich_description(j)
                                if not j.language and j.description:
                                    j.language = detect_language(j.description)

                            await conn.execute(_CREATE_RICH_UPDATES_TEMP)
                            records = []
                            for pid, j, _ in update_triples:
                                loc_ids, loc_types = _resolve_locations_sync(
                                    loc_resolver,
                                    _coerce_locations(j.locations),
                                    _coerce_text(j.job_location_type),
                                    _coerce_text(j.language),
                                )
                                desc_text = _coerce_text(j.description)
                                s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(
                                    desc_text, await _get_currency_rates(pool)
                                )
                                exp_min, exp_max = _extract_experience_fields(desc_text)
                                t_ids = _resolve_technology_ids(
                                    desc_text, await _get_technology_ids(pool)
                                )
                                title_text = _coerce_text(j.title)
                                all_titles = _build_titles(title_text, j.localizations)
                                occ_id, sen_id = _resolve_occupation_seniority(
                                    all_titles,
                                    await _get_occupation_ids(pool),
                                    await _get_seniority_ids(pool),
                                )
                                detected_langs = (
                                    detect_all_languages(j.description)
                                    if j.description
                                    else []
                                )
                                records.append(
                                    (
                                        pid,
                                        normalize_employment_type(
                                            _coerce_text(j.employment_type)
                                        ),
                                        all_titles,
                                        _build_locales(
                                            _coerce_text(j.language),
                                            j.localizations,
                                            detected_languages=detected_langs,
                                        ),
                                        loc_ids,
                                        loc_types,
                                        s_min,
                                        s_max,
                                        s_cur,
                                        s_per,
                                        s_eur,
                                        exp_min,
                                        exp_max,
                                        t_ids,
                                        occ_id,
                                        sen_id,
                                    )
                                )
                            await conn.copy_records_to_table(
                                "_rich_updates", records=records
                            )
                            await conn.execute(_BATCH_UPDATE_RICH_CONTENT)

                            # R2 staging for relisted/touched
                            backfill_count = 0
                            for pid, j, existing_hash in update_triples:
                                if existing_hash is None:
                                    backfill_count += 1
                                    if backfill_count > _R2_BACKFILL_LIMIT:
                                        continue
                                staged = _stage_r2_pending(
                                    title=_coerce_text(j.title),
                                    description=_coerce_text(j.description),
                                    language=_coerce_text(j.language),
                                    locations=_coerce_locations(j.locations),
                                    localizations=j.localizations,
                                    extras=j.extras,
                                    metadata=j.metadata,
                                    date_posted=j.date_posted,
                                    base_salary=j.base_salary,
                                    employment_type=_coerce_text(j.employment_type),
                                    job_location_type=_coerce_text(j.job_location_type),
                                    current_hash=existing_hash,
                                    source="monitor",
                                    tech_ids=_resolve_technology_ids(
                                        _coerce_text(j.description),
                                        await _get_technology_ids(pool),
                                    ),
                                )
                                if staged:
                                    is_first = existing_hash is None
                                    depth = await _get_r2_queue_depth(pool)
                                    queue_ok = is_first or depth < settings.r2_queue_max
                                    if queue_ok:
                                        await conn.execute(
                                            _STAGE_R2_PENDING,
                                            str(pid),
                                            staged[0],
                                            staged[1],
                                        )

                    else:
                        # URL-only path — insert stubs with next_scrape_at
                        if new_urls:
                            await conn.fetch(
                                _INSERT_URL_ONLY_JOBS,
                                batch.company_id,
                                batch.board_id,
                                new_urls,
                            )

                total_new += batch_new

                # Backfill location cache misses (rare path)
                if await loc_resolver.backfill_misses():
                    loc_resolver.drain_location_misses()
                await _flush_location_misses(loc_resolver, pool)

                log.info(
                    "pipeline.writer.batch",
                    board_id=batch.board_id,
                    new=batch_new,
                    relisted=batch_relisted,
                    touched=len(touched),
                )

            elif isinstance(item, ScrapeResult):
                async with pool.acquire() as conn:
                    sql = _UPDATE_ENRICH_CONTENT if item.is_enrich else _UPDATE_JOB_CONTENT
                    await conn.execute(sql, *item.params)
                    await conn.execute(_RECORD_SCRAPE_SUCCESS, item.job_posting_id)
                scrapes_succeeded += 1

            elif isinstance(item, ScrapeError):
                async with pool.acquire() as conn:
                    await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
                scrapes_failed += 1

        except Exception:
            log.exception(
                "pipeline.writer.error",
                item_type=type(item).__name__,
                board_id=getattr(item, "board_id", "?"),
            )

        write_buffer.task_done()

    return boards_succeeded, total_new, total_gone, scrapes_succeeded, scrapes_failed


async def _monitor_producer(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    fetch_buffer: asyncio.Queue,
    shutdown_event: asyncio.Event,
    num_workers: int,
    worker_id: str,
    browser: bool = False,
) -> None:
    """Claim boards from DB and feed them into fetch_buffer.

    Sends _SENTINEL to all workers on shutdown.
    """
    backoff = 1.0
    max_backoff = 30.0

    try:
        while not shutdown_event.is_set():
            budget = fetch_buffer.maxsize - fetch_buffer.qsize()
            if budget <= 0:
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=0.5,
                    )
                    break  # shutdown
                except TimeoutError:
                    continue

            claim_limit = min(budget, num_workers * 2)
            try:
                rows = await pool.fetch(
                    _CLAIM_MONITORS,
                    claim_limit,
                    worker_id,
                    [],  # no domain exclusions in pipeline mode
                    browser,
                )
            except Exception:
                log.exception("pipeline.producer.claim_error")
                rows = []

            if rows:
                backoff = 1.0
                for board in rows:
                    await fetch_buffer.put(board)
                log.info("pipeline.producer.claimed", count=len(rows))
            else:
                # Adaptive backoff when no work available
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=backoff,
                    )
                    break  # shutdown
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, max_backoff)
    finally:
        # Always send sentinels so workers can exit
        for _ in range(num_workers):
            await fetch_buffer.put(_SENTINEL)
        log.info("pipeline.producer.shutdown", sentinels_sent=num_workers)


async def run_monitor_pipeline(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    shutdown_event: asyncio.Event,
    num_workers: int = 20,
    worker_id: str = "",
    browser: bool = False,
    num_scrape_workers: int = 0,
) -> None:
    """Orchestrate the producer-consumer monitor pipeline.

    - 1 producer claims boards from DB
    - N workers fetch APIs + run CPU work (no DB access except lease extension)
    - 1 DB writer does all diff/insert/update/staging
    - Optionally: 1 scrape producer + M scrape workers share the same DB writer
    """
    t0 = monotonic()

    # Pre-load lookup tables (shared read-only across all workers)
    loc_resolver = await _get_location_resolver(pool)
    rates = await _get_currency_rates(pool)
    tech_id_map = await _get_technology_ids(pool)
    occ_ids = await _get_occupation_ids(pool)
    sen_ids = await _get_seniority_ids(pool)

    fetch_buffer: asyncio.Queue = asyncio.Queue(maxsize=num_workers * 2)

    # Size write_buffer to accommodate both monitor and scrape workers
    write_buffer_size = num_workers * 4
    if num_scrape_workers > 0:
        write_buffer_size += num_scrape_workers * 2
    write_buffer: asyncio.Queue = asyncio.Queue(maxsize=write_buffer_size)

    # Scrape buffer (only created if scrape workers requested)
    scrape_fetch_buffer: asyncio.Queue | None = None
    if num_scrape_workers > 0:
        scrape_fetch_buffer = asyncio.Queue(maxsize=num_scrape_workers * 2)

    log.info(
        "pipeline.start",
        num_workers=num_workers,
        num_scrape_workers=num_scrape_workers,
        fetch_buffer_max=fetch_buffer.maxsize,
        write_buffer_max=write_buffer.maxsize,
        browser=browser,
    )

    # Start monitor producer
    producer_task = asyncio.create_task(
        _monitor_producer(
            pool,
            http,
            fetch_buffer,
            shutdown_event,
            num_workers,
            worker_id,
            browser=browser,
        ),
        name="pipeline-producer",
    )

    # Start monitor workers
    worker_tasks = []
    for i in range(num_workers):
        task = asyncio.create_task(
            _monitor_worker(
                http,
                pool,
                fetch_buffer,
                write_buffer,
                loc_resolver,
                rates,
                tech_id_map,
                occ_ids,
                sen_ids,
                worker_id=i,
            ),
            name=f"pipeline-worker-{i}",
        )
        worker_tasks.append(task)

    # Start scrape producer + workers (if requested)
    scrape_producer_task = None
    scrape_worker_tasks = []
    if num_scrape_workers > 0 and scrape_fetch_buffer is not None:
        scrape_producer_task = asyncio.create_task(
            _scrape_pipeline_producer(
                pool,
                scrape_fetch_buffer,
                shutdown_event,
                num_scrape_workers,
                worker_id,
                browser=browser,
            ),
            name="pipeline-scrape-producer",
        )

        for i in range(num_scrape_workers):
            task = asyncio.create_task(
                _scrape_pipeline_worker(
                    http,
                    pool,
                    scrape_fetch_buffer,
                    write_buffer,
                    loc_resolver,
                    rates,
                    tech_id_map,
                    occ_ids,
                    sen_ids,
                    worker_id=i,
                ),
                name=f"pipeline-scrape-worker-{i}",
            )
            scrape_worker_tasks.append(task)

    # Start DB writer
    writer_task = asyncio.create_task(
        _db_writer(pool, write_buffer, loc_resolver),
        name="pipeline-writer",
    )

    # Wait for monitor producer to finish (either shutdown or exhaustion)
    try:
        await producer_task
    except Exception:
        log.exception("pipeline.producer.crashed")
        # Ensure sentinels are sent even on crash
        for _ in range(num_workers):
            with contextlib.suppress(Exception):
                await fetch_buffer.put(_SENTINEL)

    # Wait for all monitor workers to finish
    worker_results = await asyncio.gather(*worker_tasks, return_exceptions=True)
    total_boards = 0
    for r in worker_results:
        if isinstance(r, int):
            total_boards += r
        elif isinstance(r, Exception):
            log.error("pipeline.worker.crashed", error=str(r))

    # Wait for scrape producer + workers to finish
    total_scrapes = 0
    if scrape_producer_task is not None:
        try:
            await scrape_producer_task
        except Exception:
            log.exception("pipeline.scrape_producer.crashed")
            # Ensure sentinels are sent even on crash
            if scrape_fetch_buffer is not None:
                for _ in range(num_scrape_workers):
                    with contextlib.suppress(Exception):
                        await scrape_fetch_buffer.put(_SENTINEL)

        scrape_results = await asyncio.gather(*scrape_worker_tasks, return_exceptions=True)
        for r in scrape_results:
            if isinstance(r, int):
                total_scrapes += r
            elif isinstance(r, Exception):
                log.error("pipeline.scrape_worker.crashed", error=str(r))

    # Signal DB writer to stop
    await write_buffer.put(_SENTINEL)

    # Wait for writer to drain
    try:
        boards_succeeded, total_new, total_gone, scrapes_ok, scrapes_err = await writer_task
    except Exception:
        log.exception("pipeline.writer.crashed")
        boards_succeeded, total_new, total_gone, scrapes_ok, scrapes_err = 0, 0, 0, 0, 0

    elapsed = monotonic() - t0
    log.info(
        "pipeline.done",
        boards_total=total_boards,
        boards_succeeded=boards_succeeded,
        total_new=total_new,
        total_gone=total_gone,
        scrapes_total=total_scrapes,
        scrapes_succeeded=scrapes_ok,
        scrapes_failed=scrapes_err,
        duration_s=round(elapsed, 2),
        browser=browser,
    )


@dataclass
class _PipelineResult:
    succeeded: int = 0
    durations: list[float] = field(default_factory=list)


async def _monitor_pipeline(
    boards: list[asyncpg.Record],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
) -> _PipelineResult:
    """Process boards for one rate-limit domain serially."""
    result = _PipelineResult()
    for board in boards:
        try:
            ok, elapsed = await _process_one_board(board, pool, http)
            result.durations.append(elapsed)
            if ok:
                result.succeeded += 1
        except Exception:
            log.exception("batch.monitor.pipeline_error", board_id=str(board["id"]))
    return result


async def process_monitor_batch(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int = 200,
    worker_id: str = "w",
) -> BatchResult:
    """Claim due boards and process with domain-parallel pipelines.

    Boards sharing a rate-limit domain (same ATS API or hostname) run
    serially to respect politeness.  Different domains run concurrently.
    """
    t0 = monotonic()
    boards = await pool.fetch(_FETCH_DUE_BOARDS, limit, worker_id)

    if not boards:
        return BatchResult()

    # Group by rate-limit domain
    groups: defaultdict[str, list[asyncpg.Record]] = defaultdict(list)
    for board in boards:
        groups[_throttle_key(board)].append(board)

    log.info("batch.monitor.start", boards=len(boards), domains=len(groups))

    # Run domain pipelines concurrently
    tasks: list[asyncio.Task[_PipelineResult]] = []
    async with asyncio.TaskGroup() as tg:
        for group_boards in groups.values():
            tasks.append(tg.create_task(_monitor_pipeline(group_boards, pool, http)))

    pipeline_results = [t.result() for t in tasks]
    succeeded = sum(r.succeeded for r in pipeline_results)
    all_durations = [d for r in pipeline_results for d in r.durations]
    elapsed = monotonic() - t0

    return BatchResult(
        processed=len(boards),
        succeeded=succeeded,
        failed=len(boards) - succeeded,
        duration_s=round(elapsed, 2),
        slow_items=sum(1 for d in all_durations if d >= _SLOW_MONITOR_SECONDS),
        item_durations=all_durations,
    )


# ── Scrape Batch ─────────────────────────────────────────────────────


_JOBCONTENT_FIELDS = frozenset(f.name for f in __import__("dataclasses").fields(JobContent))


def _merge_fields(primary: JobContent, fallback: JobContent, fields: list[str]) -> JobContent:
    """Create a merged JobContent, taking listed fields from fallback if not None."""
    from dataclasses import replace

    overrides: dict = {}
    for name in fields:
        if name not in _JOBCONTENT_FIELDS:
            log.warning("batch.fallback.unknown_field", field=name)
            continue
        fb_val = getattr(fallback, name)
        if fb_val is not None:
            overrides[name] = fb_val
    return replace(primary, **overrides) if overrides else primary


async def _run_fallback_scraper(
    url: str,
    fb_type: str,
    fb_cfg: dict,
    http: httpx.AsyncClient,
    pw=None,
) -> JobContent:
    """Run a fallback scraper, using parse_html shortcut when possible."""
    scraper_t = get_scraper_type(fb_type)
    if scraper_t and scraper_t.parse_html and not fb_cfg.get("render"):
        resp = await http.get(url, follow_redirects=True)
        resp.raise_for_status()
        content = scraper_t.parse_html(resp.text, fb_cfg)
        enrich_description(content)
        return content
    return await scrape_one(url, fb_type, fb_cfg, http, pw=pw)


def _apply_defaults(content: JobContent, cfg: dict) -> JobContent:
    """Apply constant defaults for fields that are still None after scraping.

    Config example::

        "defaults": {"locations": ["Zurich, Switzerland"], "job_location_type": "onsite"}

    Only fills fields that are ``None`` (or empty list for ``locations``).
    Useful for regional boards where all jobs share a location, or small
    companies with a single office.
    """
    defaults = cfg.get("defaults")
    if not defaults or not isinstance(defaults, dict):
        return content

    for field_name, value in defaults.items():
        if field_name not in _JOBCONTENT_FIELDS:
            log.warning("batch.defaults.unknown_field", field=field_name)
            continue
        current = getattr(content, field_name)
        if current is None or (isinstance(current, list) and not current):
            setattr(content, field_name, value)
    return content


async def _apply_fallback_chain(
    content: JobContent,
    url: str,
    scraper_type: str,
    cfg: dict,
    http: httpx.AsyncClient,
    pw=None,
) -> JobContent:
    """Walk the fallback chain, applying field-level or full-replacement fallbacks."""
    current_cfg = cfg
    while "fallback" in current_cfg:
        fb = current_cfg["fallback"]
        fb_type = fb["type"]
        fb_cfg = fb.get("config") or {}
        fields = fb.get("fields")

        if fields:
            # Field-level merge: always runs
            log.info(
                "batch.scrape.fallback.fields",
                url=url,
                primary=scraper_type,
                fallback=fb_type,
                fields=fields,
            )
            fb_content = await _run_fallback_scraper(url, fb_type, fb_cfg, http, pw=pw)
            content = _merge_fields(content, fb_content, fields)
        elif not content.title:
            # Full replacement: only when primary has no title
            log.info(
                "batch.scrape.fallback",
                url=url,
                primary=scraper_type,
                fallback=fb_type,
            )
            content = await scrape_one(url, fb_type, fb_cfg, http, pw=pw)
        else:
            break

        current_cfg = fb_cfg
    return content


async def _process_one_enrich_scrape(
    item: ScrapeItem,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    scraper_type: str,
    scraper_config: dict | None,
    enrich_fields: list[str],
    pw=None,
) -> tuple[bool, float]:
    """Run a scrape that only enriches specific fields. Returns (success, duration_s)."""
    t0 = monotonic()
    try:
        cfg = scraper_config or {}
        content = await scrape_one(item.url, scraper_type, scraper_config, http, pw=pw)
        content = await _apply_fallback_chain(content, item.url, scraper_type, cfg, http, pw=pw)
        content = _apply_defaults(content, cfg)

        # Normalize before checking — normalize can strip degenerate HTML to None
        content.description = normalize_description_html(content.description)

        # Success check: at least one enriched field is non-empty
        has_data = any(getattr(content, f, None) is not None for f in enrich_fields)
        if not has_data:
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
            return False, monotonic() - t0

        # Detect language if not already set
        language = content.language
        if not language and content.description:
            language = detect_language(content.description)

        # Compute derived columns only for enriched fields
        loc_resolver = await _get_location_resolver(pool)
        tech_id_map = await _get_technology_ids(pool)
        occ_ids = await _get_occupation_ids(pool)
        sen_ids = await _get_seniority_ids(pool)
        rates = await _get_currency_rates(pool)

        # Default all params to None (COALESCE preserves existing)
        norm_emp_type = None
        all_titles = None
        locales = None
        loc_ids = None
        loc_types = None
        desc_pending = None
        meta_pending = None
        tech_ids = None
        s_min = s_max = s_cur = s_per = s_eur = None
        exp_min = exp_max = None
        occ_id = sen_id = None

        if "employment_type" in enrich_fields:
            norm_emp_type = normalize_employment_type(_coerce_text(content.employment_type))

        if "title" in enrich_fields:
            title_text = _coerce_text(content.title)
            all_titles = _build_titles(title_text, None) or None
            occ_id, sen_id = _resolve_occupation_seniority(all_titles, occ_ids, sen_ids)
            # Only overwrite locales if we have real language evidence —
            # _build_locales defaults to ["en"] which would overwrite
            # richer monitor-sourced locale data via COALESCE.
            lang_text = _coerce_text(language)
            if lang_text or content.description:
                detected_langs = (
                    detect_all_languages(content.description) if content.description else []
                )
                built = _build_locales(lang_text, None, detected_languages=detected_langs)
                # Only set if we have data beyond the bare "en" default
                if lang_text or detected_langs:
                    locales = built

        if "locations" in enrich_fields:
            lang_text = _coerce_text(language)
            loc_ids, loc_types = await _resolve_locations(
                loc_resolver,
                _coerce_locations(content.locations),
                _coerce_text(content.job_location_type),
                posting_language=lang_text,
            )

        if "description" in enrich_fields:
            desc_text = _coerce_text(content.description)
            tech_ids = _resolve_technology_ids(desc_text, tech_id_map)
            s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
            exp_min, exp_max = _extract_experience_fields(desc_text)

            # Fetch existing posting data for R2 extras
            existing = await pool.fetchrow(_FETCH_POSTING_FOR_ENRICH, item.job_posting_id)
            r2_title = None
            if existing:
                titles_arr = existing["titles"]
                if titles_arr:
                    r2_title = titles_arr[0]
            r2_title = r2_title or _coerce_text(content.title)
            r2_locations = _coerce_locations(content.locations)

            staged = _stage_r2_pending(
                title=r2_title,
                description=desc_text,
                language=_coerce_text(language),
                locations=r2_locations,
                localizations=None,
                extras=content.extras,
                metadata=content.metadata,
                date_posted=content.date_posted,
                base_salary=content.base_salary,
                employment_type=_coerce_text(content.employment_type),
                job_location_type=_coerce_text(content.job_location_type),
                current_hash=item.description_r2_hash,
                source="scrape",
                tech_ids=tech_ids,
            )
            desc_pending = staged[0] if staged else None
            meta_pending = staged[1] if staged else None
            # Queue cap: skip re-upload staging when queue is full
            if (
                staged
                and item.description_r2_hash is not None
                and await _get_r2_queue_depth(pool) >= settings.r2_queue_max
            ):
                desc_pending = None
                meta_pending = None

        async with pool.acquire() as conn:
            await conn.execute(
                _UPDATE_ENRICH_CONTENT,
                item.job_posting_id,
                norm_emp_type,
                all_titles,
                locales,
                loc_ids,
                loc_types,
                desc_pending,
                meta_pending,
                tech_ids,
                s_min,
                s_max,
                s_cur,
                s_per,
                s_eur,
                exp_min,
                exp_max,
                occ_id,
                sen_id,
            )
            await conn.execute(_RECORD_SCRAPE_SUCCESS, item.job_posting_id)

        await _flush_location_misses(loc_resolver, pool)
        elapsed = monotonic() - t0
        log.debug(
            "batch.enrich.success",
            url=item.url,
            fields=enrich_fields,
            duration_s=round(elapsed, 2),
        )
        return True, elapsed

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        log.error("batch.enrich.error", url=item.url, error=error_msg, duration_s=round(elapsed, 2))
        if _location_resolver is not None:
            _location_resolver.drain_location_misses()
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
        return False, elapsed


async def _process_one_scrape(
    item: ScrapeItem,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    scraper_type: str,
    scraper_config: dict | None,
    pw=None,
) -> tuple[bool, float]:
    """Run a scrape for a single job posting. Returns (success, duration_s)."""
    t0 = monotonic()
    try:
        cfg = scraper_config or {}

        # Early dispatch for enrich-only scrapes
        enrich_fields = cfg.get("enrich")
        if isinstance(enrich_fields, list) and enrich_fields:
            return await _process_one_enrich_scrape(
                item, pool, http, scraper_type, scraper_config, enrich_fields, pw=pw
            )

        content = await scrape_one(item.url, scraper_type, scraper_config, http, pw=pw)

        content = await _apply_fallback_chain(content, item.url, scraper_type, cfg, http, pw=pw)
        content = _apply_defaults(content, cfg)

        if not content.title or _is_garbage_title(content.title):
            # No usable content — record as failure so backoff kicks in.
            if content.title:
                log.info("batch.scrape.garbage_title", url=item.url, title=content.title)
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
            return False, monotonic() - t0

        content.description = normalize_description_html(content.description)

        # Detect language if not already set
        language = content.language
        if not language and content.description:
            language = detect_language(content.description)

        detected_langs = detect_all_languages(content.description) if content.description else []

        title_text = _coerce_text(content.title)
        desc_text = _coerce_text(content.description)
        lang_text = _coerce_text(language)
        raw_emp_type = _coerce_text(content.employment_type)
        norm_emp_type = normalize_employment_type(raw_emp_type)

        # Resolve locations
        loc_resolver = await _get_location_resolver(pool)
        loc_ids, loc_types = await _resolve_locations(
            loc_resolver,
            _coerce_locations(content.locations),
            _coerce_text(content.job_location_type),
            posting_language=lang_text,
        )

        # Resolve technologies from description
        tech_id_map = await _get_technology_ids(pool)
        tech_ids = _resolve_technology_ids(desc_text, tech_id_map)

        # Resolve occupation + seniority from title
        occ_ids = await _get_occupation_ids(pool)
        sen_ids = await _get_seniority_ids(pool)
        occ_id, sen_id = _resolve_occupation_seniority(title_text, occ_ids, sen_ids)

        # Extract salary + experience from description
        rates = await _get_currency_rates(pool)
        s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
        exp_min, exp_max = _extract_experience_fields(desc_text)

        # Stage R2 pending data (pure computation, no I/O)
        staged = _stage_r2_pending(
            title=title_text,
            description=desc_text,
            language=lang_text,
            locations=_coerce_locations(content.locations),
            localizations=None,
            extras=content.extras,
            metadata=content.metadata,
            date_posted=content.date_posted,
            base_salary=content.base_salary,
            employment_type=raw_emp_type,
            job_location_type=_coerce_text(content.job_location_type),
            current_hash=item.description_r2_hash,
            source="scrape",
            tech_ids=tech_ids,
        )
        desc_pending = staged[0] if staged else None
        meta_pending = staged[1] if staged else None
        # Queue cap: skip re-upload staging when queue is full
        if (
            staged
            and item.description_r2_hash is not None
            and await _get_r2_queue_depth(pool) >= settings.r2_queue_max
        ):
            desc_pending = None
            meta_pending = None

        async with pool.acquire() as conn:
            update_result = await conn.execute(
                _UPDATE_JOB_CONTENT,
                item.job_posting_id,
                norm_emp_type,
                _build_titles(title_text, None),
                _build_locales(lang_text, None, detected_languages=detected_langs),
                loc_ids,
                loc_types,
                desc_pending,
                meta_pending,
                tech_ids,
                s_min,
                s_max,
                s_cur,
                s_per,
                s_eur,
                exp_min,
                exp_max,
                occ_id,
                sen_id,
            )
            if _parse_update_count(update_result) != 1:
                raise RuntimeError(f"job_posting_not_found:{item.job_posting_id}")
            await conn.execute(_RECORD_SCRAPE_SUCCESS, item.job_posting_id)

        await _flush_location_misses(loc_resolver, pool)
        elapsed = monotonic() - t0
        log.debug(
            "batch.scrape.success", url=item.url, title=content.title, duration_s=round(elapsed, 2)
        )
        if elapsed >= _SLOW_SCRAPE_SECONDS:
            log.warning("batch.scrape.slow", url=item.url, duration_s=round(elapsed, 2))
        return True, elapsed

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        log.error("batch.scrape.error", url=item.url, error=error_msg, duration_s=round(elapsed, 2))
        if _location_resolver is not None:
            _location_resolver.drain_location_misses()
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
        return False, elapsed


async def _process_one_scrape_insecure(
    item: ScrapeItem,
    pool: asyncpg.Pool,
    scraper_type: str,
    scraper_config: dict | None,
) -> tuple[bool, float]:
    """Wrapper that creates a temporary insecure HTTP client for boards with ssl_verify=False."""
    from src.shared.http import create_http_client

    async with create_http_client(verify=False) as http:
        return await _process_one_scrape(item, pool, http, scraper_type, scraper_config)


async def _scrape_pipeline(
    items: list[ScrapeItem],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_scrapers: dict[str, BoardScraperConfig] | None = None,
) -> _PipelineResult:
    """Process scrape items for one domain serially."""
    # Check if any item in this pipeline needs a browser-based scraper
    need_browser = False
    needs_insecure = False
    for item in items:
        if not board_scrapers or item.board_id not in board_scrapers:
            continue
        bsc = board_scrapers[item.board_id]
        if scraper_needs_browser(bsc.scraper_type, bsc.scraper_config):
            need_browser = True
        if not bsc.ssl_verify:
            needs_insecure = True

    return await _run_scrape_items(items, pool, http, board_scrapers, need_browser, needs_insecure)


async def _run_scrape_items(
    items: list[ScrapeItem],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_scrapers: dict[str, BoardScraperConfig] | None,
    need_browser: bool,
    needs_insecure: bool = False,
) -> _PipelineResult:
    """Inner scrape loop, optionally wrapped in a shared Playwright context."""
    pw = None
    pw_ctx = None
    insecure_http = None

    if need_browser:
        try:
            from playwright.async_api import async_playwright

            pw_ctx = async_playwright()
            pw = await pw_ctx.start()
            log.info("batch.scrape.playwright_started")
        except Exception:
            log.warning("batch.scrape.playwright_unavailable", exc_info=True)

    if needs_insecure:
        from src.shared.http import create_http_client

        insecure_http = create_http_client(verify=False)

    try:
        result = _PipelineResult()
        for item in items:
            try:
                scraper_type = "json-ld"
                scraper_config: dict | None = None
                use_insecure = False
                if board_scrapers and item.board_id in board_scrapers:
                    cfg = board_scrapers[item.board_id]
                    scraper_type = cfg.scraper_type
                    scraper_config = cfg.scraper_config
                    use_insecure = not cfg.ssl_verify

                effective_http = insecure_http if use_insecure and insecure_http else http
                ok, elapsed = await _process_one_scrape(
                    item,
                    pool,
                    effective_http,
                    scraper_type,
                    scraper_config,
                    pw=pw,
                )
                result.durations.append(elapsed)
                if ok:
                    result.succeeded += 1
            except Exception:
                log.exception("batch.scrape.pipeline_error", url=item.url)
        return result
    finally:
        if pw_ctx is not None:
            with contextlib.suppress(Exception):
                await pw_ctx.__aexit__(None, None, None)
        if insecure_http is not None:
            await insecure_http.aclose()


async def process_scrape_batch(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int = 200,
    worker_id: str = "w",
) -> BatchResult:
    """Claim due job postings from Postgres and scrape with domain-parallel pipelines.

    Items targeting the same hostname run serially (respecting per-domain
    throttle).  Different hostnames run concurrently.
    """
    rows = await pool.fetch(_FETCH_DUE_JOB_POSTINGS, limit, worker_id)

    if not rows:
        return BatchResult()

    all_items = [
        ScrapeItem(
            job_posting_id=str(row["id"]),
            url=row["source_url"],
            board_id=str(row["board_id"]) if row["board_id"] else "",
            description_r2_hash=int(row["description_r2_hash"])
            if row["description_r2_hash"] is not None
            else None,
        )
        for row in rows
    ]

    board_ids = {item.board_id for item in all_items if item.board_id}
    info = await _load_board_scrapers(pool, board_ids)

    # Clear next_scrape_at for postings from rich monitors
    rich_posting_ids = [
        item.job_posting_id for item in all_items if item.board_id in info.rich_board_ids
    ]
    if rich_posting_ids:
        await pool.execute(_CLEAR_SCRAPE_FOR_RICH, rich_posting_ids)
        log.info("batch.scrape.cleared_rich", count=len(rich_posting_ids))

    # Filter out rich-monitor postings
    items = [item for item in all_items if item.board_id not in info.rich_board_ids]

    if not items:
        return BatchResult()

    # Group by scrape domain
    groups: defaultdict[str, list[ScrapeItem]] = defaultdict(list)
    for item in items:
        domain = urlparse(item.url).hostname or "unknown"
        groups[domain].append(item)

    log.info("batch.scrape.start", items=len(items), domains=len(groups))

    # Run domain pipelines concurrently
    t0 = monotonic()
    tasks: list[asyncio.Task[_PipelineResult]] = []
    async with asyncio.TaskGroup() as tg:
        for group_items in groups.values():
            tasks.append(tg.create_task(_scrape_pipeline(group_items, pool, http, info.scrapers)))

    pipeline_results = [t.result() for t in tasks]
    succeeded = sum(r.succeeded for r in pipeline_results)
    all_durations = [d for r in pipeline_results for d in r.durations]
    elapsed = monotonic() - t0

    return BatchResult(
        processed=len(items),
        succeeded=succeeded,
        failed=len(items) - succeeded,
        duration_s=round(elapsed, 2),
        slow_items=sum(1 for d in all_durations if d >= _SLOW_SCRAPE_SECONDS),
        item_durations=all_durations,
    )


# ── Single Board ──────────────────────────────────────────────────────

_FETCH_BOARD_BY_SLUG = """
SELECT * FROM job_board WHERE board_slug = $1
"""

_FETCH_BOARD_SCRAPE_ITEMS = """
SELECT id, source_url, board_id,
       split_part(split_part(source_url, '://', 2), '/', 1) AS scrape_domain,
       description_r2_hash
FROM job_posting
WHERE board_id = $1
  AND is_active = true
  AND next_scrape_at IS NOT NULL
  AND next_scrape_at <= now()
"""

_FETCH_BOARD_ALL_ACTIVE = """
SELECT id, source_url, board_id,
       split_part(split_part(source_url, '://', 2), '/', 1) AS scrape_domain,
       description_r2_hash
FROM job_posting
WHERE board_id = $1
  AND is_active = true
"""


async def dry_run_single_board(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_slug: str,
    *,
    verbose: bool = False,
    scrape_limit: int = 3,
    pw=None,
) -> None:
    """Dry-run a single board: monitor + scrape without any DB writes.

    Runs monitor_one() to discover jobs, then scrape_one() on a sample of URLs
    to show what the scraper would produce.  Useful for testing config changes.

    When *pw* is provided, Playwright is available for monitors/scrapers that
    require browser rendering (e.g. replay-mode api_sniffer, rendered nextdata).
    """
    from dataclasses import fields as dc_fields

    board = await pool.fetchrow(_FETCH_BOARD_BY_SLUG, board_slug)
    if not board:
        log.error("dry_run.not_found", board_slug=board_slug)
        return
    crawler_type = board["crawler_type"]
    metadata = _parse_metadata(board["metadata"])
    enrich_fields = _board_has_enrich(metadata)

    log.info(
        "dry_run.start",
        board_slug=board_slug,
        crawler_type=crawler_type,
        enrich=enrich_fields or "(none)",
    )

    # ── Monitor ──────────────────────────────────────────────────────
    result = await monitor_one(board["board_url"], crawler_type, metadata, http, pw=pw)

    is_rich = result.jobs_by_url is not None
    log.info(
        "dry_run.monitor.done",
        urls=len(result.urls),
        rich=is_rich,
        enrich=enrich_fields or "(none)",
    )

    if not result.urls:
        log.warning("dry_run.monitor.empty")
        return

    if is_rich and verbose:
        sample_url = next(iter(result.urls))
        job = result.jobs_by_url[sample_url]
        log.info("dry_run.monitor.sample_url", url=sample_url)
        for f in dc_fields(job):
            val = getattr(job, f.name)
            if val is not None:
                display = val
                if f.name == "description" and isinstance(val, str) and len(val) > 200:
                    display = val[:200] + "…"
                log.info("dry_run.monitor.field", field=f.name, value=display)
            else:
                log.info("dry_run.monitor.field", field=f.name, value="(null)")

    if is_rich and enrich_fields:
        # Show which fields the monitor provides vs what enrich will fill
        sample_url = next(iter(result.urls))
        job = result.jobs_by_url[sample_url]
        provided = [f.name for f in dc_fields(job) if getattr(job, f.name) is not None]
        missing = [f.name for f in dc_fields(job) if getattr(job, f.name) is None]
        log.info("dry_run.monitor.field_coverage", provided=provided, missing=missing)

    # ── Scraper ──────────────────────────────────────────────────────
    # Determine scraper settings (same logic as _load_board_scrapers)
    explicit_scraper = metadata.get("scraper_type")
    scraper_config = metadata.get("scraper_config")
    if not isinstance(scraper_config, dict):
        scraper_config = None

    if not explicit_scraper or explicit_scraper == "skip":
        if enrich_fields:
            scraper_type = "json-ld"
        else:
            from src.workspace._compat import auto_scraper_type

            auto = auto_scraper_type(crawler_type, metadata)
            if auto and auto[0] != "skip":
                scraper_type = auto[0]
                scraper_config = scraper_config or auto[1]
            elif auto and auto[0] == "skip":
                log.info("dry_run.scraper.skip", reason="rich monitor, no enrich configured")
                return
            else:
                scraper_type = "json-ld"
    else:
        scraper_type = explicit_scraper

    log.info(
        "dry_run.scraper.config",
        scraper_type=scraper_type,
        scraper_config=scraper_config,
        enrich=enrich_fields or "(none)",
    )

    # Pick sample URLs for scraping
    sample_urls = list(result.urls)[:scrape_limit]
    log.info("dry_run.scraper.start", sample_size=len(sample_urls), total=len(result.urls))

    cfg = scraper_config or {}
    for url in sample_urls:
        try:
            content = await scrape_one(url, scraper_type, scraper_config, http, pw=pw)
            content = await _apply_fallback_chain(content, url, scraper_type, cfg, http, pw=pw)
            content = _apply_defaults(content, cfg)
            content.description = normalize_description_html(content.description)

            if enrich_fields:
                has_data = any(getattr(content, f, None) is not None for f in enrich_fields)
                status = "ok" if has_data else "EMPTY (would fail)"
            elif content.title:
                status = "ok"
            else:
                status = "EMPTY (no title)"

            log.info(
                "dry_run.scraper.result",
                url=url,
                status=status,
                title=content.title,
                description_len=len(content.description) if content.description else 0,
                locations=content.locations,
                employment_type=content.employment_type,
            )

            if verbose:
                for f in dc_fields(content):
                    val = getattr(content, f.name)
                    if val is not None:
                        display = val
                        if f.name == "description" and isinstance(val, str) and len(val) > 300:
                            display = val[:300] + "…"
                        log.info("dry_run.scraper.field", url=url, field=f.name, value=display)
                    else:
                        log.info("dry_run.scraper.field", url=url, field=f.name, value="(null)")

        except Exception as exc:
            log.error("dry_run.scraper.error", url=url, error=_error_message(exc))

    log.info("dry_run.complete", board_slug=board_slug)


async def run_single_board(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_slug: str,
    *,
    force_rescrape: bool = False,
) -> None:
    """Process a single board end-to-end: monitor then scrape.

    Bypasses scheduling — fetches the board directly by slug and processes
    all due scrape items for that board after the monitor run.
    When *force_rescrape* is True, scrapes all active jobs regardless of schedule.
    """
    board = await pool.fetchrow(_FETCH_BOARD_BY_SLUG, board_slug)
    if not board:
        log.error("single_board.not_found", board_slug=board_slug)
        return

    board_id = str(board["id"])
    log.info("single_board.monitor.start", board_slug=board_slug, board_id=board_id)

    # Monitor — use streaming path for streaming monitors
    stream_fn = get_stream_fn(board["crawler_type"])
    if stream_fn is not None:
        extender = DeadlineExtender()
        _ok, monitor_duration = await _process_one_board_streaming(board, pool, http, extender)
    else:
        _ok, monitor_duration = await _process_one_board(board, pool, http)
    log.info(
        "single_board.monitor.done", board_slug=board_slug, duration_s=round(monitor_duration, 2)
    )

    # Scrape items for this board
    query = _FETCH_BOARD_ALL_ACTIVE if force_rescrape else _FETCH_BOARD_SCRAPE_ITEMS
    rows = await pool.fetch(query, board["id"])
    if not rows:
        log.info("single_board.scrape.none_due", board_slug=board_slug)
        return

    items = [
        ScrapeItem(
            job_posting_id=str(row["id"]),
            url=row["source_url"],
            board_id=board_id,
            description_r2_hash=int(row["description_r2_hash"])
            if row["description_r2_hash"] is not None
            else None,
        )
        for row in rows
    ]

    info = await _load_board_scrapers(pool, {board_id})

    if board_id in info.rich_board_ids:
        log.info("single_board.scrape.skip_rich", board_slug=board_slug)
        return

    groups: defaultdict[str, list[ScrapeItem]] = defaultdict(list)
    for item, row in zip(items, rows, strict=True):
        domain = row["scrape_domain"] or urlparse(item.url).hostname or "unknown"
        groups[domain].append(item)

    log.info("single_board.scrape.start", board_slug=board_slug, items=len(items))

    t0 = monotonic()
    tasks: list[asyncio.Task[_PipelineResult]] = []
    async with asyncio.TaskGroup() as tg:
        for group_items in groups.values():
            tasks.append(tg.create_task(_scrape_pipeline(group_items, pool, http, info.scrapers)))

    pipeline_results = [t.result() for t in tasks]
    succeeded = sum(r.succeeded for r in pipeline_results)
    failed = len(items) - succeeded
    scrape_duration = monotonic() - t0
    log.info(
        "single_board.complete",
        board_slug=board_slug,
        scraped=len(items),
        succeeded=succeeded,
        failed=failed,
        scrape_duration_s=round(scrape_duration, 2),
    )
