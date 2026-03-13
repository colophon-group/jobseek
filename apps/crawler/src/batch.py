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
import gc
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

from src.core.description_store import content_hash, upload_description, upload_posting
from src.core.enum_normalize import normalize_employment_type
from src.core.location_resolve import LocationResolver
from src.core.monitor import monitor_one
from src.core.monitors import api_monitor_types
from src.core.scrape import scrape_one
from src.core.scrapers import enrich_description, get_scraper
from src.shared.html_normalize import normalize_description_html
from src.shared.langdetect import detect_language
from src.shared.redis import get_redis

log = structlog.get_logger()


# ── Constants ────────────────────────────────────────────────────────

# API monitor types share a single API host per type (throttle-domain keys).
_API_MONITOR_TYPES = api_monitor_types()

# Lazy-loaded location resolver singleton
_location_resolver: LocationResolver | None = None

# Max R2 backfill uploads per board run (touched postings without hashes).
# Prevents huge first-time runs from timing out. Backfill completes incrementally.
_R2_BACKFILL_LIMIT = 500

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
        log.info("batch.location_resolver.loaded", entries=len(_location_resolver._entries))
    return _location_resolver


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

    # DB fallback for non-core locale names (rare path)
    if await resolver.backfill_misses():
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

_INSERT_RICH_JOB = """
INSERT INTO job_posting
    (company_id, board_id,
     employment_type, source_url,
     first_seen_at, last_seen_at,
     is_active, titles, locales,
     location_ids, location_types)
VALUES ($1, $2, $3, $4,
        now(), now(),
        true, $5, $6,
        $7, $8)
RETURNING id
"""

_CREATE_RICH_UPDATES_TEMP = """
CREATE TEMP TABLE _rich_updates (
    id uuid,
    employment_type text,
    titles text[], locales text[],
    location_ids integer[], location_types text[]
) ON COMMIT DROP
"""

_BATCH_UPDATE_RICH_CONTENT = """
UPDATE job_posting AS jp
SET employment_type = u.employment_type,
    titles = u.titles, locales = u.locales,
    location_ids = u.location_ids, location_types = u.location_types
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
    description_r2_hash = $7,
    to_be_enriched = CASE
        WHEN description_r2_hash IS DISTINCT FROM $7 THEN true
        ELSE to_be_enriched
    END
WHERE id = $1
"""

_SET_R2_HASH = """
UPDATE job_posting
SET description_r2_hash = $2, to_be_enriched = true
WHERE id = $1::uuid
"""

_FETCH_DUE_JOB_POSTINGS = """
WITH candidates AS (
    SELECT id, split_part(split_part(source_url, '://', 2), '/', 1) AS domain,
           next_scrape_at,
           (titles = '{}')::int AS needs_initial_scrape
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


def _build_locales(language: str | None, localizations: dict | None) -> list[str]:
    """Build locales array from primary language + localization keys."""
    locales: list[str] = []
    primary = language or "en"
    locales.append(primary)
    if localizations and isinstance(localizations, dict):
        for locale in localizations:
            if locale not in locales:
                locales.append(locale)
    return locales


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
        dt = _coerce_datetime(date_posted)
        if dt is not None:
            merged["date_posted"] = dt.isoformat()
    if base_salary is not None:
        merged["base_salary"] = base_salary
    if employment_type is not None:
        merged["raw_employment_type"] = employment_type
    if job_location_type is not None:
        merged["raw_job_location_type"] = job_location_type
    return merged


def _compute_r2_hash(description: str | None, merged_extras: dict) -> int:
    """Compute a combined hash of all R2-bound content."""
    parts = description or ""
    if merged_extras:
        parts += "\0" + json.dumps(merged_extras, sort_keys=True, ensure_ascii=False)
    return content_hash(parts)


async def _upload_to_r2(
    posting_id: str,
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
) -> int | None:
    """Upload description and merged extras to R2. Best-effort, errors are logged.

    Returns the new description_r2_hash (caller should persist in DB),
    or None if no description was provided.
    """
    if not description:
        return None

    try:
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

        # Fast path: nothing changed — skip all R2 API calls
        if current_hash is not None and current_hash == new_hash:
            return new_hash

        # Upload primary description + extras (tracks diffs in history)
        await asyncio.to_thread(upload_posting, posting_id, locale, description, merged)

        # Upload localizations (secondary locales, description only)
        if localizations and isinstance(localizations, dict):
            for loc_locale, loc_data in localizations.items():
                if loc_locale == locale:
                    continue
                loc_desc = None
                if isinstance(loc_data, dict):
                    loc_desc = loc_data.get("description")
                elif isinstance(loc_data, str):
                    loc_desc = loc_data
                if loc_desc:
                    await asyncio.to_thread(upload_description, posting_id, loc_locale, loc_desc)

        return new_hash
    except Exception:
        log.exception("batch.r2_upload.error", posting_id=posting_id)
        return None


@dataclass
class BoardScraperConfig:
    """Scraper settings for a board (fallback chain lives inside scraper_config)."""

    scraper_type: str
    scraper_config: dict | None


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

        # Determine scraper: explicit > auto-configured > default (json-ld)
        if not explicit_scraper:
            # Check if monitor auto-configures a scraper
            from src.workspace._compat import auto_scraper_type

            auto = auto_scraper_type(crawler_type, metadata)
            if auto and auto[0] == "skip":
                rich_board_ids.add(board_id)
                continue
            scraper_type = auto[0] if auto else "json-ld"
            auto_config = auto[1] if auto else None
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


@dataclass
class WorkItem:
    """A single unit of work for the continuous worker pool."""

    domain: str
    kind: str  # "monitor" | "scrape"
    run: Callable[[], Awaitable[tuple[bool, float]]]
    id: str = ""  # board ID or posting ID — used for lease release


# ── Claim Queries (Worker Pool) ──────────────────────────────────────

_CLAIM_MONITORS = """
WITH ranked AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY throttle_key
           ORDER BY next_check_at, id
         ) AS domain_rank
  FROM job_board
  WHERE is_enabled = true
    AND board_status IN ('active', 'suspect')
    AND next_check_at <= now()
    AND (leased_until IS NULL OR leased_until < now())
    AND throttle_key != ALL($3::text[])
),
picked AS (
  SELECT id
  FROM ranked
  ORDER BY domain_rank, id
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
    SELECT id, split_part(split_part(source_url, '://', 2), '/', 1) AS domain,
           next_scrape_at,
           (titles = '{}')::int AS needs_initial_scrape
    FROM job_posting
    WHERE is_active = true
      AND next_scrape_at IS NOT NULL
      AND next_scrape_at <= now()
      AND (leased_until IS NULL OR leased_until < now())
      AND split_part(split_part(source_url, '://', 2), '/', 1) != ALL($3::text[])
    FOR UPDATE SKIP LOCKED
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
) -> list[WorkItem]:
    """Claim due boards (interleaved across domains) and return WorkItems."""
    if limit <= 0:
        return []

    rows = await pool.fetch(_CLAIM_MONITORS, limit, worker_id, exclude_domains or [])
    items: list[WorkItem] = []
    for board in rows:
        domain = board["throttle_key"]
        items.append(
            WorkItem(
                domain=domain,
                kind="monitor",
                run=functools.partial(_process_one_board, board, pool, http),
                id=str(board["id"]),
            )
        )
    return items


