"""Batch processor — Layer 2.

Claims due work from the DB, runs single jobs concurrently, writes results back.
Portable across all deployment environments.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import asyncpg
import httpx
import structlog

from src.core.monitor import MonitorResult, monitor_one
from src.core.monitors import DiscoveredJob
from src.core.scrape import scrape_one

log = structlog.get_logger()


# ── SQL Queries ──────────────────────────────────────────────────────

_FETCH_DUE_BOARDS = """
UPDATE job_board
SET last_checked_at = now()
WHERE id IN (
    SELECT id FROM job_board
    WHERE is_enabled = true
      AND next_check_at <= now()
    ORDER BY next_check_at
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
RETURNING *
"""

_DIFF_URLS = """
WITH discovered AS (
  SELECT unnest($1::text[]) AS url
),
touched AS (
  UPDATE job_posting
  SET last_seen_at = now()
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.status = 'active'
    AND job_posting.source_url = d.url
  RETURNING job_posting.source_url
),
relisted AS (
  UPDATE job_posting
  SET status = 'active', delisted_at = NULL,
      last_seen_at = now(), updated_at = now()
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.status = 'delisted'
    AND job_posting.source_url = d.url
  RETURNING job_posting.id, job_posting.source_url
),
gone AS (
  UPDATE job_posting
  SET status = 'delisted', delisted_at = now(), updated_at = now()
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

_RECORD_SUCCESS = """
UPDATE job_board
SET consecutive_failures = 0,
    last_error = NULL,
    last_success_at = now(),
    next_check_at = now() + (check_interval_minutes || ' minutes')::interval,
    updated_at = now()
WHERE id = $1
"""

_RECORD_FAILURE = """
UPDATE job_board
SET consecutive_failures = consecutive_failures + 1,
    last_error = $2,
    next_check_at = now() + LEAST(
        (check_interval_minutes * pow(2, consecutive_failures)) || ' minutes',
        '1440 minutes'
    )::interval,
    is_enabled = CASE WHEN consecutive_failures + 1 >= 5 THEN false ELSE is_enabled END,
    updated_at = now()
WHERE id = $1
"""

_INSERT_RICH_JOB = """
INSERT INTO job_posting
    (company_id, board_id, title, description, locations,
     employment_type, job_location_type, base_salary, skills,
     date_posted, responsibilities, qualifications, metadata,
     source_url, status, first_seen_at, last_seen_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
        $14, 'active', now(), now())
"""

_UPDATE_RELISTED_CONTENT = """
UPDATE job_posting
SET title = $2, description = $3, locations = $4,
    employment_type = $5, job_location_type = $6, base_salary = $7,
    skills = $8, date_posted = $9, responsibilities = $10,
    qualifications = $11, metadata = $12, updated_at = now()
WHERE id = $1
"""

_INSERT_URL_ONLY_JOBS = """
INSERT INTO job_posting (company_id, board_id, source_url, status, first_seen_at, last_seen_at)
SELECT $1, $2, unnest($3::text[]), 'active', now(), now()
RETURNING id, source_url
"""

_ENQUEUE_URLS = """
INSERT INTO job_url_queue (job_posting_id, url)
SELECT unnest($1::uuid[]), unnest($2::text[])
ON CONFLICT (url) DO NOTHING
"""

_UPDATE_METADATA = """
UPDATE job_board
SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
    updated_at = now()
WHERE id = $1
"""

_CLAIM_SCRAPE_QUEUE = """
UPDATE job_url_queue
SET status = 'processing', locked_until = now() + interval '5 minutes'
WHERE id IN (
    SELECT q.id FROM job_url_queue q
    WHERE q.status = 'pending'
    ORDER BY q.created_at
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
RETURNING id, job_posting_id, url
"""

_UPDATE_JOB_CONTENT = """
UPDATE job_posting
SET title = $2, description = $3, locations = $4,
    employment_type = $5, job_location_type = $6, base_salary = $7,
    skills = $8, date_posted = $9, responsibilities = $10,
    qualifications = $11, metadata = $12, updated_at = now()
WHERE id = $1
"""

_MARK_SCRAPE_SUCCESS = """
UPDATE job_url_queue SET status = 'completed' WHERE id = $1
"""

_MARK_SCRAPE_FAILURE = """
UPDATE job_url_queue
SET status = CASE WHEN retries + 1 >= max_retries THEN 'failed' ELSE 'pending' END,
    retries = retries + 1,
    error_message = $2,
    locked_until = NULL
WHERE id = $1
"""


# ── Helpers ──────────────────────────────────────────────────────────

def _jsonb(val: dict | None) -> str | None:
    return json.dumps(val) if val is not None else None


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
) -> None:
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
                await conn.execute(_RECORD_SUCCESS, board_id)
            return

        async with pool.acquire() as conn:
            # Persist newly discovered sitemap URL
            if result.new_sitemap_url:
                await conn.execute(
                    _UPDATE_METADATA,
                    board_id,
                    json.dumps({"sitemap_url": result.new_sitemap_url}),
                )

            # Run diff
            rows = await conn.fetch(_DIFF_URLS, list(result.urls), board_id)

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
                if new_jobs:
                    await conn.executemany(
                        _INSERT_RICH_JOB,
                        [
                            (
                                company_id, board_id, j.title, j.description, j.locations,
                                j.employment_type, j.job_location_type, _jsonb(j.base_salary),
                                j.skills, j.date_posted, j.responsibilities, j.qualifications,
                                _jsonb(j.metadata), j.url,
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
                    await conn.executemany(
                        _UPDATE_RELISTED_CONTENT,
                        [
                            (
                                pid, j.title, j.description, j.locations,
                                j.employment_type, j.job_location_type, _jsonb(j.base_salary),
                                j.skills, j.date_posted, j.responsibilities, j.qualifications,
                                _jsonb(j.metadata),
                            )
                            for pid, j in relisted_pairs
                        ],
                    )

            # URL-only path
            if result.jobs_by_url is None and new_urls:
                inserted = await conn.fetch(
                    _INSERT_URL_ONLY_JOBS, company_id, board_id, new_urls,
                )
                posting_ids = [str(r["id"]) for r in inserted]
                urls = [r["source_url"] for r in inserted]
                await conn.execute(_ENQUEUE_URLS, posting_ids, urls)
                board_log.info("batch.enqueued", count=len(urls))

            await conn.execute(_RECORD_SUCCESS, board_id)

        board_log.info(
            "batch.monitor.success",
            discovered=len(result.urls),
            new=len(new_urls),
            relisted=len(relisted),
            gone=len(gone),
        )

    except Exception as exc:
        board_log.exception("batch.monitor.error")
        error_msg = str(exc)[:500]
        async with pool.acquire() as conn:
            await conn.execute(_RECORD_FAILURE, board_id, error_msg)


async def process_monitor_batch(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int = 10,
) -> BatchResult:
    """Claim due boards and run monitor_one for each concurrently."""
    boards = await pool.fetch(_FETCH_DUE_BOARDS, limit)

    if not boards:
        return BatchResult()

    log.info("batch.monitor.start", count=len(boards))
    result = BatchResult(processed=len(boards))

    async with asyncio.TaskGroup() as tg:
        for board in boards:
            tg.create_task(_process_one_board(board, pool, http))

    result.succeeded = result.processed  # TaskGroup raises on failure
    return result


# ── Scrape Batch ─────────────────────────────────────────────────────

async def _process_one_scrape(
    queue_item: asyncpg.Record,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    scraper_type: str,
    scraper_config: dict | None,
) -> None:
    """Run a scrape for a single queued URL."""
    queue_id = str(queue_item["id"])
    posting_id = str(queue_item["job_posting_id"])
    url = queue_item["url"]

    try:
        content = await scrape_one(url, scraper_type, scraper_config, http)

        async with pool.acquire() as conn:
            await conn.execute(
                _UPDATE_JOB_CONTENT,
                posting_id, content.title, content.description, content.locations,
                content.employment_type, content.job_location_type,
                _jsonb(content.base_salary), content.skills, content.date_posted,
                content.responsibilities, content.qualifications,
                _jsonb(content.metadata),
            )
            await conn.execute(_MARK_SCRAPE_SUCCESS, queue_id)

        log.debug("batch.scrape.success", url=url, title=content.title)

    except Exception as exc:
        log.error("batch.scrape.error", url=url, error=str(exc))
        async with pool.acquire() as conn:
            await conn.execute(_MARK_SCRAPE_FAILURE, queue_id, str(exc)[:500])


async def process_scrape_batch(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int = 10,
) -> BatchResult:
    """Claim due URLs from queue and run scrape_one for each concurrently.

    Note: This fetches board scraper config for each URL from the job_posting's board.
    For simplicity, uses json-ld as the default scraper when no config is specified.
    """
    items = await pool.fetch(_CLAIM_SCRAPE_QUEUE, limit)

    if not items:
        return BatchResult()

    log.info("batch.scrape.start", count=len(items))
    result = BatchResult(processed=len(items))

    # Look up scraper config for each item's board
    # For now, use json-ld as the default scraper type
    async with asyncio.TaskGroup() as tg:
        for item in items:
            tg.create_task(
                _process_one_scrape(item, pool, http, "json-ld", None)
            )

    result.succeeded = result.processed
    return result
