"""Add US regional macros and 'Anywhere' handling to the location tables.

Creates macro regions for the US (Northeast, Southeast, Midwest, etc.)
with parent_id pointing to the United States country entry.

Usage:
  uv run python scripts/add_us_region_macros.py
  uv run python scripts/add_us_region_macros.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio

import asyncpg

from src.config import settings

US_ID = 6252001

# (id, parent_id, names_en)
MACROS = [
    (10, US_ID, ["Northeast US", "Northeastern US"]),
    (11, US_ID, ["Southeast US", "Southeastern US"]),
    (12, US_ID, ["Midwest US", "Midwestern US", "Central US"]),
    (13, US_ID, ["West US", "Western US"]),
    (14, US_ID, ["Southwest US", "Southwestern US"]),
    (15, US_ID, ["Pacific Northwest", "Pacific Northwest US"]),
]

LOCALES = ["en", "de", "fr", "it"]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Add US regional macros")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    try:
        for macro_id, parent_id, names in MACROS:
            existing = await conn.fetchrow("SELECT id FROM location WHERE id = $1", macro_id)
            if existing:
                print(f"  skip ID={macro_id} ({names[0]}) — already exists")
                continue

            print(
                f"  {'would add' if args.dry_run else 'adding'} ID={macro_id} {names[0]} (parent={parent_id})"
            )

            if not args.dry_run:
                await conn.execute(
                    "INSERT INTO location (id, parent_id, type, population) VALUES ($1, $2, 'macro', 0)",
                    macro_id,
                    parent_id,
                )
                for name in names:
                    for locale in LOCALES:
                        await conn.execute(
                            "INSERT INTO location_name (location_id, locale, name) VALUES ($1, $2, $3)",
                            macro_id,
                            locale,
                            name,
                        )

        if args.dry_run:
            print("\n  DRY RUN — no changes written")
        else:
            print("\n  done")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
