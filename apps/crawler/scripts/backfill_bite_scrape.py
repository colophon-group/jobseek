"""Backfill next_scrape_at for active BITE postings.

Sets next_scrape_at = now for active job postings belonging to BITE boards
that have NULL next_scrape_at, so they get picked up by the scraper on
the next batch cycle.

Currently no BITE boards are configured, but this script is ready for
future use after boards are added to boards.csv.

Usage:
  uv run python scripts/backfill_bite_scrape.py --dry-run
  uv run python scripts/backfill_bite_scrape.py
"""

from __future__ import annotations

import argparse
import asyncio

import asyncpg

from src.config import settings

_BACKFILL_QUERY = """
UPDATE job_posting
SET next_scrape_at = now()
WHERE board_id IN (
    SELECT id FROM board WHERE monitor_type = 'bite'
)
AND is_active = true
AND next_scrape_at IS NULL
"""

_COUNT_QUERY = """
SELECT count(*) FROM job_posting
WHERE board_id IN (
    SELECT id FROM board WHERE monitor_type = 'bite'
)
AND is_active = true
AND next_scrape_at IS NULL
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill next_scrape_at for BITE postings")
    parser.add_argument("--dry-run", action="store_true", help="Count but don't update")
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)

    try:
        count = await conn.fetchval(_COUNT_QUERY)
        print(f"Found {count} active BITE postings with NULL next_scrape_at")

        if count == 0:
            print("Nothing to do.")
            return 0

        if args.dry_run:
            print("DRY RUN — skipping update")
            return 0

        result = await conn.execute(_BACKFILL_QUERY)
        updated = int(result.split()[-1]) if result else 0
        print(f"Updated {updated} rows")
    finally:
        await conn.close()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
