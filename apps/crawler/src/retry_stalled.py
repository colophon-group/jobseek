"""Operator CLI for transient-3-strike scrape recovery (#2738).

PR #2732 introduced a three-class scrape failure classification
(``permanent_gone`` / ``budget_eligible`` / ``transient``) and the
worker self-heal in ``pipeline._process_scrape_work`` honours
``next_scrape_at IS NULL`` as a hard short-circuit. The transient class
backs off via ``next_scrape_at = NULL`` after 3 consecutive failures.

For a posting that hits 3 transient failures (90-min upstream 5xx blip,
network glitch, etc.) while the upstream listing keeps citing it
continuously, the monitor's ``relisted`` CTE never fires (it requires
the URL to be NOT currently listed, then re-discovered). The posting
stays ``is_active=true`` with ``next_scrape_at=NULL`` indefinitely. Web
users still see the data (last successful scrape), but it's frozen at
that snapshot.

This CLI resets ``next_scrape_at`` to ``now()`` for postings stuck in
that state for at least ``--max-age-days`` days, and enqueues scrape
tasks in Redis so the existing worker picks them up. Mirrors the
``crawler backfill-locations`` pattern: small batched UPDATE-RETURNING
transactions (no long-held locks), idempotent across re-runs.

Usage::

    uv run crawler retry-stalled-scrapes [--max-age-days N] [--board-slug SLUG] [--dry-run]

The default ``--max-age-days`` is 7.

The query targets ``scrape_failures >= 3`` specifically — that's the
transient-3-strike state. Postings on boards with
``rescrape_policy = "never"`` (Starbucks, Uber, every paid-proxy board)
also have ``next_scrape_at IS NULL`` after a successful scrape, but
their ``scrape_failures = 0``, so they're not affected.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import asyncpg
import structlog

from src.redis_queue import enqueue_scrape, get_redis

log = structlog.get_logger()

#: Batch size for the UPDATE-RETURNING loop. Same as ``backfill_locations``
#: — keeps each transaction short-lived so concurrent ``crawler sync`` and
#: exporter writes aren't blocked.
_RETRY_BATCH_SIZE = 5000

#: Default ``--max-age-days`` when no value is supplied. Seven days
#: balances "give the upstream blip a chance to clear" with "don't let
#: postings rot past relevance".
_DEFAULT_MAX_AGE_DAYS = 7

# UPDATE-RETURNING the stalled candidates in batch. ``ORDER BY id LIMIT``
# pairs with the WHERE shrinking each iteration (UPDATE flips
# next_scrape_at to non-NULL).
#
# Termination invariant: one UTC cutoff is captured before the loop and
# supplied to every statement. A worker that re-scrapes a promoted row stamps
# ``last_scraped_at`` at or after that cutoff, so even if the row immediately
# stalls again it cannot re-enter this invocation. This is essential for
# ``--max-age-days 0``; using each statement's later ``now()`` would let a
# fast failure satisfy ``last_scraped_at < now()`` again.
#
# ``scrape_failures >= 3`` distinguishes transient-3-strike stall from
# a successful scrape on a ``rescrape_policy=never`` board (which also
# leaves ``next_scrape_at IS NULL`` but with ``scrape_failures = 0``
# because ``_RECORD_SCRAPE_SUCCESS`` resets the counter).
_PROMOTE_STALLED_BATCH = """
WITH targets AS (
    SELECT jp.id FROM job_posting jp
    WHERE jp.is_active = true
      AND jp.next_scrape_at IS NULL
      AND jp.scrape_failures >= 3
      AND jp.last_scraped_at IS NOT NULL
      AND jp.last_scraped_at < $2::timestamptz - ($1::int * interval '1 day')
    ORDER BY jp.id
    LIMIT $3
)
UPDATE job_posting jp
SET next_scrape_at = now()
FROM targets t
WHERE jp.id = t.id
RETURNING jp.id::text, jp.source_url, jp.board_id::text, jp.description_r2_hash
"""

# Board-scoped recovery must begin with the tiny exact-slug board set, then
# reach postings through the existing board index. Keeping the scope behind an
# OR parameter made PostgreSQL choose a parallel sequential scan of the full
# posting table in production (#9167), taking 21s for the dry-run and timing
# out the UPDATE at 30s. MATERIALIZED preserves this join order even when
# production statistics underestimate the stalled rows for a board.
_PROMOTE_STALLED_BY_BOARD_BATCH = """
WITH scoped_boards AS MATERIALIZED (
    SELECT id FROM job_board WHERE board_slug = ANY($2::text[])
),
targets AS (
    SELECT jp.id
    FROM scoped_boards sb
    JOIN job_posting jp ON jp.board_id = sb.id
    WHERE jp.is_active = true
      AND jp.next_scrape_at IS NULL
      AND jp.scrape_failures >= 3
      AND jp.last_scraped_at IS NOT NULL
      AND jp.last_scraped_at < $3::timestamptz - ($1::int * interval '1 day')
    ORDER BY jp.id
    LIMIT $4
)
UPDATE job_posting jp
SET next_scrape_at = now()
FROM targets t
WHERE jp.id = t.id
RETURNING jp.id::text, jp.source_url, jp.board_id::text, jp.description_r2_hash
"""

# Read-only count for ``--dry-run``. Same predicates as the UPDATE
# above — a dry-run reports exactly what the next non-dry-run would
# touch (modulo concurrent writes between the two invocations).
_COUNT_STALLED = """
SELECT count(*) FROM job_posting jp
WHERE jp.is_active = true
  AND jp.next_scrape_at IS NULL
  AND jp.scrape_failures >= 3
  AND jp.last_scraped_at IS NOT NULL
  AND jp.last_scraped_at < $2::timestamptz - ($1::int * interval '1 day')
