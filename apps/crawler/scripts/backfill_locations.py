"""Backfill location_ids + location_types on existing job_posting rows.

Uses LocationResolver to match existing `locations text[]` to structured IDs.

Usage:
  uv run python scripts/backfill_locations.py --dry-run
  uv run python scripts/backfill_locations.py --limit 1000
  uv run python scripts/backfill_locations.py --log-unmatched unmatched.txt
"""

from __future__ import annotations

import argparse
import asyncio
import json

import asyncpg

from src.config import settings
from src.core.location_resolve import LocationResolver

_FETCH_POSTINGS = """
SELECT id::text, locations, locales[1] AS lang
FROM job_posting
WHERE locations IS NOT NULL
  AND locations != '{}'
  AND location_ids IS NULL
  AND is_active = true
ORDER BY first_seen_at DESC
LIMIT $1
"""

_BATCH_SIZE = 500


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill location_ids from locations text[]")
    parser.add_argument("--limit", type=int, default=100_000, help="Max rows to process")
    parser.add_argument("--dry-run", action="store_true", help="Resolve but don't update DB")
    parser.add_argument(
        "--log-unmatched", type=str, default=None, help="File to log unmatched strings"
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()

    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=2, statement_cache_size=0
    )

    try:
        # Load resolver
        print("Loading location resolver...")
        resolver = LocationResolver()
        await resolver.load(pool)
        print(
            f"  loaded {len(resolver._entries)} locations, "
            f"{len(resolver._name_to_ids)} unique names"
        )

        # Fetch postings
        print(f"Fetching postings (limit={args.limit})...")
        rows = await conn.fetch(_FETCH_POSTINGS, args.limit)
        print(f"  found {len(rows)} postings with locations but no location_ids")

        if not rows:
            return 0

        # Resolve
        matched = 0
        unmatched_strings: list[str] = []
        updates: list[tuple[str, list[int], list[str]]] = []

        for row in rows:
            raw_locations = row["locations"]
            lang = row["lang"] or None

            results = resolver.resolve(raw_locations, None, posting_language=lang)
            if results:
                # Build parallel arrays — only include entries with location_ids
                final_ids = []
                final_types = []
                for r in results:
                    if r.location_id is not None:
                        final_ids.append(r.location_id)
                        final_types.append(r.location_type)

                if not final_ids:
                    continue

                updates.append((row["id"], final_ids, final_types))
                matched += 1
            else:
                for loc in raw_locations:
                    if loc and loc.strip():
                        unmatched_strings.append(loc.strip())

        total = len(rows)
        match_rate = matched / total * 100 if total else 0
        print(f"\nResolved: {matched}/{total} ({match_rate:.1f}%)")
        print(f"Unmatched strings: {len(unmatched_strings)}")

        # Log unmatched
        if args.log_unmatched and unmatched_strings:
            # Deduplicate and sort by frequency
            from collections import Counter

            counter = Counter(unmatched_strings)
            with open(args.log_unmatched, "w") as f:
                for string, count in counter.most_common():
                    f.write(f"{count:4d}  {string}\n")
            print(f"Wrote unmatched strings to {args.log_unmatched}")

        if args.dry_run:
            print("DRY RUN — skipping DB update")
            return 0

        # Batch update
        print(f"Updating {len(updates)} rows...")
        updated = 0
        for i in range(0, len(updates), _BATCH_SIZE):
            batch = updates[i : i + _BATCH_SIZE]
            async with conn.transaction():
                await conn.execute("""
                    CREATE TEMP TABLE _loc_updates (
                        id uuid,
                        location_ids integer[],
                        location_types text[]
                    ) ON COMMIT DROP
                """)
                await conn.copy_records_to_table(
                    "_loc_updates",
                    records=[(uid, ids, types) for uid, ids, types in batch],
                    columns=["id", "location_ids", "location_types"],
                )
                result = await conn.execute("""
                    UPDATE job_posting AS jp
                    SET location_ids = u.location_ids,
                        location_types = u.location_types
                    FROM _loc_updates u
                    WHERE jp.id = u.id
                """)
                count = int(result.split()[-1]) if result else 0
                updated += count

            print(f"  batch {i // _BATCH_SIZE + 1}: updated {count} rows")

        print(f"\nTotal updated: {updated}")
    finally:
        await pool.close()
        await conn.close()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