async def claim_scrape_work(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int,
    worker_id: str,
    exclude_domains: list[str] | None = None,
) -> list[WorkItem]:
    """Claim due job postings (interleaved across domains) and return WorkItems."""
    if limit <= 0:
        return []

    rows = await pool.fetch(_CLAIM_SCRAPES, limit, worker_id, exclude_domains or [])
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
        if board_id and board_id in info.scrapers:
            cfg = info.scrapers[board_id]
            scraper_type = cfg.scraper_type
            scraper_config = cfg.scraper_config

        r2_hash = row["description_r2_hash"]
        item = ScrapeItem(
            job_posting_id=str(row["id"]),
            url=row["source_url"],
            board_id=board_id,
            description_r2_hash=int(r2_hash) if r2_hash is not None else None,
        )
        items.append(
            WorkItem(
                domain=domain,
                kind="scrape",
                run=functools.partial(
                    _process_one_scrape,
                    item,
                    pool,
                    http,
                    scraper_type,
                    scraper_config,
                ),
                id=str(row["id"]),
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

        result = await monitor_one(board_url, crawler_type, metadata, http)

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

        # Collect R2 work to run after DB transaction
        r2_work: list[tuple[str, dict, int | None]] = []

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
            rows = await conn.fetch(
                _DIFF_URLS,
                list(result.urls),
                board_id,
                delist_threshold,
                is_rich,
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

                if new_jobs:
                    for j in new_jobs:
                        loc_ids, loc_types = await _resolve_locations(
                            loc_resolver,
                            _coerce_locations(j.locations),
                            _coerce_text(j.job_location_type),
                            _coerce_text(j.language),
                        )
                        row = await conn.fetchrow(
                            _INSERT_RICH_JOB,
                            company_id,
                            board_id,
                            normalize_employment_type(_coerce_text(j.employment_type)),
                            j.url,
                            _build_titles(_coerce_text(j.title), j.localizations),
                            _build_locales(_coerce_text(j.language), j.localizations),
                            loc_ids,
                            loc_types,
                        )
                        if row:
                            r2_work.append(
                                (
                                    str(row["id"]),
                                    dict(
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
                                    ),
                                    None,
                                )
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
                        records.append(
                            (
                                pid,
                                normalize_employment_type(_coerce_text(j.employment_type)),
                                _build_titles(_coerce_text(j.title), j.localizations),
                                _build_locales(_coerce_text(j.language), j.localizations),
                                loc_ids,
                                loc_types,
                            )
                        )
                    await conn.copy_records_to_table(
                        "_rich_updates",
                        records=records,
                    )
                    await conn.execute(_BATCH_UPDATE_RICH_CONTENT)

                    # R2 work for updated postings:
                    # - With existing hash: always check for content changes
                    # - Without hash (backfill): cap to avoid overwhelming R2
                    backfill_count = 0
                    for pid, j, existing_hash in update_triples:
                        if existing_hash is None:
                            backfill_count += 1
                            if backfill_count > _R2_BACKFILL_LIMIT:
                                continue
                        r2_work.append(
                            (
                                str(pid),
                                dict(
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
                                ),
                                existing_hash,
                            )
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

        # R2 uploads after transaction — concurrent to avoid timeout for large boards
        if r2_work:
            r2_semaphore = asyncio.Semaphore(20)

            async def _do_upload(
                pid: str, kw: dict, cur_hash: int | None
            ) -> tuple[str, int] | None:
                async with r2_semaphore:
                    new_hash = await _upload_to_r2(pid, current_hash=cur_hash, **kw)
                    if new_hash is not None and new_hash != cur_hash:
                        return (pid, new_hash)
                    return None

            results = await asyncio.gather(
                *[_do_upload(pid, kw, eh) for pid, kw, eh in r2_work],
                return_exceptions=True,
            )
            hashes_to_persist = [r for r in results if isinstance(r, tuple)]
            r2_errors = sum(1 for r in results if isinstance(r, Exception))
            if r2_errors:
                board_log.warning("batch.r2_upload.failures", count=r2_errors, total=len(r2_work))
            if hashes_to_persist:
                async with pool.acquire() as conn:
                    for posting_id, new_hash in hashes_to_persist:
                        await conn.execute(_SET_R2_HASH, posting_id, new_hash)

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

        # Free large temporaries (jobs_by_url, r2_work) before next item
        del result
        gc.collect()

        return True, elapsed

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        board_log.exception("batch.monitor.error", error=error_msg, duration_s=round(elapsed, 2))
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_FAILURE, board_id, error_msg)
        return False, elapsed


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


async def _process_one_scrape(
    item: ScrapeItem,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    scraper_type: str,
    scraper_config: dict | None,
) -> tuple[bool, float]:
    """Run a scrape for a single job posting. Returns (success, duration_s)."""
    t0 = monotonic()
    try:
        cfg = scraper_config or {}
        content = await scrape_one(item.url, scraper_type, scraper_config, http)

        # Walk the fallback chain embedded in scraper_config
        current_cfg = cfg
        while not content.title and "fallback" in current_cfg:
            fb = current_cfg["fallback"]
            fb_type = fb["type"]
            fb_cfg = fb.get("config") or {}
            log.info(
                "batch.scrape.fallback",
                url=item.url,
                primary=scraper_type,
                fallback=fb_type,
            )
            content = await scrape_one(item.url, fb_type, fb_cfg, http)
            current_cfg = fb_cfg

        if not content.title or _is_garbage_title(content.title):
            # No usable content — record success (don't retry) but skip DB/R2 writes.
            # The posting stays as a URL stub with empty title/description.
            if content.title:
                log.info("batch.scrape.garbage_title", url=item.url, title=content.title)
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_SUCCESS, item.job_posting_id)
            return True, monotonic() - t0

        content.description = normalize_description_html(content.description)

        # Detect language if not already set
        language = content.language
        if not language and content.description:
            language = detect_language(content.description)

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

        # R2 upload (best-effort, before DB write)
        new_r2_hash = await _upload_to_r2(
            item.job_posting_id,
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
        )

        async with pool.acquire() as conn:
            update_result = await conn.execute(
                _UPDATE_JOB_CONTENT,
                item.job_posting_id,
                norm_emp_type,
                _build_titles(title_text, None),
                _build_locales(lang_text, None),
                loc_ids,
                loc_types,
                new_r2_hash,
            )

        if _parse_update_count(update_result) != 1:
            raise RuntimeError(f"job_posting_not_found:{item.job_posting_id}")

        async with pool.acquire() as conn:
            await conn.execute(_RECORD_SCRAPE_SUCCESS, item.job_posting_id)
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
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
        return False, elapsed


async def _scrape_pipeline(
    items: list[ScrapeItem],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_scrapers: dict[str, BoardScraperConfig] | None = None,
) -> _PipelineResult:
    """Process scrape items for one domain serially."""
    result = _PipelineResult()
    for item in items:
        try:
            scraper_type = "json-ld"
            scraper_config: dict | None = None
            if board_scrapers and item.board_id in board_scrapers:
                cfg = board_scrapers[item.board_id]
                scraper_type = cfg.scraper_type
                scraper_config = cfg.scraper_config

            ok, elapsed = await _process_one_scrape(
                item,
                pool,
                http,
                scraper_type,
                scraper_config,
            )
            result.durations.append(elapsed)
            if ok:
                result.succeeded += 1
        except Exception:
            log.exception("batch.scrape.pipeline_error", url=item.url)
    return result


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

    # Monitor
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
