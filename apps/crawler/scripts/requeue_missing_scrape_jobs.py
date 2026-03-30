"""Backfill next_scrape_at for active postings missing scraped content.

Sets next_scrape_at = now() so the Postgres scheduler picks them up.

Usage:
  uv run python scripts/requeue_missing_scrape_jobs.py --dry-run
  uv run python scripts/requeue_missing_scrape_jobs.py --limit 10000
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json

import asyncpg

from src.config import settings
from src.core.monitors import is_rich_monitor

_FETCH_CANDIDATES = """
SELECT
  jp.id::text AS job_posting_id,
  jp.source_url,
  jp.board_id::text AS board_id,
  jp.created_at,
  jp.updated_at,
  jb.crawler_type,
  jb.metadata
FROM job_posting jp
JOIN job_board jb ON jb.id = jp.board_id
WHERE jp.status = 'active'
  AND jb.is_enabled = true
  AND jp.source_url IS NOT NULL
  AND (jp.title IS NULL OR jp.description IS NULL)
  AND jp.next_scrape_at IS NULL
ORDER BY jp.first_seen_at ASC
LIMIT $1
"""

_SET_NEXT_SCRAPE = """
UPDATE job_posting
SET next_scrape_at = now(),
    scrape_domain = split_part(split_part(source_url, '://', 2), '/', 1),
    updated_at = now()
WHERE id = ANY($1::uuid[])
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill next_scrape_at for postings missing content"
    )
    parser.add_argument("--limit", type=int, default=10000, help="Max DB rows to inspect")
    parser.add_argument("--dry-run", action="store_true", help="Print counts without updating")
    return parser.parse_args()


def _parse_metadata(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
    return {}


async def _run() -> int:
    args = _parse_args()

    conn = await asyncpg.connect(settings.database_url)
    try:
        rows = await conn.fetch(_FETCH_CANDIDATES, args.limit)

        # Filter out postings from rich monitors (they already have content from discovery)
        eligible_ids: list[str] = []
        skipped_rich = 0
        for row in rows:
            crawler_type = row["crawler_type"]
            metadata = _parse_metadata(row["metadata"])
            if is_rich_monitor(crawler_type, metadata):
                skipped_rich += 1
                continue
            eligible_ids.append(row["job_posting_id"])

        print(f"candidates={len(rows)} skipped_rich={skipped_rich} eligible={len(eligible_ids)}")

        if args.dry_run or not eligible_ids:
            return 0

        await conn.execute(_SET_NEXT_SCRAPE, eligible_ids)
        print(f"updated next_scrape_at for {len(eligible_ids)} postings")
    finally:
        await conn.close()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
