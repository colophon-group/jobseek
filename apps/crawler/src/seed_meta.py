"""Seed script: sync CSVs to DB and make meta-careers immediately due.

Usage:
    uv run python -m src.seed_meta
"""

from __future__ import annotations

import asyncio

import dotenv
import structlog

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv(".env")

from src.db import close_pool, create_pool  # noqa: E402
from src.shared.logging import setup_logging  # noqa: E402
from src.sync import run_sync  # noqa: E402

log = structlog.get_logger()

_RESET_NEXT_CHECK = """
UPDATE job_board
SET next_check_at = now(),
    leased_until  = NULL
WHERE board_slug = $1
RETURNING id, board_slug, next_check_at
"""


async def main() -> None:
    setup_logging("info")

    # Full CSV → DB sync
    await run_sync()

    # Re-open pool (run_sync closes it)
    pool = await create_pool()
    try:
        rows = await pool.fetch(_RESET_NEXT_CHECK, "meta-careers")
        if rows:
            row = rows[0]
            log.info("seed_meta.ready", board_slug=row["board_slug"], id=row["id"])
        else:
            log.error("seed_meta.not_found", board_slug="meta-careers")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
