"""Backfill salary and experience data on existing job postings.

Reads cached HTML descriptions from data/descriptions_cache/ and extracts
salary ranges + experience requirements using heuristic extractors.
In dry-run mode (default), reports statistics. In write mode, updates the DB.

Usage:
  uv run python scripts/backfill_salary_experience.py --dry-run
  uv run python scripts/backfill_salary_experience.py --limit 5000
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

import asyncpg

from src.config import settings
from src.core.experience_extract import extract_experience
from src.core.salary_extract import extract_salary_unified

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "descriptions_cache"

_BATCH_SIZE = 500


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill salary + experience from descriptions")
    parser.add_argument("--limit", type=int, default=0, help="Max files to process (0 = all)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Resolve from cache only, don't update DB"
    )
    return parser.parse_args()


def _process_cache(limit: int) -> None:
    """Process local description cache and report statistics."""
    if not CACHE_DIR.exists():
        print(f"Cache directory not found: {CACHE_DIR}")
        sys.exit(1)

    files = sorted(CACHE_DIR.glob("*.html"))
    total = len(files)
    if limit > 0:
        files = files[:limit]

    print(f"Processing {len(files)} of {total} cached descriptions\n")

    currency_counts: Counter[str] = Counter()
    period_counts: Counter[str] = Counter()
    salary_hits = 0
    experience_hits = 0
    exp_histogram: Counter[int] = Counter()
    salary_ranges: list[tuple[int, int | None, str]] = []

    for i, f in enumerate(files):
        if (i + 1) % 5000 == 0:
            print(f"  ... {i + 1}/{len(files)}")

        html = f.read_text(encoding="utf-8", errors="replace")

        sr = extract_salary_unified(html)
        if sr:
            salary_hits += 1
            currency_counts[sr.currency] += 1
            period_counts[sr.period] += 1
            # Normalize to annual for stats
            if sr.period == "hourly":
                min_annual = round(sr.min / 100 * 2080)
                max_annual = round(sr.max / 100 * 2080) if sr.max else None
            elif sr.period == "monthly":
                min_annual = sr.min * 12
                max_annual = sr.max * 12 if sr.max else None
            else:
                min_annual = sr.min
                max_annual = sr.max
            salary_ranges.append((min_annual, max_annual, sr.currency))

        exp = extract_experience(html)
        if exp:
            experience_hits += 1
            exp_histogram[exp.min_years] += 1

    processed = len(files)
    sal_rate = salary_hits / processed * 100 if processed else 0
    exp_rate = experience_hits / processed * 100 if processed else 0

    # ── Summary ──
    print(f"\n{'=' * 70}")
    print(f"Processed: {processed}")
    print(f"{'=' * 70}")

    print(f"\nSalary: {salary_hits} ({sal_rate:.1f}%)")
    print("\nBy currency:")
    for cur, count in currency_counts.most_common():
        print(f"  {cur}: {count}")
    print("\nBy period:")
    for per, count in period_counts.most_common():
        print(f"  {per}: {count}")

    if salary_ranges:
        usd = [r for r in salary_ranges if r[2] == "USD"]
        eur = [r for r in salary_ranges if r[2] == "EUR"]
        for label, subset in [("USD", usd), ("EUR", eur)]:
            if subset:
                mins = [r[0] for r in subset]
                print(f"\n{label} annual range: {min(mins):,} - {max(mins):,} (min values)")

    print(f"\nExperience: {experience_hits} ({exp_rate:.1f}%)")
    print("\nExperience histogram (min years):")
    for years in sorted(exp_histogram):
        count = exp_histogram[years]
        pct = count / processed * 100
        bar = "#" * max(1, int(pct * 2))
        print(f"  {years:2d}y: {count:5d} ({pct:4.1f}%) {bar}")


async def _run_db() -> int:
    """DB-backed backfill."""
    args = _parse_args()

    if args.dry_run:
        _process_cache(args.limit)
        print("\nDRY RUN — no DB changes")
        return 0

    pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=2, statement_cache_size=0
    )
    assert pool is not None

    try:
        # Check columns exist
        async with pool.acquire() as conn:
            col_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_name = 'job_posting' AND column_name = 'salary_min'
                )
            """)
            if not col_exists:
                print("ERROR: salary columns do not exist yet.")
                print("Run the migration first, or use --dry-run to preview from cache.")
                return 1

        # Load currency rates
        async with pool.acquire() as conn:
            rate_rows = await conn.fetch("SELECT currency, to_eur FROM currency_rate")
        rates: dict[str, float] = {r["currency"]: float(r["to_eur"]) for r in rate_rows}
        print(f"Loaded {len(rates)} currency rates")

        # Fetch postings that have a description but no salary/experience data
        async with pool.acquire() as conn:
            limit = args.limit if args.limit > 0 else 200_000
            rows = await conn.fetch(
                """
                SELECT id::text
                FROM job_posting
                WHERE is_active = true
                  AND description_r2_hash IS NOT NULL
                  AND salary_min IS NULL
                  AND experience_min IS NULL
                ORDER BY first_seen_at DESC
                LIMIT $1
            """,
                limit,
            )

        print(f"  {len(rows)} postings to process")
        if not rows:
            print("Nothing to backfill.")
            return 0

        updates: list[
            tuple[str, int | None, int | None, str | None, str | None, int | None, int | None]
        ] = []
        no_cache = 0
        no_match = 0
        currency_counts: Counter[str] = Counter()

        for row in rows:
            pid = row["id"]
            cache_file = CACHE_DIR / f"{pid}.html"
            if not cache_file.exists():
                no_cache += 1
                continue

            html = cache_file.read_text(encoding="utf-8", errors="replace")

            sr = extract_salary_unified(html)
            exp = extract_experience(html)

            if sr is None and exp is None:
                no_match += 1
                continue

            s_min = s_max = s_eur = None
            s_cur = s_per = None
            if sr:
                if sr.period == "hourly":
                    s_min = round(sr.min / 100 * 2080)
                    s_max = round(sr.max / 100 * 2080) if sr.max else None
                elif sr.period == "monthly":
                    s_min = sr.min * 12
                    s_max = sr.max * 12 if sr.max else None
                else:
                    s_min = sr.min
                    s_max = sr.max
                s_cur = sr.currency
                s_per = sr.period
                to_eur = rates.get(sr.currency, 0)
                s_eur = round(s_min * to_eur) if to_eur > 0 else None
                currency_counts[sr.currency] += 1

            exp_min = exp.min_years if exp else None
            exp_max = exp.max_years if exp else None
            updates.append((pid, s_min, s_max, s_cur, s_per, s_eur, exp_min, exp_max))

        print(f"\nResolved: {len(updates)}/{len(rows)}")
        print(f"  No cache file: {no_cache}")
        print(f"  No match: {no_match}")

        if currency_counts:
            print("\nCurrency distribution:")
            for cur, count in currency_counts.most_common():
                print(f"  {cur}: {count}")

        if not updates:
            print("\nNo matches to write.")
            return 0

        # Batch update
        print(f"\nUpdating {len(updates)} rows...")
        updated = 0
        async with pool.acquire() as conn:
            for i in range(0, len(updates), _BATCH_SIZE):
                batch = updates[i : i + _BATCH_SIZE]
                for pid, s_min, s_max, s_cur, s_per, s_eur, exp_min, exp_max in batch:
                    result = await conn.execute(
                        """
                        UPDATE job_posting
                        SET salary_min = $2, salary_max = $3,
                            salary_currency = $4, salary_period = $5,
                            salary_eur = $6,
                            experience_min = $7, experience_max = $8
                        WHERE id = $1
                    """,
                        pid,
                        s_min,
                        s_max,
                        s_cur,
                        s_per,
                        s_eur,
                        exp_min,
                        exp_max,
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
