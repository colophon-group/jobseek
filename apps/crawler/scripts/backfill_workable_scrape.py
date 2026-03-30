"""Backfill next_scrape_at for active Workable postings.

After converting the Workable monitor from N+1 (rich) to URL-only,
existing active postings need next_scrape_at set so the scraper picks
them up on the daily schedule.

Usage:
  uv run python scripts/backfill_workable_scrape.py --dry-run
  uv run python scripts/backfill_workable_scrape.py
"""

from __future__ import annotations

import argparse
import asyncio

import asyncpg
import structlog

from src.config import settings

log = structlog.get_logger()

_COUNT = """
SELECT count(*) FROM job_posting
WHERE is_active = true AND next_scrape_at IS NULL
  AND board_id IN (SELECT id FROM job_board WHERE crawler_type = 'workable')
"""

_BACKFILL = """
UPDATE job_posting SET next_scrape_at = now()
WHERE is_active = true AND next_scrape_at IS NULL
  AND board_id IN (SELECT id FROM job_board WHERE crawler_type = 'workable')
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill next_scrape_at for active Workable postings"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report count without changes")
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    try:
        count = await conn.fetchval(_COUNT)
        log.info("backfill.preview", active_workable_null_scrape=count)

        if args.dry_run:
            log.info("dry_run.complete")
            return 0

        if count == 0:
            log.info("backfill.nothing_to_do")
            return 0

        result = await conn.execute(_BACKFILL)
        log.info("backfill.done", result=result)

        remaining = await conn.fetchval(_COUNT)
        if remaining > 0:
            log.error("postcondition.failed", null_next_scrape=remaining)
            return 1

        log.info("backfill.complete")
    finally:
        await conn.close()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
