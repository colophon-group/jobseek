"""CSV -> DB sync script.

Reads data/companies.csv and data/boards.csv, upserts rows into the database.
The DB is derived state — CSVs are the source of truth.

Usage:
    uv run python -m src.sync              # sync both CSVs
    uv run python -m src.sync --dry-run    # show what would change
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import structlog

if TYPE_CHECKING:
    import asyncpg

from src.config import settings
from src.db import close_pool, create_pool
from src.shared.logging import setup_logging

log = structlog.get_logger()

DATA_DIR = Path(__file__).parent.parent / "data"

_UPSERT_COMPANIES = """
INSERT INTO company (slug, name, website, logo, icon)
SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[])
ON CONFLICT (slug) DO UPDATE SET
    name = COALESCE(EXCLUDED.name, company.name),
    website = COALESCE(EXCLUDED.website, company.website),
    logo = COALESCE(EXCLUDED.logo, company.logo),
    icon = COALESCE(EXCLUDED.icon, company.icon),
    updated_at = now()
"""

_UPSERT_BOARDS = """
INSERT INTO job_board (company_id, board_slug, board_url, crawler_type, metadata,
                       next_check_at)
SELECT c.id, b.board_slug, b.board_url, b.crawler_type, b.metadata::jsonb,
       now() + (random() * 3600) * interval '1 second'
FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[])
  AS b(company_slug, board_slug, board_url, crawler_type, metadata)
JOIN company c ON c.slug = b.company_slug
ON CONFLICT (board_url) DO UPDATE SET
    board_slug = COALESCE(EXCLUDED.board_slug, job_board.board_slug),
    crawler_type = EXCLUDED.crawler_type,
    metadata = EXCLUDED.metadata,
    updated_at = now()
"""

_DISABLE_REMOVED_BOARDS = """
UPDATE job_board
SET is_enabled = false, updated_at = now()
WHERE board_url NOT IN (SELECT unnest($1::text[]))
  AND is_enabled = true
"""


def _load_companies() -> pl.DataFrame:
    path = DATA_DIR / "companies.csv"
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_companies", count=len(df), path=str(path))
    return df


def _load_boards() -> pl.DataFrame:
    path = DATA_DIR / "boards.csv"
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_boards", count=len(df), path=str(path))
    return df


def _or_none(val: str | None) -> str | None:
    return val if val else None


async def sync_companies(conn: asyncpg.Connection, companies: pl.DataFrame, dry_run: bool) -> None:
    """Batch upsert companies."""
    if len(companies) == 0:
        return

    slugs: list[str] = []
    names: list[str] = []
    websites: list[str | None] = []
    logos: list[str | None] = []
    icons: list[str | None] = []

    for row in companies.iter_rows(named=True):
        slugs.append(row["slug"])
        names.append(row["name"])
        websites.append(_or_none(row.get("website")))
        logos.append(_or_none(row.get("logo_url")))
        icons.append(_or_none(row.get("icon_url")))

    if dry_run:
        log.info("sync.companies.dry_run", count=len(slugs))
        return

    await conn.execute(_UPSERT_COMPANIES, slugs, names, websites, logos, icons)
    log.info("sync.companies.upserted", count=len(slugs))


async def sync_boards(
    conn: asyncpg.Connection,
    boards: pl.DataFrame,
    dry_run: bool,
) -> None:
    """Batch upsert boards and disable boards removed from CSV."""
    if len(boards) == 0:
        return

    company_slugs: list[str] = []
    board_slugs: list[str | None] = []
    board_urls: list[str] = []
    crawler_types: list[str] = []
    metadatas: list[str | None] = []
    skipped = 0

    for row in boards.iter_rows(named=True):
        monitor_config_str = row.get("monitor_config") or None
        metadata: str | None = None
        if monitor_config_str:
            try:
                parsed = json.loads(monitor_config_str)
                metadata = json.dumps(parsed)
            except json.JSONDecodeError:
                log.error(
                    "sync.board.invalid_config",
                    board_url=row["board_url"],
                    config=monitor_config_str,
                )
                skipped += 1
                continue

        company_slugs.append(row["company_slug"])
        board_slugs.append(_or_none(row.get("board_slug")))
        board_urls.append(row["board_url"])
        crawler_types.append(row["monitor_type"])
        metadatas.append(metadata)

    if dry_run:
        log.info("sync.boards.dry_run", count=len(board_urls), skipped=skipped)
        return

    if not board_urls:
        log.info("sync.boards.all_skipped", skipped=skipped)
        return

    await conn.execute(
        _UPSERT_BOARDS, company_slugs, board_slugs, board_urls, crawler_types, metadatas
    )
    log.info("sync.boards.upserted", count=len(board_urls), skipped=skipped)

    await conn.execute(_DISABLE_REMOVED_BOARDS, board_urls)


async def run_sync(dry_run: bool = False) -> None:
    setup_logging(settings.log_level)

    companies = _load_companies()
    boards = _load_boards()

    if len(companies) == 0 and len(boards) == 0:
        log.info("sync.empty", msg="No data in CSVs, nothing to sync")
        return

    pool = await create_pool()
    try:
        async with pool.acquire() as conn, conn.transaction():
            await sync_companies(conn, companies, dry_run)
            await sync_boards(conn, boards, dry_run)

        log.info(
            "sync.complete",
            companies=len(companies),
            boards=len(boards),
            dry_run=dry_run,
        )
    finally:
        await close_pool()


def main():
    parser = argparse.ArgumentParser(description="Sync CSV config to database")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()
    asyncio.run(run_sync(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
