"""Backfill technology tags on existing job postings.

Reads cached HTML descriptions from data/descriptions_cache/ and extracts
technologies using the deterministic regex resolver. In dry-run mode (default),
reports statistics and sample matches. In write mode, updates the DB
(requires technology table + technology_ids column to exist).

Usage:
  uv run python scripts/backfill_technologies.py --dry-run
  uv run python scripts/backfill_technologies.py --dry-run --report-file tech_report.txt
  uv run python scripts/backfill_technologies.py --limit 5000
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

import asyncpg

from src.config import settings
from src.core.technology_resolve import load_technology_ids, match_technologies

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "descriptions_cache"

_BATCH_SIZE = 500


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill technology_ids from descriptions")
    parser.add_argument("--limit", type=int, default=0, help="Max files to process (0 = all)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Resolve from cache only, don't update DB"
    )
    parser.add_argument(
        "--report-file",
        type=str,
        default=None,
        help="Write detailed per-posting report to file",
    )
    parser.add_argument(
        "--min-techs",
        type=int,
        default=0,
        help="Only report postings with >= N technologies (for --report-file)",
    )
    parser.add_argument(
        "--show-unmatched",
        type=int,
        default=20,
        help="Number of zero-match sample filenames to show",
    )
    return parser.parse_args()


def _process_cache(
    limit: int,
    min_techs: int,
    report_file: str | None,
    show_unmatched: int,
) -> None:
    """Process local description cache and report statistics."""
    if not CACHE_DIR.exists():
        print(f"Cache directory not found: {CACHE_DIR}")
        sys.exit(1)

    files = sorted(CACHE_DIR.glob("*.html"))
    total = len(files)
    if limit > 0:
        files = files[:limit]

    print(f"Processing {len(files)} of {total} cached descriptions\n")

    tech_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    postings_by_tech_count: Counter[int] = Counter()
    matched_files = 0
    unmatched_files: list[str] = []
    report_lines: list[str] = []

    # Load category mapping for reporting
    import polars as pl

    csv_path = Path(__file__).resolve().parent.parent / "data" / "technologies.csv"
    df = pl.read_csv(csv_path, infer_schema_length=0)
    slug_to_category = {row["slug"]: row["category"] for row in df.iter_rows(named=True)}
    slug_to_name = {row["slug"]: row["name"] for row in df.iter_rows(named=True)}

    for i, f in enumerate(files):
        if (i + 1) % 5000 == 0:
            print(f"  ... {i + 1}/{len(files)}")

        html = f.read_text(encoding="utf-8", errors="replace")
        slugs = match_technologies(html)
        n = len(slugs)
        postings_by_tech_count[n] += 1

        if slugs:
            matched_files += 1
            for slug in slugs:
                tech_counts[slug] += 1
                cat = slug_to_category.get(slug, "unknown")
                category_counts[cat] += 1
        else:
            if len(unmatched_files) < show_unmatched:
                unmatched_files.append(f.stem)

        if report_file and n >= min_techs:
            names = [slug_to_name.get(s, s) for s in slugs]
            report_lines.append(f"{f.stem}\t{n}\t{', '.join(sorted(names))}")

    processed = len(files)
    match_rate = matched_files / processed * 100 if processed else 0

    # ── Summary ──
    print(f"\n{'=' * 70}")
    print(f"Processed: {processed}")
    print(f"With >= 1 tech: {matched_files} ({match_rate:.1f}%)")
    print(f"Zero techs: {processed - matched_files} ({100 - match_rate:.1f}%)")
    print(f"Unique techs matched: {len(tech_counts)}")
    print(f"{'=' * 70}")

    # ── Distribution by tech count ──
    print("\nTechnologies per posting:")
    for n_techs in sorted(postings_by_tech_count):
        count = postings_by_tech_count[n_techs]
        pct = count / processed * 100
        bar = "#" * max(1, int(pct))
        print(f"  {n_techs:3d} techs: {count:6d} ({pct:5.1f}%) {bar}")

    # ── By category ──
    print("\nMatches by category:")
    for cat, count in category_counts.most_common():
        print(f"  {cat:<15s} {count:>7d}")

    # ── Top technologies ──
    print("\nTop 40 technologies:")
    for slug, count in tech_counts.most_common(40):
        name = slug_to_name.get(slug, slug)
        cat = slug_to_category.get(slug, "?")
        pct = count / processed * 100
        print(f"  {count:6d} ({pct:4.1f}%)  {name:<25s} [{cat}]")

    # ── Bottom technologies (low signal) ──
    bottom = tech_counts.most_common()[-20:]
    bottom.reverse()
    print("\nBottom 20 technologies (consider removing if noise):")
    for slug, count in bottom:
        name = slug_to_name.get(slug, slug)
        cat = slug_to_category.get(slug, "?")
        print(f"  {count:6d}  {name:<25s} [{cat}]")

    # ── Unmatched samples ──
    if unmatched_files:
        print(f"\nSample unmatched posting IDs ({len(unmatched_files)} shown):")
        for pid in unmatched_files[:show_unmatched]:
            print(f"  {pid}")

    # ── Write report ──
    if report_file and report_lines:
        with open(report_file, "w") as f:
            f.write("posting_id\ttech_count\ttechnologies\n")
            for line in sorted(report_lines, key=lambda x: -int(x.split("\t")[1])):
                f.write(line + "\n")
        print(f"\nWrote {len(report_lines)} rows to {report_file}")


async def _run_db() -> int:
    """DB-backed backfill (requires technology table to exist)."""
    args = _parse_args()

    if args.dry_run:
        _process_cache(args.limit, args.min_techs, args.report_file, args.show_unmatched)
        print("\nDRY RUN — no DB changes")
        return 0

    # ── Live DB update path ──
    pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=2, statement_cache_size=0
    )
    assert pool is not None

    try:
        # Check if technology table exists
        async with pool.acquire() as conn:
            exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'technology'
                )
            """)
            if not exists:
                print("ERROR: technology table does not exist yet.")
                print("Run the migration first, or use --dry-run to preview from cache.")
                return 1

            col_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_name = 'job_posting' AND column_name = 'technology_ids'
                )
            """)
            if not col_exists:
                print("ERROR: job_posting.technology_ids column does not exist yet.")
                print("Run the migration first, or use --dry-run to preview from cache.")
                return 1

        print("Loading technology IDs from DB...")
        tech_ids = await load_technology_ids(pool)
        if not tech_ids:
            print("ERROR: No technologies in DB. Run `uv run python -m src.sync` first.")
            return 1
        print(f"  {len(tech_ids)} technologies loaded")

        # Fetch postings that have a description but no technology_ids
        async with pool.acquire() as conn:
            limit = args.limit if args.limit > 0 else 100_000
            rows = await conn.fetch(
                """
                SELECT id::text, description_r2_hash
                FROM job_posting
                WHERE is_active = true
                  AND description_r2_hash IS NOT NULL
                  AND technology_ids IS NULL
                ORDER BY first_seen_at DESC
                LIMIT $1
            """,
                limit,
            )

        print(f"  {len(rows)} postings to process")
        if not rows:
            print("Nothing to backfill.")
            return 0

        # Read descriptions from cache and resolve
        updates: list[tuple[str, list[int]]] = []
        no_cache = 0
        no_match = 0
        tech_counter: Counter[str] = Counter()

        for row in rows:
            pid = row["id"]
            cache_file = CACHE_DIR / f"{pid}.html"
            if not cache_file.exists():
                no_cache += 1
                continue

            html = cache_file.read_text(encoding="utf-8", errors="replace")
            slugs = match_technologies(html)
            if not slugs:
                no_match += 1
                continue

            ids = sorted({tech_ids[s] for s in slugs if s in tech_ids})
            if ids:
                updates.append((pid, ids))
                for s in slugs:
                    if s in tech_ids:
                        tech_counter[s] += 1

        print(f"\nResolved: {len(updates)}/{len(rows)}")
        print(f"  No cache file: {no_cache}")
        print(f"  No tech match: {no_match}")

        if tech_counter:
            print("\nTop technologies:")
            for slug, count in tech_counter.most_common(20):
                print(f"  {count:5d}  {slug}")

        if not updates:
            print("\nNo matches to write.")
            return 0

        # Batch update
        print(f"\nUpdating {len(updates)} rows...")
        updated = 0
        async with pool.acquire() as conn:
            for i in range(0, len(updates), _BATCH_SIZE):
                batch = updates[i : i + _BATCH_SIZE]
                for pid, ids in batch:
                    result = await conn.execute(
                        "UPDATE job_posting SET technology_ids = $1 WHERE id = $2",
                        ids,
                        pid,
                    )
                    if result and result.endswith("1"):
                        updated += 1

                done = min(i + _BATCH_SIZE, len(updates))
                print(f"  {done}/{len(updates)} processed")

        print(f"\nTotal updated: {updated}")
    finally:
        await pool.close()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run_db()))


if __name__ == "__main__":
    main()
