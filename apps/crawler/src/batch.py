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
from urllib.parse import urlparse

import asyncpg
import httpx
import structlog

from src.core.monitor import monitor_one
from src.core.monitors import api_monitor_types
from src.core.scrape import scrape_one
from src.core.scrapers import enrich_description
from src.shared.dedup import filter_unseen, mark_seen
from src.shared.langdetect import detect_language
from src.shared.queue import QueueItem, dequeue, enqueue, recover_stale, requeue_retries
from src.shared.queue import complete as queue_complete
from src.shared.queue import fail as queue_fail
from src.shared.redis import get_redis

log = structlog.get_logger()


# ── Constants ────────────────────────────────────────────────────────

# API monitor types share a single API host per type (throttle-domain keys).
_API_MONITOR_TYPES = api_monitor_types()


# ── SQL Queries ──────────────────────────────────────────────────────

_FETCH_DUE_BOARDS = """
UPDATE job_board
SET last_checked_at = now(),
    next_check_at = now() + (check_interval_minutes || ' minutes')::interval
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
        (5 * pow(2, consecutive_failures)) || ' minutes',
        '1440 minutes'
    )::interval,
    is_enabled = CASE WHEN consecutive_failures + 1 >= 5 THEN false ELSE is_enabled END,
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
INSERT INTO job_posting (company_id, board_id, source_url, status, first_seen_at, last_seen_at)
SELECT $1, $2, unnest($3::text[]), 'active', now(), now()
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


# ── Helpers ──────────────────────────────────────────────────────────


def _jsonb(val: dict | None) -> str | None:
    return json.dumps(val) if val is not None else None


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

        async with pool.acquire() as conn, conn.transaction():
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

                # Enrich descriptions + detect language for jobs that don't already have it
                for j in new_jobs:
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
                                j.title,
                                j.description,
                                j.locations,
                                j.employment_type,
                                j.job_location_type,
                                _jsonb(j.base_salary),
                                j.date_posted,
                                j.language,
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
                        enrich_description(j)
                        if not j.language and j.description:
                            j.language = detect_language(j.description)

                    await conn.executemany(
                        _UPDATE_RELISTED_CONTENT,
                        [
                            (
                                pid,
                                j.title,
                                j.description,
                                j.locations,
                                j.employment_type,
                                j.job_location_type,
                                _jsonb(j.base_salary),
                                j.date_posted,
                                j.language,
                                _jsonb(j.localizations),
                                _jsonb(j.extras),
                                _jsonb(j.metadata),
                            )
                            for pid, j in relisted_pairs
                        ],
                    )

            # URL-only path — insert stubs in Postgres, enqueue to Redis
            if result.jobs_by_url is None and new_urls:
                # Filter out already-seen URLs (cross-instance dedup)
                with contextlib.suppress(Exception):
                    new_urls = await filter_unseen(new_urls)

                if new_urls:
                    inserted = await conn.fetch(
                        _INSERT_URL_ONLY_JOBS,
                        company_id,
                        board_id,
                        new_urls,
                    )
                    queue_items = [
                        QueueItem(
                            job_posting_id=str(r["id"]),
                            url=r["source_url"],
                            board_id=board_id,
                        )
                        for r in inserted
                    ]
                    await enqueue(queue_items)
                    with contextlib.suppress(Exception):
                        await mark_seen([r["source_url"] for r in inserted])
                    board_log.info("batch.enqueued", count=len(queue_items))

            await conn.execute(_RECORD_SUCCESS, board_id)

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

    except Exception as exc:
        board_log.exception("batch.monitor.error")
        error_msg = str(exc)[:500]
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_FAILURE, board_id, error_msg)


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
            await _process_one_board(board, pool, http)
            succeeded += 1
        except Exception:
            log.exception("batch.monitor.pipeline_error", board_id=str(board["id"]))
    return succeeded


async def process_monitor_batch(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int = 200,
) -> BatchResult:
    """Claim due boards and process with domain-parallel pipelines.

    Boards sharing a rate-limit domain (same ATS API or hostname) run
    serially to respect politeness.  Different domains run concurrently.
    """
    boards = await pool.fetch(_FETCH_DUE_BOARDS, limit)

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
    item: QueueItem,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    scraper_type: str,
    scraper_config: dict | None,
) -> bool:
    """Run a scrape for a single queued URL. Returns True on success."""
    try:
        content = await scrape_one(item.url, scraper_type, scraper_config, http)

        # Detect language if not already set
        language = content.language
        if not language and content.description:
            language = detect_language(content.description)

        async with pool.acquire() as conn:
            await conn.execute(
                _UPDATE_JOB_CONTENT,
                item.job_posting_id,
                content.title,
                content.description,
                content.locations,
                content.employment_type,
                content.job_location_type,
                _jsonb(content.base_salary),
                content.date_posted,
                language,
                _jsonb(content.extras),
                _jsonb(content.metadata),
            )

        await queue_complete(item)
        log.debug("batch.scrape.success", url=item.url, title=content.title)
        return True

    except Exception as exc:
        log.error("batch.scrape.error", url=item.url, error=str(exc))
        with contextlib.suppress(Exception):
            await queue_fail(item, str(exc)[:500])
        return False


async def _scrape_pipeline(
    items: list[QueueItem],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
) -> int:
    """Process scrape items for one domain serially.

    Returns count of items that completed successfully.
    """
    succeeded = 0
    for item in items:
        try:
            if await _process_one_scrape(item, pool, http, "json-ld", None):
                succeeded += 1
        except Exception:
            log.exception("batch.scrape.pipeline_error", url=item.url)
    return succeeded


async def process_scrape_batch(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int = 200,
) -> BatchResult:
    """Claim URLs from Redis queue and scrape with domain-parallel pipelines.

    Items targeting the same hostname run serially (respecting per-domain
    throttle).  Different hostnames run concurrently.
    """
    # Move due retries back to the main queue
    await requeue_retries()
    # Recover items stuck beyond visibility timeout
    await recover_stale()

    items = await dequeue(limit)

    if not items:
        return BatchResult()

    # Group by target hostname
    groups: defaultdict[str, list[QueueItem]] = defaultdict(list)
    for item in items:
        host = urlparse(item.url).hostname or "unknown"
        groups[host].append(item)

    log.info("batch.scrape.start", items=len(items), domains=len(groups))

    # Run domain pipelines concurrently
    tasks: list[asyncio.Task[int]] = []
    async with asyncio.TaskGroup() as tg:
        for group_items in groups.values():
            tasks.append(tg.create_task(_scrape_pipeline(group_items, pool, http)))

    succeeded = sum(t.result() for t in tasks)
    return BatchResult(processed=len(items), succeeded=succeeded, failed=len(items) - succeeded)
