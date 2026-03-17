"""Backfill seniority_id on existing job_posting rows.

Re-evaluates ALL active postings against seniority patterns, reporting
new fills, corrections, and distribution for taxonomy improvement.

Usage:
  uv run python scripts/backfill_seniority.py                  # dry-run (default)
  uv run python scripts/backfill_seniority.py --write          # apply changes
  uv run python scripts/backfill_seniority.py --limit 5000     # limit rows
  uv run python scripts/backfill_seniority.py --log unmatched_seniority.txt
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

import asyncpg

from src.config import settings
from src.core.seniority_resolve import load_seniority_ids, match_seniority

_FETCH_ALL = """
SELECT id::text,
       titles,
       seniority_id AS current_seniority_id
FROM job_posting
WHERE is_active = true
ORDER BY first_seen_at DESC
LIMIT $1
"""

_BATCH_SIZE = 500


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill seniority_id from titles")
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


def _best_match(titles: list[str] | None) -> tuple[str | None, str | None]:
    """Try to match seniority from titles.

    Returns (slug, source_string) or (None, best_unmatched_string).
    """
    if titles:
        for title in titles:
            if title and title.strip():
                slug = match_seniority(title)
                if slug:
                    return slug, title

    best = titles[0] if titles else None
    return None, best


async def _run() -> int:
    args = _parse_args()

    pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=2, statement_cache_size=0
    )
    assert pool is not None

    try:
        print("Loading seniority IDs from DB...")
        sen_ids = await load_seniority_ids(pool)
        id_to_slug = {v: k for k, v in sen_ids.items()}
        if not sen_ids:
            print("ERROR: No seniority levels in DB. Run `uv run python -m src.sync` first.")
            return 1
        print(f"  {len(sen_ids)} seniority levels loaded")

        async with pool.acquire() as conn:
            print(f"Fetching all active postings (limit={args.limit})...")
            rows = await conn.fetch(_FETCH_ALL, args.limit)

        print(f"  {len(rows)} active postings")

        if not rows:
            print("Nothing to process.")
            return 0

        # Classify each posting
        new_fills: list[tuple[str, int]] = []
        corrections: list[tuple[str, int, int]] = []
        unchanged = 0
        cleared: list[tuple[str, str]] = []
        unmatched_strings: list[str] = []
        matched_slugs: Counter[str] = Counter()

        for row in rows:
            current_id = row["current_seniority_id"]
            slug, source = _best_match(row["titles"])

            if slug and slug in sen_ids:
                new_id = sen_ids[slug]
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
        total_undetectable = total - total_matched - len(cleared)

        # ── Report ──
        print(f"\n{'═' * 60}")
        print("SENIORITY BACKFILL REPORT")
        print(f"{'═' * 60}")
        print(f"Total active postings:  {total}")
        print(f"Matched:                {total_matched} ({total_matched / total * 100:.1f}%)")
        print(f"  Unchanged:            {unchanged}")
        print(f"  New fills:            {len(new_fills)}")
        print(f"  Corrections:          {len(corrections)}")
        pct = total_undetectable / total * 100
        print(f"No seniority in title:  {total_undetectable} ({pct:.1f}%)")
        if cleared:
            print(f"Would clear (false pos?): {len(cleared)}")

        if matched_slugs:
            # Order by seniority level
            level_order = [
                "intern",
                "entry",
                "mid",
                "senior",
                "lead",
                "staff",
                "principal",
                "director",
                "executive",
            ]
            print("\nSeniority distribution:")
            for slug in level_order:
                if slug in matched_slugs:
                    pct = matched_slugs[slug] / total_matched * 100 if total_matched else 0
                    bar = "█" * int(pct / 2)
                    print(f"  {slug:12s}  {matched_slugs[slug]:5d}  {pct:5.1f}%  {bar}")

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
                print(f"  {count:4d}  {old_slug:12s} ← {title}")

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
                        CREATE TEMP TABLE _sen_updates (
                            id uuid,
                            seniority_id integer
                        ) ON COMMIT DROP
                    """)
                    await conn.copy_records_to_table(
                        "_sen_updates",
                        records=batch,
                        columns=["id", "seniority_id"],
                    )
                    result = await conn.execute("""
                        UPDATE job_posting AS jp
                        SET seniority_id = u.seniority_id
                        FROM _sen_updates u
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
