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
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import asyncpg
import httpx
import structlog

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

_DIFF_URLS = """
WITH discovered AS (
  SELECT unnest($1::text[]) AS url
),
touched AS (
  UPDATE job_posting
  SET last_seen_at = now(), missing_count = 0
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.status = 'active'
    AND job_posting.source_url = d.url
  RETURNING job_posting.source_url
),
relisted AS (
  UPDATE job_posting
  SET status = 'active', delisted_at = NULL, delist_reason = NULL,
      relisted_at = now(), missing_count = 0,
      last_seen_at = now(), updated_at = now()
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.status = 'delisted'
    AND job_posting.source_url = d.url
  RETURNING job_posting.id, job_posting.source_url
),
gone AS (
  UPDATE job_posting
  SET missing_count = missing_count + 1,
      status = CASE
          WHEN missing_count + 1 >= $3 THEN 'delisted'
          ELSE status
      END,
      delisted_at = CASE
          WHEN missing_count + 1 >= $3 THEN now()
          ELSE delisted_at
      END,
      delist_reason = CASE
          WHEN missing_count + 1 >= $3 THEN 'missing_from_board'
          ELSE delist_reason
      END,
      next_scrape_at = CASE
          WHEN missing_count + 1 >= $3 THEN NULL
          ELSE next_scrape_at
      END,
      updated_at = now()
  WHERE job_posting.board_id = $2
    AND job_posting.status = 'active'
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
SELECT 'relisted' AS action, id::text, source_url AS url FROM relisted
UNION ALL
SELECT 'gone', id::text, source_url FROM gone
UNION ALL
SELECT 'new', NULL, url FROM new_urls
"""

# Delist threshold: API monitors are authoritative (1 miss = delist),
# URL-only monitors are fragile (2 misses before delist).
_DELIST_THRESHOLD_AUTHORITATIVE = 1
_DELIST_THRESHOLD_FRAGILE = 2

_DELIST_BOARD_POSTINGS = """
UPDATE job_posting
SET status = 'delisted', delisted_at = now(),
    delist_reason = 'board_gone', next_scrape_at = NULL,
    updated_at = now()
WHERE board_id = $1 AND status = 'active'
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
    (company_id, board_id, title, description, locations,
     employment_type, job_location_type, base_salary,
     date_posted, language, localizations, extras, metadata,
     source_url, status, first_seen_at, last_seen_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
        $14, 'active', now(), now())
"""

_UPDATE_RELISTED_CONTENT = """
UPDATE job_posting
SET title = $2, description = $3, locations = $4,
    employment_type = $5, job_location_type = $6, base_salary = $7,
    date_posted = $8, language = $9, localizations = $10,
    extras = $11, metadata = $12, updated_at = now()
WHERE id = $1
"""

_INSERT_URL_ONLY_JOBS = """
INSERT INTO job_posting (company_id, board_id, source_url, status,
                         first_seen_at, last_seen_at, next_scrape_at, scrape_domain)
SELECT $1, $2, u.url, 'active', now(), now(), now(),
       split_part(split_part(u.url, '://', 2), '/', 1)
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
SET title = $2, description = $3, locations = $4,
    employment_type = $5, job_location_type = $6, base_salary = $7,
    date_posted = $8, language = $9, extras = $10,
    metadata = $11, updated_at = now()
WHERE id = $1
"""

_FETCH_DUE_JOB_POSTINGS = """
UPDATE job_posting
SET lease_owner   = $2,
    leased_until  = now() + interval '10 minutes'
WHERE id IN (
    SELECT id FROM job_posting
    WHERE status = 'active'
      AND next_scrape_at IS NOT NULL
      AND next_scrape_at <= now()
      AND (leased_until IS NULL OR leased_until < now())
    ORDER BY next_scrape_at
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
RETURNING id, source_url, board_id
"""

_RECORD_SCRAPE_SUCCESS = """
UPDATE job_posting
SET scrape_failures  = 0,
    last_scrape_error = NULL,
    last_scraped_at  = now(),
    next_scrape_at   = now() + (scrape_interval_hours || ' hours')::interval,
    lease_owner      = NULL,
    leased_until     = NULL,
    updated_at       = now()
WHERE id = $1
"""

_RECORD_SCRAPE_FAILURE = """
UPDATE job_posting
SET scrape_failures   = scrape_failures + 1,
    last_scrape_error = $2,
    last_scraped_at   = now(),
    next_scrape_at    = CASE
        WHEN scrape_failures + 1 >= 3 THEN NULL
        ELSE now() + (30 * pow(2, scrape_failures)) * interval '1 minute'
    END,
    lease_owner  = NULL,
    leased_until = NULL,
    updated_at   = now()
WHERE id = $1
"""

