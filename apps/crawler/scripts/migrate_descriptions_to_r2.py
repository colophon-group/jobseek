"""Bulk-migrate existing job_posting descriptions to R2.

Reads all postings with a description from the DB, uploads each to R2.
R2 path is deterministic: job/{id}/. No DB marker column needed.
Run once before dropping the description column.

Progress is saved to .migrate_progress.json (cursor-based resume).
Uploads run concurrently (default 20 workers) for speed.

Usage:
    uv run python scripts/migrate_descriptions_to_r2.py [--batch-size 500] [--concurrency 20] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC
from pathlib import Path

import dotenv

dotenv.load_dotenv(".env.local")

import asyncpg
import structlog

from src.config import settings
from src.core.description_store import upload_description, upload_posting

log = structlog.get_logger()

_PROGRESS_FILE = Path(".migrate_progress.json")

_FETCH_POSTINGS = """
SELECT id, title, description, locations, language, localizations, extras, metadata,
       date_posted, base_salary, employment_type, job_location_type
FROM job_posting
WHERE description IS NOT NULL AND id > $1
ORDER BY id
LIMIT $2
"""


def _load_progress() -> str:
    if _PROGRESS_FILE.exists():
        data = json.loads(_PROGRESS_FILE.read_text())
        return data.get("last_id", "00000000-0000-0000-0000-000000000000")
    return "00000000-0000-0000-0000-000000000000"


def _save_progress(last_id: str) -> None:
    _PROGRESS_FILE.write_text(json.dumps({"last_id": last_id}))


def _process_one(row: asyncpg.Record) -> None:
    """Upload one posting to R2 (synchronous, runs in thread pool)."""
    posting_id = str(row["id"])
    title = row["title"]
    description = row["description"]
    locations = row["locations"]
    language = row["language"] or "en"
    localizations = row["localizations"]
    extras_raw = row["extras"]
    metadata_raw = row["metadata"]
    date_posted = row["date_posted"]
    base_salary = row["base_salary"]
    employment_type = row["employment_type"]
    job_location_type = row["job_location_type"]

    merged_extras: dict = {}
    if extras_raw and isinstance(extras_raw, dict):
        merged_extras.update(extras_raw)
    if title is not None:
        merged_extras["title"] = title
    if locations:
        merged_extras["locations"] = locations
    if metadata_raw and isinstance(metadata_raw, dict):
        merged_extras["metadata"] = metadata_raw
    if date_posted is not None:
        if date_posted.tzinfo is None:
            date_posted = date_posted.replace(tzinfo=UTC)
        merged_extras["date_posted"] = date_posted.isoformat()
    if base_salary is not None:
        merged_extras["base_salary"] = base_salary
    if employment_type is not None:
        merged_extras["raw_employment_type"] = employment_type
    if job_location_type is not None:
        merged_extras["raw_job_location_type"] = job_location_type

    upload_posting(posting_id, language, description, merged_extras)

    if localizations and isinstance(localizations, dict):
        for locale, loc_data in localizations.items():
            if locale == language:
                continue
            loc_desc = None
            if isinstance(loc_data, dict):
                loc_desc = loc_data.get("description")
            elif isinstance(loc_data, str):
                loc_desc = loc_data
            if loc_desc:
                upload_description(posting_id, locale, loc_desc)


async def migrate(batch_size: int, concurrency: int, dry_run: bool) -> None:
    pool = await asyncpg.create_pool(settings.database_url, statement_cache_size=0)
    assert pool is not None

    cursor = _load_progress()
    total = 0
    errors = 0
    sem = asyncio.Semaphore(concurrency)

    log.info(
        "migrate.start",
        resume_from=cursor,
        batch_size=batch_size,
        concurrency=concurrency,
        dry_run=dry_run,
    )

    async def _upload(row: asyncpg.Record) -> bool:
        async with sem:
            try:
                await asyncio.to_thread(_process_one, row)
                return True
            except Exception:
                log.exception("migrate.error", posting_id=str(row["id"]))
                return False

    while True:
        rows = await pool.fetch(_FETCH_POSTINGS, cursor, batch_size)
        if not rows:
            break

        if dry_run:
            for row in rows:
                log.info("migrate.dry_run", posting_id=str(row["id"]))
            total += len(rows)
            cursor = str(rows[-1]["id"])
            _save_progress(cursor)
            continue

        tasks = [_upload(row) for row in rows]
        results = await asyncio.gather(*tasks)

        for ok in results:
            if ok:
                total += 1
            else:
                errors += 1

        cursor = str(rows[-1]["id"])
        _save_progress(cursor)
        log.info("migrate.progress", total=total, errors=errors, cursor=cursor[:8])

    await pool.close()
    log.info("migrate.done", total=total, errors=errors)
    if errors:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate descriptions to R2")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    asyncio.run(migrate(args.batch_size, args.concurrency, args.dry_run))


if __name__ == "__main__":
    main()
