"""One-shot backfill: clear ``next_scrape_at`` for rich-monitor postings.

Drains the stuck-scrape cohort described in
``dev/browser-errors/01-rich-monitor-scheduling.md``. Postings whose board
is rich-no-scrape (``metadata.scraper_type = 'skip'`` or the crawler type
auto-resolves to skip, AND no enrichment configured) must never have
``next_scrape_at`` set, or the scheduler keeps feeding the placeholder
``skip`` scraper which raises ``RuntimeError("skip scraper called …")``.

The update is idempotent — run once after deploying the preventative fixes.

The UPDATE is chunked (default 5,000 rows per transaction) so a ~40 k row
cohort doesn't hold row-level locks on ``job_posting`` long enough to
contend with the exporter or live scrape workers. Each chunk commits
independently.

Usage::

    cd apps/crawler
    LOCAL_DATABASE_URL="postgresql://crawler:<pwd>@<host>:5432/crawler" \\
    uv run python ../../scripts/backfill-clear-rich-scrape.py [--dry-run] \\
        [--batch-size 5000]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg
import structlog

log = structlog.get_logger()

# Hardcoded mirror of ``workspace._compat._AUTO_SKIP_CRAWLER_TYPES``.
# Keep in sync. The classifier in ``_is_skip_no_scrape`` and the SQL
# predicate in ``queries/scrape.py::_SKIP_NO_SCRAPE_PREDICATE`` must
# agree with this list or the backfill will drift.
_AUTO_SKIP_CRAWLER_TYPES: tuple[str, ...] = (
    "accenture",
    "amazon",
    "apify_meta",
    "ashby",
    "deel",
    "dvinci",
    "gem",
    "greenhouse",
    "hireology",
    "inline",
    "lever",
    "mokahr",
    "pinpoint",
    "recruitee",
    "rss",
    "signals",
    "traffit",
)


def _skip_no_scrape_predicate(alias: str = "jb") -> str:
    literal = ", ".join(f"'{t}'" for t in _AUTO_SKIP_CRAWLER_TYPES)
    return f"""(
        ({alias}.metadata->>'scraper_type' = 'skip'
         OR (
             {alias}.metadata->>'scraper_type' IS NULL
             AND (
                 {alias}.crawler_type IN ({literal})
                 OR (
                     {alias}.crawler_type IN ('api_sniffer', 'nextdata')
                     AND {alias}.metadata ? 'fields'
                 )
             )
         )
        )
        AND NOT COALESCE({alias}.metadata->'scraper_config' ? 'enrich', false)
    )"""


_COUNT_STUCK = f"""
SELECT jb.crawler_type,
       count(*) AS cnt
FROM job_posting jp
JOIN job_board jb ON jp.board_id = jb.id
WHERE jp.next_scrape_at IS NOT NULL
  AND {_skip_no_scrape_predicate('jb')}
GROUP BY jb.crawler_type
ORDER BY cnt DESC
"""


# Chunked clear: select up to N candidate ids per batch, update them in the
# same statement via CTE. The CTE + IN lets us use ``LIMIT`` without LOCK
# contention on the SELECT — each batch is a short independent txn.
_CLEAR_CHUNK = f"""
WITH candidates AS (
    SELECT jp.id
    FROM job_posting jp
    JOIN job_board jb ON jp.board_id = jb.id
    WHERE jp.next_scrape_at IS NOT NULL
      AND {_skip_no_scrape_predicate('jb')}
    LIMIT $1
)
UPDATE job_posting
SET next_scrape_at = NULL,
    leased_until   = NULL,
    scrape_failures = 0,
    updated_at     = now()
WHERE id IN (SELECT id FROM candidates)
RETURNING id
"""


async def _run(dry_run: bool, batch_size: int) -> None:
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

        cleared = 0
        while True:
            # One short transaction per batch: row-level locks are held for
            # milliseconds, not seconds. Concurrent scrape writers and the
            # exporter see a stream of small updates instead of a single
            # 40 k-row lock wall.
            async with conn.transaction():
                batch_rows = await conn.fetch(_CLEAR_CHUNK, batch_size)
            if not batch_rows:
                break
            cleared += len(batch_rows)
            log.info("backfill.batch_cleared", batch=len(batch_rows), total=cleared)
        log.info("backfill.cleared", count=cleared)
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report stuck postings without modifying them.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows per transaction. Lower = less lock contention, more roundtrips.",
    )
    args = parser.parse_args()
    asyncio.run(_run(dry_run=args.dry_run, batch_size=args.batch_size))


if __name__ == "__main__":
    main()
