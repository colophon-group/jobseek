"""Normalize SmartRecruiters URLs + backfill next_scrape_at.

After converting the SmartRecruiters monitor from N+1 (rich) to URL-only,
existing source_url values need the SEO slug stripped and next_scrape_at set.

SmartRecruiters posting IDs are numeric (15+ digits). Existing URLs look like:
  https://jobs.smartrecruiters.com/Token/743999106810286-senior-software-engineer
After normalization:
  https://jobs.smartrecruiters.com/Token/743999106810286

Usage:
  uv run python scripts/backfill_smartrecruiters_scrape.py --dry-run
  uv run python scripts/backfill_smartrecruiters_scrape.py
"""

from __future__ import annotations

import argparse
import asyncio

import asyncpg
import structlog

from src.config import settings

log = structlog.get_logger()

_COUNT_URLS = """
SELECT count(*) FROM job_posting
WHERE board_id IN (SELECT id FROM job_board WHERE crawler_type = 'smartrecruiters')
  AND source_url ~ '/\\d{10,}-[^/]+$'
"""

_NORMALIZE_URLS = """
UPDATE job_posting
SET source_url = regexp_replace(source_url, '(/(\\d{10,}))-[^/]+$', '\\1')
WHERE board_id IN (SELECT id FROM job_board WHERE crawler_type = 'smartrecruiters')
  AND source_url ~ '/\\d{10,}-[^/]+$'
"""

_COUNT_SCRAPE = """
SELECT count(*) FROM job_posting
WHERE is_active = true AND next_scrape_at IS NULL
  AND board_id IN (SELECT id FROM job_board WHERE crawler_type = 'smartrecruiters')
"""

_BACKFILL_SCRAPE = """
UPDATE job_posting SET next_scrape_at = now()
WHERE is_active = true AND next_scrape_at IS NULL
  AND board_id IN (SELECT id FROM job_board WHERE crawler_type = 'smartrecruiters')
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize SmartRecruiters URLs + backfill next_scrape_at"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report counts without changes")
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    try:
        # 1. Normalize URLs
        url_count = await conn.fetchval(_COUNT_URLS)
        log.info("normalize.preview", urls_to_normalize=url_count)

        if not args.dry_run and url_count > 0:
            result = await conn.execute(_NORMALIZE_URLS)
            log.info("normalize.done", result=result)

            remaining = await conn.fetchval(_COUNT_URLS)
            if remaining > 0:
                log.error("normalize.postcondition_failed", remaining=remaining)
                return 1

        # 2. Backfill next_scrape_at
        scrape_count = await conn.fetchval(_COUNT_SCRAPE)
        log.info("backfill.preview", active_null_scrape=scrape_count)

        if args.dry_run:
            log.info("dry_run.complete")
            return 0

        if scrape_count == 0 and url_count == 0:
            log.info("backfill.nothing_to_do")
            return 0

        if scrape_count > 0:
            result = await conn.execute(_BACKFILL_SCRAPE)
            log.info("backfill.done", result=result)

            remaining = await conn.fetchval(_COUNT_SCRAPE)
            if remaining > 0:
                log.error("backfill.postcondition_failed", null_next_scrape=remaining)
                return 1

        log.info("migration.complete")
    finally:
        await conn.close()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
