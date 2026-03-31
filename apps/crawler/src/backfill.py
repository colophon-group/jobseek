"""Backfill location_ids for jobs scraped while GeoNames tables were empty.

Enqueues re-scrape tasks into Redis at low priority (tier 2).  The existing
scrape pipeline handles location resolution.  R2 uploads are avoided because
``description_r2_hash`` is passed through, so ``_stage_r2_pending`` skips
unchanged descriptions.

Usage::

    uv run crawler backfill-locations
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import asyncpg
import structlog

from src.redis_queue import enqueue_scrape, get_redis

log = structlog.get_logger()

_FETCH_MISSING_LOCATIONS = """
SELECT jp.id::text, jp.source_url, jp.board_id::text, jp.description_r2_hash
FROM job_posting jp
WHERE jp.is_active = true
  AND jp.location_ids IS NULL
  AND jp.description_r2_hash IS NOT NULL
"""


async def backfill_locations(pool: asyncpg.Pool) -> int:
    """Enqueue re-scrapes for active jobs missing location_ids.

    Returns the number of tasks enqueued.
    """
    rows = await pool.fetch(_FETCH_MISSING_LOCATIONS)
    if not rows:
        log.info("backfill.locations.none_needed")
        return 0

    log.info("backfill.locations.found", count=len(rows))

    r = get_redis()

    # Cache board configs to avoid repeated Redis lookups
    board_cache: dict[str, bool] = {}  # board_id -> needs_browser
    enqueued = 0
    now = time.time()

    for row in rows:
        posting_id = row["id"]
        url = row["source_url"]
        board_id = row["board_id"] or ""
        r2_hash = row["description_r2_hash"]
        domain = urlparse(url).hostname or ""

        # Determine if scraper needs browser from cached board config
        if board_id and board_id not in board_cache:
            board_config = await r.hgetall(f"board:{board_id}")
            board_cache[board_id] = (
                board_config.get("scraper_needs_browser", "0") == "1" if board_config else False
            )
        needs_browser = board_cache.get(board_id, False)

        added = await enqueue_scrape(
            domain,
            posting_id,
            now,  # due immediately
            {
                "source_url": url,
                "board_id": board_id,
                "description_r2_hash": str(r2_hash) if r2_hash is not None else "",
                "scrape_step": "0",
            },
            browser=needs_browser,
            first_time=False,  # tier 2 = lowest priority
        )
        if added:
            enqueued += 1

    log.info("backfill.locations.enqueued", enqueued=enqueued, skipped=len(rows) - enqueued)
    return enqueued
