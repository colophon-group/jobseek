"""Backfill occupation_id on existing job_posting rows.

Re-evaluates ALL active postings against the occupation taxonomy, reporting
new fills, corrections, and unmatched titles for taxonomy improvement.

Usage:
  uv run python scripts/backfill_occupations.py                  # dry-run (default)
  uv run python scripts/backfill_occupations.py --write          # apply changes
  uv run python scripts/backfill_occupations.py --limit 5000     # limit rows
  uv run python scripts/backfill_occupations.py --log unmatched_occupations.txt
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

import asyncpg

from src.config import settings
from src.core.occupation_resolve import load_occupation_ids, match_occupation

_FETCH_ALL = """
SELECT id::text,
       titles,
       occupation_id AS current_occupation_id,
       enrichment->>'occupation' AS enrichment_occupation
FROM job_posting
WHERE is_active = true
ORDER BY first_seen_at DESC
LIMIT $1
"""

_BATCH_SIZE = 500


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill occupation_id from titles + enrichment")
    parser.add_argument("--limit", type=int, default=200_000, help="Max rows to process")
    parser.add_argument(
        "--write", action="store_true", help="Apply changes to DB (default: dry-run)"
    )
    parser.add_argument(
        "--log",
        type=str,
        default=None,
        help="File to log unmatched title strings (by frequency)",
    )
    return parser.parse_args()


def _best_match(
    titles: list[str] | None, enrichment_occ: str | None
) -> tuple[str | None, str | None]:
    """Try to match occupation from enrichment first, then titles.

    Returns (slug, source_string) or (None, best_unmatched_string).
    """
    if enrichment_occ:
        slug = match_occupation(enrichment_occ)
        if slug:
            return slug, enrichment_occ

    if titles:
        for title in titles:
            if title and title.strip():
                slug = match_occupation(title)
                if slug:
                    return slug, title

    best = enrichment_occ or (titles[0] if titles else None)
    return None, best


async def _run() -> int:
    args = _parse_args()

    pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=2, statement_cache_size=0
    )
    assert pool is not None

    try:
        print("Loading occupation IDs from DB...")
        occ_ids = await load_occupation_ids(pool)
        id_to_slug = {v: k for k, v in occ_ids.items()}
        if not occ_ids:
            print("ERROR: No occupations in DB. Run `uv run python -m src.sync` first.")
            return 1
        print(f"  {len(occ_ids)} occupations loaded")

        async with pool.acquire() as conn:
            print(f"Fetching all active postings (limit={args.limit})...")
            rows = await conn.fetch(_FETCH_ALL, args.limit)

        print(f"  {len(rows)} active postings")

        if not rows:
            print("Nothing to process.")
            return 0

        # Classify each posting
        new_fills: list[tuple[str, int]] = []  # NULL → matched
        corrections: list[tuple[str, int, int]] = []  # old_id → new_id (different)
        unchanged = 0
        cleared: list[tuple[str, str]] = []  # had value, now no match (potential false positive)
        unmatched_strings: list[str] = []
        matched_slugs: Counter[str] = Counter()

        for row in rows:
            current_id = row["current_occupation_id"]
            slug, source = _best_match(row["titles"], row["enrichment_occupation"])

            if slug and slug in occ_ids:
                new_id = occ_ids[slug]
                matched_slugs[slug] += 1

                if current_id is None:
                    new_fills.append((row["id"], new_id))
                elif current_id != new_id:
                    corrections.append((row["id"], current_id, new_id))
                else:
                    unchanged += 1
            else:
                if current_id is not None:
                    old_slug = id_to_slug.get(current_id, f"id={current_id}")
                    title = row["titles"][0] if row["titles"] else "?"
                    cleared.append((title, old_slug))
                elif source:
                    unmatched_strings.append(source.strip())

        total = len(rows)
        total_matched = unchanged + len(new_fills) + len(corrections)

        # ── Report ──
        print(f"\n{'═' * 60}")
        print("OCCUPATION BACKFILL REPORT")
        print(f"{'═' * 60}")
        print(f"Total active postings:  {total}")
        print(f"Matched:                {total_matched} ({total_matched / total * 100:.1f}%)")
        print(f"  Unchanged:            {unchanged}")
        print(f"  New fills:            {len(new_fills)}")
        print(f"  Corrections:          {len(corrections)}")
        print(f"Unmatched:              {len(unmatched_strings)}")
        if cleared:
            print(f"Would clear (false pos?): {len(cleared)}")

        if matched_slugs:
            print("\nOccupation distribution (top 30):")
            for slug, count in matched_slugs.most_common(30):
                print(f"  {count:5d}  {slug}")

        if corrections:
            print("\nCorrections (top 20):")
            for _, old_id, new_id in corrections[:20]:
                old_slug = id_to_slug.get(old_id, f"id={old_id}")
                new_slug = id_to_slug.get(new_id, f"id={new_id}")
                print(f"  {old_slug} → {new_slug}")

        if cleared:
            print("\nPotential false positives — currently set but no match (top 20):")
            cleared_counter = Counter(cleared)
            for (title, old_slug), count in cleared_counter.most_common(20):
                print(f"  {count:4d}  {old_slug:30s} ← {title}")

        unmatched_counter = Counter(unmatched_strings)
        if unmatched_counter:
            print(f"\nTop unmatched titles ({len(unmatched_counter)} unique):")
            for title, count in unmatched_counter.most_common(40):
                print(f"  {count:5d}  {title}")

        if args.log and unmatched_counter:
            with open(args.log, "w") as f:
                for string, count in unmatched_counter.most_common():
                    f.write(f"{count:4d}  {string}\n")
            print(f"\nWrote {len(unmatched_counter)} unmatched strings to {args.log}")

        if not args.write:
            print("\nDRY RUN — pass --write to apply changes")
            return 0

        # Apply: new fills + corrections
        updates = [(pid, nid) for pid, nid in new_fills]
        updates += [(pid, nid) for pid, _, nid in corrections]

        if not updates:
            print("\nNo changes to write.")
            return 0

        print(f"\nWriting {len(updates)} changes...")
        updated = 0
        async with pool.acquire() as conn:
            for i in range(0, len(updates), _BATCH_SIZE):
                batch = updates[i : i + _BATCH_SIZE]
                async with conn.transaction():
                    await conn.execute("""
                        CREATE TEMP TABLE _occ_updates (
                            id uuid,
                            occupation_id integer
                        ) ON COMMIT DROP
                    """)
                    await conn.copy_records_to_table(
                        "_occ_updates",
                        records=batch,
                        columns=["id", "occupation_id"],
                    )
                    result = await conn.execute("""
                        UPDATE job_posting AS jp
                        SET occupation_id = u.occupation_id
                        FROM _occ_updates u
                        WHERE jp.id = u.id
                    """)
                    count = int(result.split()[-1]) if result else 0
                    updated += count

                print(f"  batch {i // _BATCH_SIZE + 1}: {count} rows")

        print(f"\nTotal updated: {updated}")
    finally:
        await pool.close()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
