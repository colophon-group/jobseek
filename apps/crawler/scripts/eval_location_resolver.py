"""Evaluate location resolver accuracy on real job posting data.

Samples 100 unique (location, language) pairs from job_posting,
runs them through the resolver with the posting language as a hint,
and prints results for manual review.

Usage:
    uv run python scripts/eval_location_resolver.py
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

from src.core.location_resolve import LocationResolver


async def main() -> None:
    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"],
        min_size=1,
        max_size=2,
        statement_cache_size=0,
    )
    assert pool

    resolver = LocationResolver()
    await resolver.load(pool)

    # Sample 100 (location, language) pairs from active postings
    rows = await pool.fetch("""
        SELECT loc, lang FROM (
            SELECT DISTINCT ON (loc)
                unnest(locations) AS loc,
                locales[1] AS lang
            FROM job_posting
            WHERE is_active = true
              AND locations IS NOT NULL
              AND array_length(locations, 1) > 0
        ) sub
        ORDER BY random()
        LIMIT 100
    """)

    print(f"Sampled {len(rows)} unique location strings\n")
    print(f"{'#':>3}  {'Input':<50} {'Lang':<5} {'Type':<8} {'Resolved'}")
    print("-" * 120)

    unresolved = 0
    for i, row in enumerate(rows, 1):
        raw = row["loc"]
        lang = row["lang"] or None
        results = resolver.resolve([raw], posting_language=lang)

        if not results:
            print(f"{i:>3}  {raw:<50} {(lang or '—'):<5} {'—':<8} *** UNRESOLVED ***")
            unresolved += 1
        else:
            for j, r in enumerate(results):
                prefix = f"{i:>3}" if j == 0 else "   "
                if r.location_id:
                    name = resolver.display_name(r.location_id) or f"id={r.location_id}"
                else:
                    name = "(no geo)"
                inp = raw if j == 0 else ""
                lang_col = (lang or "—") if j == 0 else ""
                print(f"{prefix}  {inp:<50} {lang_col:<5} {r.location_type:<8} {name}")

    print("-" * 120)
    resolved = len(rows) - unresolved
    print(f"\nResolved: {resolved}/{len(rows)} ({resolved / len(rows) * 100:.1f}%)")
    print(f"Unresolved: {unresolved}/{len(rows)} ({unresolved / len(rows) * 100:.1f}%)")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