"""

_COUNT_STALLED_BY_BOARD = """
WITH scoped_boards AS MATERIALIZED (
    SELECT id FROM job_board WHERE board_slug = ANY($2::text[])
)
SELECT count(*)
FROM scoped_boards sb
JOIN job_posting jp ON jp.board_id = sb.id
WHERE jp.is_active = true
  AND jp.next_scrape_at IS NULL
  AND jp.scrape_failures >= 3
  AND jp.last_scraped_at IS NOT NULL
  AND jp.last_scraped_at < $3::timestamptz - ($1::int * interval '1 day')
"""


async def count_stalled_scrapes(
    pool: asyncpg.Pool,
    max_age_days: int,
    *,
    board_slugs: list[str] | None = None,
) -> int:
    """Return the count of postings that match the stalled-scrape criteria.

    Used by ``--dry-run`` to report how many rows would be affected
    without making any writes.
    """
    recovery_cutoff = datetime.now(UTC)
    if board_slugs is None:
        return await pool.fetchval(_COUNT_STALLED, max_age_days, recovery_cutoff)
    return await pool.fetchval(
        _COUNT_STALLED_BY_BOARD,
        max_age_days,
        board_slugs,
        recovery_cutoff,
    )


async def retry_stalled_scrapes(
    pool: asyncpg.Pool,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    *,
    board_slugs: list[str] | None = None,
) -> int:
    """Reset ``next_scrape_at`` and enqueue scrapes for stalled postings.

    Returns the number of tasks enqueued in Redis. The Lua
    ``enqueue_scrape`` is dedup-safe, so a posting already in the queue
    from a prior invocation contributes 0 to the count.
    """
    r = get_redis()
    board_cache: dict[str, bool] = {}  # board_id -> needs_browser
    recovery_cutoff = datetime.now(UTC)

    enqueued = 0

    # Loop until the WHERE matches no rows. Each iteration's UPDATE
    # flips ``next_scrape_at`` to non-NULL on the targets, dropping
    # them out of the WHERE for subsequent iterations.
    while True:
        if board_slugs is None:
            rows = await pool.fetch(
                _PROMOTE_STALLED_BATCH,
                max_age_days,
                recovery_cutoff,
                _RETRY_BATCH_SIZE,
            )
        else:
            rows = await pool.fetch(
                _PROMOTE_STALLED_BY_BOARD_BATCH,
                max_age_days,
                board_slugs,
                recovery_cutoff,
                _RETRY_BATCH_SIZE,
            )
        if not rows:
            break
        log.info("retry_stalled.batch", count=len(rows))

        now = time.time()
        for row in rows:
            posting_id = row["id"]
            url = row["source_url"]
            board_id = row["board_id"] or ""
            r2_hash = row["description_r2_hash"]
            domain = urlparse(url).hostname or ""

            if board_id and board_id not in board_cache:
                board_config = await r.hgetall(f"board:{board_id}")
                board_cache[board_id] = (
                    board_config.get("scraper_needs_browser", "0") == "1" if board_config else False
                )
            needs_browser = board_cache.get(board_id, False)

            added = await enqueue_scrape(
                domain,
                posting_id,
                now,
                {
                    "source_url": url,
                    "board_id": board_id,
                    "description_r2_hash": (str(r2_hash) if r2_hash is not None else ""),
                    "scrape_step": "0",
                },
                browser=needs_browser,
                first_time=False,  # tier 2 = lowest priority
            )
            if added:
                enqueued += 1

    if enqueued == 0:
        log.info(
            "retry_stalled.none_needed",
            max_age_days=max_age_days,
            board_slugs=board_slugs,
        )
    else:
        log.info(
            "retry_stalled.enqueued",
            enqueued=enqueued,
            max_age_days=max_age_days,
            board_slugs=board_slugs,
        )
    return enqueued
