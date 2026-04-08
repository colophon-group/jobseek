"""One-shot backfill: clear ``next_scrape_at`` for rich-monitor postings.

Drains the stuck-scrape cohort described in
``dev/browser-errors/01-rich-monitor-scheduling.md``. Postings whose board
has ``metadata.scraper_type = 'skip'`` (without enrichment) must never have
``next_scrape_at`` set, or the scheduler keeps feeding the placeholder
``skip`` scraper which raises ``RuntimeError("skip scraper called …")``.

The update is idempotent — run once after deploying the preventative fixes.

Usage::

    cd apps/crawler
    LOCAL_DATABASE_URL="postgresql://crawler:<pwd>@<host>:5432/crawler" \\
    uv run python ../../scripts/backfill-clear-rich-scrape.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg
import structlog

log = structlog.get_logger()


_COUNT_STUCK = """
SELECT jb.crawler_type,
       count(*) AS cnt
FROM job_posting jp
JOIN job_board jb ON jp.board_id = jb.id
WHERE jp.next_scrape_at IS NOT NULL
  AND jb.metadata->>'scraper_type' = 'skip'
  -- COALESCE guards the NULL scraper_config case: ``? 'enrich'`` returns
  -- NULL when scraper_config is NULL, and NOT NULL is NULL (not TRUE).
  AND NOT COALESCE(jb.metadata->'scraper_config' ? 'enrich', false)
GROUP BY jb.crawler_type
ORDER BY cnt DESC
"""


_CLEAR_STUCK = """
WITH rich_boards AS (
    SELECT id
    FROM job_board
    WHERE metadata->>'scraper_type' = 'skip'
      AND NOT COALESCE(metadata->'scraper_config' ? 'enrich', false)
)
UPDATE job_posting
SET next_scrape_at = NULL,
    leased_until   = NULL,
    scrape_failures = 0,
    updated_at     = now()
WHERE board_id IN (SELECT id FROM rich_boards)
  AND next_scrape_at IS NOT NULL
"""


async def _run(dry_run: bool) -> None:
    dsn = os.environ.get("LOCAL_DATABASE_URL")
    if not dsn:
        log.error("backfill.no_dsn", hint="set LOCAL_DATABASE_URL")
        sys.exit(1)

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(_COUNT_STUCK)
        if not rows:
            log.info("backfill.nothing_to_do")
            return

        total = 0
        for row in rows:
            log.info("backfill.stuck", crawler_type=row["crawler_type"], count=row["cnt"])
            total += row["cnt"]
        log.info("backfill.total_stuck", count=total)

        if dry_run:
            log.info("backfill.dry_run", hint="re-run without --dry-run to apply")
            return

        async with conn.transaction():
            result = await conn.execute(_CLEAR_STUCK)
        # asyncpg returns "UPDATE n"
        updated = int(result.rsplit(" ", 1)[-1]) if result.startswith("UPDATE") else 0
        log.info("backfill.cleared", count=updated)
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report stuck postings without modifying them.",
    )
    args = parser.parse_args()
    asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
