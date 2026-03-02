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

import polars as pl
import structlog

from src.config import settings
from src.db import close_pool, create_pool
from src.shared.logging import setup_logging

log = structlog.get_logger()

DATA_DIR = Path(__file__).parent.parent / "data"

_UPSERT_COMPANY = """
INSERT INTO company (slug, name, website, logo, icon)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (slug) DO UPDATE SET
    name = COALESCE(EXCLUDED.name, company.name),
    website = COALESCE(EXCLUDED.website, company.website),
    logo = COALESCE(EXCLUDED.logo, company.logo),
    icon = COALESCE(EXCLUDED.icon, company.icon),
    updated_at = now()
RETURNING id, slug
"""

_UPSERT_BOARD = """
INSERT INTO job_board (company_id, board_url, crawler_type, metadata)
VALUES ($1, $2, $3, $4::jsonb)
ON CONFLICT (board_url) DO UPDATE SET
    crawler_type = EXCLUDED.crawler_type,
    metadata = EXCLUDED.metadata,
    updated_at = now()
RETURNING id, board_url
"""

_DISABLE_REMOVED_BOARDS = """
UPDATE job_board
SET is_enabled = false, updated_at = now()
WHERE board_url NOT IN (SELECT unnest($1::text[]))
  AND is_enabled = true
"""

_GET_COMPANY_ID = """
SELECT id FROM company WHERE slug = $1
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


async def sync_companies(conn, companies: pl.DataFrame, dry_run: bool) -> dict[str, str]:
    """Upsert companies, return slug -> id mapping."""
    slug_to_id: dict[str, str] = {}

    for row in companies.iter_rows(named=True):
        slug = row["slug"]
        name = row["name"]
        website = row.get("website") or None
        logo_url = row.get("logo_url") or None
        icon_url = row.get("icon_url") or None

        if dry_run:
            log.info("sync.company.dry_run", slug=slug, name=name)
            continue

        result = await conn.fetchrow(
            _UPSERT_COMPANY,
            slug,
            name,
            website,
            logo_url,
            icon_url,
        )
        slug_to_id[result["slug"]] = str(result["id"])
        log.info("sync.company.upserted", slug=slug)

    return slug_to_id


async def sync_boards(
    conn,
    boards: pl.DataFrame,
    slug_to_id: dict[str, str],
    dry_run: bool,
) -> None:
    """Upsert boards and disable boards removed from CSV."""
    all_board_urls: list[str] = []

    for row in boards.iter_rows(named=True):
        company_slug = row["company_slug"]
        board_url = row["board_url"]
        monitor_type = row["monitor_type"]
        monitor_config_str = row.get("monitor_config") or None

        all_board_urls.append(board_url)

        # Get company_id
        company_id = slug_to_id.get(company_slug)
        if not company_id:
            # Look up in DB (company may have been synced in a previous run)
            result = await conn.fetchrow(_GET_COMPANY_ID, company_slug)
            if result:
                company_id = str(result["id"])
            else:
                log.error(
                    "sync.board.missing_company",
                    company_slug=company_slug,
                    board_url=board_url,
                )
                continue

        # Parse monitor_config JSON
        metadata: str | None = None
        if monitor_config_str:
            try:
                parsed = json.loads(monitor_config_str)
                metadata = json.dumps(parsed)
            except json.JSONDecodeError:
                log.error(
                    "sync.board.invalid_config",
                    board_url=board_url,
                    config=monitor_config_str,
                )
                continue

        if dry_run:
            log.info("sync.board.dry_run", board_url=board_url, monitor_type=monitor_type)
            continue

        await conn.fetchrow(
            _UPSERT_BOARD,
            company_id,
            board_url,
            monitor_type,
            metadata,
        )
        log.info("sync.board.upserted", board_url=board_url, monitor_type=monitor_type)

    # Disable boards not in CSV
    if not dry_run and all_board_urls:
        await conn.execute(_DISABLE_REMOVED_BOARDS, all_board_urls)


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
            slug_to_id = await sync_companies(conn, companies, dry_run)
            await sync_boards(conn, boards, slug_to_id, dry_run)

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
