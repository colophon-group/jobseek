"""Consolidate redundant Workday boards and backfill next_scrape_at.

Reassigns postings from deprecated PwC/Uniqlo boards to surviving ones,
then backfills next_scrape_at for all active Workday postings with NULL.

Usage:
  uv run python scripts/consolidate_workday_boards.py --dry-run
  uv run python scripts/consolidate_workday_boards.py
"""

from __future__ import annotations

import argparse
import asyncio

import asyncpg
import structlog

from src.config import settings

log = structlog.get_logger()

# (deprecated_url, surviving_url)
_REASSIGNMENTS: list[tuple[str, str]] = [
    (
        "https://pwc.wd3.myworkdayjobs.com/Global_Campus_Careers",
        "https://pwc.wd3.myworkdayjobs.com/Global_Experienced_Careers",
    ),
    (
        "https://pwc.wd3.myworkdayjobs.com/US_Experienced_Careers",
        "https://pwc.wd3.myworkdayjobs.com/Global_Experienced_Careers",
    ),
    (
        "https://fastretailing.wd3.myworkdayjobs.com/headquarters_eu_Uniqlo",
        "https://fastretailing.wd3.myworkdayjobs.com/graduates_eu_Uniqlo",
    ),
    (
        "https://fastretailing.wd3.myworkdayjobs.com/store_staff_eu_Uniqlo",
        "https://fastretailing.wd3.myworkdayjobs.com/graduates_eu_Uniqlo",
    ),
]

_LOOKUP_BOARD = """
SELECT id, board_slug FROM job_board WHERE board_url = $1
"""

_REASSIGN_POSTINGS = """
UPDATE job_posting SET board_id = $1
WHERE board_id = $2
"""

_COUNT_POSTINGS = """
SELECT count(*) FROM job_posting WHERE board_id = $1
"""

_BACKFILL_NEXT_SCRAPE = """
UPDATE job_posting SET next_scrape_at = now()
WHERE is_active = true AND next_scrape_at IS NULL
  AND board_id IN (SELECT id FROM job_board WHERE crawler_type = 'workday')
"""

_COUNT_BACKFILL_CANDIDATES = """
SELECT count(*) FROM job_posting
WHERE is_active = true AND next_scrape_at IS NULL
  AND board_id IN (SELECT id FROM job_board WHERE crawler_type = 'workday')
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consolidate redundant Workday boards and backfill next_scrape_at"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report counts without changes")
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    try:
        # --- Resolve board IDs ---
        reassignments: list[tuple[asyncpg.Record, asyncpg.Record]] = []
        for dep_url, surv_url in _REASSIGNMENTS:
            dep = await conn.fetchrow(_LOOKUP_BOARD, dep_url)
            surv = await conn.fetchrow(_LOOKUP_BOARD, surv_url)
            if not dep:
                log.error("board.not_found", url=dep_url)
                return 1
            if not surv:
                log.error("board.not_found", url=surv_url)
                return 1
            reassignments.append((dep, surv))

        # --- Preview ---
        for dep, surv in reassignments:
            count = await conn.fetchval(_COUNT_POSTINGS, dep["id"])
            log.info(
                "reassign.preview",
                deprecated=dep["board_slug"],
                surviving=surv["board_slug"],
                postings=count,
            )

        backfill_count = await conn.fetchval(_COUNT_BACKFILL_CANDIDATES)
        log.info("backfill.preview", active_workday_null_scrape=backfill_count)

        if args.dry_run:
            log.info("dry_run.complete")
            return 0

        # --- Execute in transaction ---
        async with conn.transaction():
            for dep, surv in reassignments:
                result = await conn.execute(_REASSIGN_POSTINGS, surv["id"], dep["id"])
                log.info(
                    "reassign.done",
                    deprecated=dep["board_slug"],
                    surviving=surv["board_slug"],
                    result=result,
                )

            result = await conn.execute(_BACKFILL_NEXT_SCRAPE)
            log.info("backfill.done", result=result)

        # --- Verify postconditions ---
        for dep, _surv in reassignments:
            remaining = await conn.fetchval(_COUNT_POSTINGS, dep["id"])
            if remaining > 0:
                log.error("postcondition.failed", board=dep["board_slug"], remaining=remaining)
                return 1

        remaining_null = await conn.fetchval(_COUNT_BACKFILL_CANDIDATES)
        if remaining_null > 0:
            log.error("postcondition.failed", null_next_scrape=remaining_null)
            return 1

        log.info("consolidation.complete")
    finally:
        await conn.close()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
