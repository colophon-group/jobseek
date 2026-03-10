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
from urllib.parse import urlparse

import polars as pl
import structlog

if TYPE_CHECKING:
    import asyncpg

from src.config import settings
from src.core.monitors import api_monitor_types
from src.db import close_pool, create_pool
from src.shared.logging import setup_logging

_API_MONITOR_TYPES = api_monitor_types()

log = structlog.get_logger()

DATA_DIR = Path(__file__).parent.parent / "data"

_UPSERT_COMPANIES = """
INSERT INTO company (slug, name, website, logo, icon, logo_type)
SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[], $6::text[])
ON CONFLICT (slug) DO UPDATE SET
    name = COALESCE(EXCLUDED.name, company.name),
    website = COALESCE(EXCLUDED.website, company.website),
    logo = COALESCE(EXCLUDED.logo, company.logo),
    icon = COALESCE(EXCLUDED.icon, company.icon),
    logo_type = COALESCE(EXCLUDED.logo_type, company.logo_type),
    updated_at = now()
"""

_UPSERT_BOARDS = """
INSERT INTO job_board (company_id, board_slug, board_url, crawler_type, metadata,
                       next_check_at, throttle_key)
SELECT c.id, b.board_slug, b.board_url, b.crawler_type, b.metadata::jsonb,
       now() + (random() * 3600) * interval '1 second',
       b.throttle_key
FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[], $6::text[])
  AS b(company_slug, board_slug, board_url, crawler_type, metadata, throttle_key)
JOIN company c ON c.slug = b.company_slug
ON CONFLICT (board_url) DO UPDATE SET
    board_slug = COALESCE(EXCLUDED.board_slug, job_board.board_slug),
    crawler_type = EXCLUDED.crawler_type,
    metadata = EXCLUDED.metadata,
    throttle_key = EXCLUDED.throttle_key,
    is_enabled = true,
    board_status = CASE
        WHEN job_board.board_status = 'disabled' THEN 'active'
        ELSE job_board.board_status
    END,
    updated_at = now()
"""

_DISABLE_REMOVED_BOARDS = """
UPDATE job_board
SET is_enabled = false, board_status = 'disabled', updated_at = now()
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


def _compute_throttle_key(monitor_type: str, board_url: str) -> str:
    """Compute rate-limit grouping key from monitor type and board URL."""
    if monitor_type in _API_MONITOR_TYPES:
        return monitor_type
    return urlparse(board_url).hostname or board_url


async def sync_companies(conn: asyncpg.Connection, companies: pl.DataFrame, dry_run: bool) -> None:
    """Batch upsert companies."""
    if len(companies) == 0:
        return

    slugs: list[str] = []
    names: list[str] = []
    websites: list[str | None] = []
    logos: list[str | None] = []
    icons: list[str | None] = []
    logo_types: list[str | None] = []

    for row in companies.iter_rows(named=True):
        slugs.append(row["slug"])
        names.append(row["name"])
        websites.append(_or_none(row.get("website")))
        logos.append(_or_none(row.get("logo_url")))
        icons.append(_or_none(row.get("icon_url")))
        logo_types.append(_or_none(row.get("logo_type")))

    if dry_run:
        log.info("sync.companies.dry_run", count=len(slugs))
        return

    await conn.execute(_UPSERT_COMPANIES, slugs, names, websites, logos, icons, logo_types)
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
    throttle_keys: list[str] = []
    skipped = 0

    for row in boards.iter_rows(named=True):
        monitor_config_str = row.get("monitor_config") or None
        scraper_type = _or_none(row.get("scraper_type"))
        scraper_config_str = row.get("scraper_config") or None
        metadata_obj: dict = {}

        if monitor_config_str:
            try:
                parsed = json.loads(monitor_config_str)
                if not isinstance(parsed, dict):
                    raise ValueError("monitor_config must be a JSON object")
                metadata_obj.update(parsed)
            except json.JSONDecodeError:
                log.error(
                    "sync.board.invalid_config",
                    board_url=row["board_url"],
                    config=monitor_config_str,
                )
                skipped += 1
                continue
            except ValueError:
                log.error(
                    "sync.board.invalid_config",
                    board_url=row["board_url"],
                    config=monitor_config_str,
                )
                skipped += 1
                continue

        if scraper_type:
            metadata_obj["scraper_type"] = scraper_type

        if scraper_config_str:
            try:
                scraper_cfg = json.loads(scraper_config_str)
                if not isinstance(scraper_cfg, dict):
                    raise ValueError("scraper_config must be a JSON object")
                metadata_obj["scraper_config"] = scraper_cfg
            except json.JSONDecodeError:
                log.error(
                    "sync.board.invalid_scraper_config",
                    board_url=row["board_url"],
                    config=scraper_config_str,
                )
                skipped += 1
                continue
            except ValueError:
                log.error(
                    "sync.board.invalid_scraper_config",
                    board_url=row["board_url"],
                    config=scraper_config_str,
                )
                skipped += 1
                continue

        fallback_scraper_type = _or_none(row.get("fallback_scraper_type"))
        fallback_scraper_config_str = row.get("fallback_scraper_config") or None

        if fallback_scraper_type:
            metadata_obj["fallback_scraper_type"] = fallback_scraper_type

        if fallback_scraper_config_str:
            try:
                fallback_cfg = json.loads(fallback_scraper_config_str)
                if not isinstance(fallback_cfg, dict):
                    raise ValueError("fallback_scraper_config must be a JSON object")
                metadata_obj["fallback_scraper_config"] = fallback_cfg
            except json.JSONDecodeError:
                log.error(
                    "sync.board.invalid_fallback_scraper_config",
                    board_url=row["board_url"],
                    config=fallback_scraper_config_str,
                )
                skipped += 1
                continue
            except ValueError:
                log.error(
                    "sync.board.invalid_fallback_scraper_config",
                    board_url=row["board_url"],
                    config=fallback_scraper_config_str,
                )
                skipped += 1
                continue

        metadata: str | None = json.dumps(metadata_obj) if metadata_obj else None

        company_slugs.append(row["company_slug"])
        board_slugs.append(_or_none(row.get("board_slug")))
        board_urls.append(row["board_url"])
        crawler_types.append(row["monitor_type"])
        metadatas.append(metadata)
        throttle_keys.append(_compute_throttle_key(row["monitor_type"], row["board_url"]))

    if dry_run:
        log.info("sync.boards.dry_run", count=len(board_urls), skipped=skipped)
        return

    if not board_urls:
        log.info("sync.boards.all_skipped", skipped=skipped)
        return

    await conn.execute(
        _UPSERT_BOARDS,
        company_slugs,
        board_slugs,
        board_urls,
        crawler_types,
        metadatas,
        throttle_keys,
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
