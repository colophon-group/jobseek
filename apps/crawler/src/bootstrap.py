"""Bootstrap local Postgres from Supabase.

One-time migration: copies all job_board and job_posting data from Supabase
to the local Postgres on Hetzner. Does NOT touch Supabase data.

Usage:
    LOCAL_DATABASE_URL=$LOCAL_DATABASE_URL \
    uv run python -m src.bootstrap
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

import asyncpg
import dotenv
import structlog

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv(".env")

from src.config import settings  # noqa: E402
from src.shared.logging import setup_logging  # noqa: E402

log = structlog.get_logger()

# Columns shared between Supabase and local Postgres for job_board
_BOARD_COLUMNS = [
    "id",
    "company_id",
    "board_slug",
    "crawler_type",
    "board_url",
    "check_interval_minutes",
    "next_check_at",
    "last_checked_at",
    "last_success_at",
    "consecutive_failures",
    "last_error",
    "is_enabled",
    "board_status",
    "throttle_key",
    "lease_owner",
    "leased_until",
    "empty_check_count",
    "last_non_empty_at",
    "gone_at",
    "metadata",
    "scrape_interval_hours",
    "monitor_needs_browser",
    "scraper_needs_browser",
    "created_at",
    "updated_at",
]

# Columns that exist in BOTH Supabase and local Postgres for job_posting
# (Supabase has no updated_at; local has no description_pending/r2_pending_meta/lease_owner)
_POSTING_COLUMNS_SUPA = [
    "id",
    "company_id",
    "board_id",
    "is_active",
    "locales",
    "titles",
    "location_ids",
    "location_types",
    "description_r2_hash",
    "employment_type",
    "source_url",
    "first_seen_at",
    "last_seen_at",
    "next_scrape_at",
    "last_scraped_at",
    "leased_until",
    "scrape_failures",
    "missing_count",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_eur",
    "experience_min",
    "experience_max",
    "occupation_id",
    "seniority_id",
    "technology_ids",
    "enrichment",
    "to_be_enriched",
    "enrich_version",
    "last_enriched_at",
]

# Same columns used for INSERT into local Postgres
_POSTING_COLUMNS_SUPA_LOCAL = _POSTING_COLUMNS_SUPA

BATCH_SIZE = 5000


def _column_list(columns: Sequence[str]) -> str:
    return ", ".join(columns)


def _upsert_set(columns: Sequence[str]) -> str:
    return ", ".join(column + " = EXCLUDED." + column for column in columns if column != "id")


_BOARD_COLUMN_LIST = _column_list(_BOARD_COLUMNS)
_BOARD_UPSERT_SET = _upsert_set(_BOARD_COLUMNS)
_BOARD_SELECT_SQL = "SELECT " + _BOARD_COLUMN_LIST + " FROM job_board ORDER BY id"
_IMPORT_BOARDS_UPSERT_SQL = (
    "INSERT INTO job_board ("
    + _BOARD_COLUMN_LIST
    + ") SELECT "
    + _BOARD_COLUMN_LIST
    + " FROM _import_boards ON CONFLICT (id) DO UPDATE SET "
    + _BOARD_UPSERT_SET
)

_POSTING_COLUMN_LIST_SUPA = _column_list(_POSTING_COLUMNS_SUPA)
_POSTING_UPSERT_SET_SUPA = _upsert_set(_POSTING_COLUMNS_SUPA)
_POSTING_SELECT_BATCH_SQL = (
    "SELECT " + _POSTING_COLUMN_LIST_SUPA + " FROM job_posting ORDER BY id OFFSET $1 LIMIT $2"
)
_IMPORT_POSTINGS_UPSERT_SQL = (
    "INSERT INTO job_posting ("
    + _POSTING_COLUMN_LIST_SUPA
    + ") SELECT "
    + _POSTING_COLUMN_LIST_SUPA
    + " FROM _import_postings ON CONFLICT (id) DO UPDATE SET "
    + _POSTING_UPSERT_SET_SUPA
)


async def _copy_boards(supa: asyncpg.Pool, local: asyncpg.Pool) -> int:
    """Copy all job_board rows from Supabase to local Postgres."""
    rows = await supa.fetch(_BOARD_SELECT_SQL)
    if not rows:
        return 0

    # Use temp table + INSERT ON CONFLICT for idempotency
    async with local.acquire() as conn, conn.transaction():
        await conn.execute("""
            CREATE TEMP TABLE _import_boards (LIKE job_board INCLUDING DEFAULTS)
            ON COMMIT DROP
        """)
        await conn.copy_records_to_table(
            "_import_boards",
            records=[tuple(r[c] for c in _BOARD_COLUMNS) for r in rows],
            columns=_BOARD_COLUMNS,
        )
        await conn.execute(_IMPORT_BOARDS_UPSERT_SQL)

    return len(rows)


async def _copy_postings(supa: asyncpg.Pool, local: asyncpg.Pool) -> int:
    """Copy all job_posting rows from Supabase to local Postgres in batches."""
    total = await supa.fetchval("SELECT count(*) FROM job_posting")
    log.info("bootstrap.postings_count", total=total)

    copied = 0
    offset = 0

    while offset < total:
        rows = await supa.fetch(
            _POSTING_SELECT_BATCH_SQL,
            offset,
            BATCH_SIZE,
        )
        if not rows:
            break

        async with local.acquire() as conn, conn.transaction():
            await conn.execute("""
                CREATE TEMP TABLE _import_postings (LIKE job_posting INCLUDING DEFAULTS)
                ON COMMIT DROP
            """)
            await conn.copy_records_to_table(
                "_import_postings",
                records=[tuple(r[c] for c in _POSTING_COLUMNS_SUPA) for r in rows],
                columns=_POSTING_COLUMNS_SUPA,
            )
            await conn.execute(_IMPORT_POSTINGS_UPSERT_SQL)

        copied += len(rows)
        offset += BATCH_SIZE
        log.info("bootstrap.postings_batch", copied=copied, total=total)

    return copied


async def main() -> None:
    setup_logging()
    log.info("bootstrap.start")
    t0 = time.monotonic()

    supa = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=3,
        command_timeout=120,
        statement_cache_size=0,
    )
    local = await asyncpg.create_pool(
        settings.local_database_url,
        min_size=1,
        max_size=3,
        command_timeout=120,
        statement_cache_size=0,
    )

    try:
        boards = await _copy_boards(supa, local)
        log.info("bootstrap.boards_done", count=boards)

        postings = await _copy_postings(supa, local)
        log.info("bootstrap.postings_done", count=postings)

        elapsed = round(time.monotonic() - t0, 1)
        log.info("bootstrap.complete", boards=boards, postings=postings, elapsed_s=elapsed)
    finally:
        await supa.close()
        await local.close()


if __name__ == "__main__":
    asyncio.run(main())