_FETCH_BOARD_SCRAPERS = """
SELECT id::text AS id, metadata
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


async def _load_board_scrapers(
    pool: asyncpg.Pool,
    board_ids: set[str],
) -> dict[str, tuple[str, dict | None]]:
    """Load scraper type/config by board id from job_board metadata."""
    if not board_ids:
        return {}

    rows = await pool.fetch(_FETCH_BOARD_SCRAPERS, list(board_ids))
    resolved: dict[str, tuple[str, dict | None]] = {}

    for row in rows:
        board_id = row["id"]
        metadata = _parse_metadata(row["metadata"])
        scraper_type = metadata.get("scraper_type") or "json-ld"
        scraper_config = metadata.get("scraper_config")
        if not isinstance(scraper_config, dict):
            scraper_config = None

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

        resolved[board_id] = (scraper_type, scraper_config)

    return resolved


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


@dataclass
class BatchResult:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0


# ── Monitor Batch ────────────────────────────────────────────────────


async def _process_one_board(
    board: asyncpg.Record,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
) -> bool:
    """Run a full monitor cycle for a single board."""
    board_id = str(board["id"])
    company_id = str(board["company_id"])
    board_url = board["board_url"]
    crawler_type = board["crawler_type"]

    board_log = log.bind(board_id=board_id, board_url=board_url, crawler_type=crawler_type)

    try:
        # Build monitor config from board metadata
        metadata = board["metadata"] if board["metadata"] else {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        result = await monitor_one(board_url, crawler_type, metadata, http)

        if not result.urls:
            board_log.warning("batch.monitor.empty")
            async with pool.acquire() as conn:
                rows = await conn.fetch(_RECORD_EMPTY_CHECK, board_id)
                # If board transitioned to 'gone', delist all its active postings
                if rows and rows[0]["board_status"] == "gone":
                    await conn.execute(_DELIST_BOARD_POSTINGS, board_id)
                    board_log.warning("batch.monitor.board_gone")
            return True

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
            rows = await conn.fetch(
                _DIFF_URLS,
                list(result.urls),
                board_id,
                delist_threshold,
            )

            new_urls: list[str] = []
            relisted: list[dict] = []
            gone: list[dict] = []

            for row in rows:
                action = row["action"]
                if action == "new":
                    new_urls.append(row["url"])
                elif action == "relisted":
                    relisted.append({"id": row["id"], "url": row["url"]})
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

                if new_jobs:
                    await conn.executemany(
                        _INSERT_RICH_JOB,
                        [
                            (
                                company_id,
                                board_id,
                                _coerce_text(j.title),
                                _coerce_text(j.description),
                                _coerce_locations(j.locations),
                                _coerce_text(j.employment_type),
                                _coerce_text(j.job_location_type),
                                _jsonb(j.base_salary),
                                _coerce_datetime(j.date_posted),
                                _coerce_text(j.language),
                                _jsonb(j.localizations),
                                _jsonb(j.extras),
                                _jsonb(j.metadata),
                                j.url,
                            )
                            for j in new_jobs
                        ],
                    )
                relisted_pairs = [
                    (item["id"], result.jobs_by_url[item["url"]])
                    for item in relisted
                    if item["url"] in result.jobs_by_url
                ]
                if relisted_pairs:
                    # Enrich descriptions + detect language for relisted jobs too
                    for _, j in relisted_pairs:
                        j.description = normalize_description_html(j.description)
                        enrich_description(j)
                        if not j.language and j.description:
                            j.language = detect_language(j.description)

                    await conn.executemany(
                        _UPDATE_RELISTED_CONTENT,
                        [
                            (
                                pid,
                                _coerce_text(j.title),
                                _coerce_text(j.description),
                                _coerce_locations(j.locations),
                                _coerce_text(j.employment_type),
                                _coerce_text(j.job_location_type),
                                _jsonb(j.base_salary),
                                _coerce_datetime(j.date_posted),
                                _coerce_text(j.language),
                                _jsonb(j.localizations),
                                _jsonb(j.extras),
                                _jsonb(j.metadata),
                            )
                            for pid, j in relisted_pairs
                        ],
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

        board_log.info(
            "batch.monitor.success",
            discovered=len(result.urls),
            new=len(new_urls),
            relisted=len(relisted),
            gone=len(gone),
        )

        # Invalidate stats cache when job counts change
        if new_urls or gone:
            with contextlib.suppress(Exception):
                await get_redis().delete("cache:platform-stats")
        return True

    except Exception as exc:
        error_msg = _error_message(exc)
        board_log.exception("batch.monitor.error", error=error_msg)
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_FAILURE, board_id, error_msg)
        return False


async def _monitor_pipeline(
    boards: list[asyncpg.Record],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
) -> int:
    """Process boards for one rate-limit domain serially.

    Returns count of boards that completed without error.
    """
    succeeded = 0
    for board in boards:
        try:
            if await _process_one_board(board, pool, http):
                succeeded += 1
        except Exception:
            log.exception("batch.monitor.pipeline_error", board_id=str(board["id"]))
    return succeeded


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
    boards = await pool.fetch(_FETCH_DUE_BOARDS, limit, worker_id)

    if not boards:
        return BatchResult()

    # Group by rate-limit domain
    groups: defaultdict[str, list[asyncpg.Record]] = defaultdict(list)
    for board in boards:
        groups[_throttle_key(board)].append(board)

    log.info("batch.monitor.start", boards=len(boards), domains=len(groups))

    # Run domain pipelines concurrently
    tasks: list[asyncio.Task[int]] = []
    async with asyncio.TaskGroup() as tg:
        for group_boards in groups.values():
            tasks.append(tg.create_task(_monitor_pipeline(group_boards, pool, http)))

    succeeded = sum(t.result() for t in tasks)
    return BatchResult(processed=len(boards), succeeded=succeeded, failed=len(boards) - succeeded)


# ── Scrape Batch ─────────────────────────────────────────────────────


async def _process_one_scrape(
    item: ScrapeItem,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    scraper_type: str,
    scraper_config: dict | None,
) -> bool:
    """Run a scrape for a single job posting. Returns True on success."""
    try:
        content = await scrape_one(item.url, scraper_type, scraper_config, http)
        content.description = normalize_description_html(content.description)

        # Detect language if not already set
        language = content.language
        if not language and content.description:
            language = detect_language(content.description)

        async with pool.acquire() as conn:
            update_result = await conn.execute(
                _UPDATE_JOB_CONTENT,
                item.job_posting_id,
                _coerce_text(content.title),
                _coerce_text(content.description),
                _coerce_locations(content.locations),
                _coerce_text(content.employment_type),
                _coerce_text(content.job_location_type),
                _jsonb(content.base_salary),
                _coerce_datetime(content.date_posted),
                _coerce_text(language),
                _jsonb(content.extras),
                _jsonb(content.metadata),
            )

        if _parse_update_count(update_result) != 1:
            raise RuntimeError(f"job_posting_not_found:{item.job_posting_id}")

        async with pool.acquire() as conn:
            await conn.execute(_RECORD_SCRAPE_SUCCESS, item.job_posting_id)
        log.debug("batch.scrape.success", url=item.url, title=content.title)
        return True

    except Exception as exc:
        error_msg = _error_message(exc)
        log.error("batch.scrape.error", url=item.url, error=error_msg)
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id, error_msg)
        return False


async def _scrape_pipeline(
    items: list[ScrapeItem],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_scrapers: dict[str, tuple[str, dict | None]] | None = None,
) -> int:
    """Process scrape items for one domain serially.

    Returns count of items that completed successfully.
    """
    succeeded = 0
    for item in items:
        try:
            scraper_type = "json-ld"
            scraper_config: dict | None = None
            if board_scrapers and item.board_id in board_scrapers:
                scraper_type, scraper_config = board_scrapers[item.board_id]

            if await _process_one_scrape(item, pool, http, scraper_type, scraper_config):
                succeeded += 1
        except Exception:
            log.exception("batch.scrape.pipeline_error", url=item.url)
    return succeeded


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

    items = [
        ScrapeItem(
            job_posting_id=str(row["id"]),
            url=row["source_url"],
            board_id=str(row["board_id"]) if row["board_id"] else "",
        )
        for row in rows
    ]

    board_ids = {item.board_id for item in items if item.board_id}
    board_scrapers = await _load_board_scrapers(pool, board_ids)

    # Group by target hostname
    groups: defaultdict[str, list[ScrapeItem]] = defaultdict(list)
    for item in items:
        host = urlparse(item.url).hostname or "unknown"
        groups[host].append(item)

    log.info("batch.scrape.start", items=len(items), domains=len(groups))

    # Run domain pipelines concurrently
    tasks: list[asyncio.Task[int]] = []
    async with asyncio.TaskGroup() as tg:
        for group_items in groups.values():
            tasks.append(tg.create_task(_scrape_pipeline(group_items, pool, http, board_scrapers)))

    succeeded = sum(t.result() for t in tasks)
    return BatchResult(processed=len(items), succeeded=succeeded, failed=len(items) - succeeded)
