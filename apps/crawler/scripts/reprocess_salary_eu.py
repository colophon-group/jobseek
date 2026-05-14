#!/usr/bin/env python
"""One-shot reprocess of salary extraction over active EU postings.

Re-runs ``_extract_salary_fields`` over each posting's primary-locale
description HTML for one of two country sets:

* ``scope-a`` (default; PR #3324 / issue #3324) — 7 non-Eurozone currencies
  added by PR #3269: PL, CZ, SE, DK, HU, RO, BG.
* ``scope-b`` (issue #3341) — EUR/GBP/CHF locale failures fixed by PR #3342:
  DE, AT, CH, FR, IT, ES, NL, BE, UK, IE, PT, GR.

Writes back ``salary_min`` / ``salary_max`` / ``salary_currency`` /
``salary_period`` / ``salary_eur`` and bumps ``updated_at`` only when at
least one field actually changes — so the exporter's ``(updated_at, id)``
cursor reflows just the touched rows to Supabase + Typesense.

Idempotent: re-running produces no additional UPDATEs once values match.

Usage:
    # Recon + before counts only (scope-a)
    uv run python scripts/reprocess_salary_eu.py --stats

    # Dry-run (no writes; prints summary + N proposed changes per country)
    uv run python scripts/reprocess_salary_eu.py --dry-run [--samples 5]

    # Live run
    uv run python scripts/reprocess_salary_eu.py --live

    # Scope B (EUR/GBP/CHF locales)
    uv run python scripts/reprocess_salary_eu.py --countries-set scope-b --dry-run
    uv run python scripts/reprocess_salary_eu.py --countries-set scope-b --live

    # Both scopes together
    uv run python scripts/reprocess_salary_eu.py --countries-set all --dry-run

The script connects via ``LOCAL_DATABASE_URL`` from the environment
(or from ``apps/crawler/.env.local`` if loaded).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import asyncpg

# Make ``src`` importable when running directly from apps/crawler/.
HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from src.processing.cpu import _extract_salary_fields  # noqa: E402

# GeoNames IDs for the 7 EU countries added in PR #3269 (Scope A).
SCOPE_A_COUNTRY_IDS: dict[str, int] = {
    "PL": 798544,  # Poland
    "CZ": 3077311,  # Czechia
    "SE": 2661886,  # Kingdom of Sweden
    "DK": 2623032,  # Denmark
    "HU": 719819,  # Hungary
    "RO": 798549,  # Romania
    "BG": 732800,  # Bulgaria
}

# GeoNames IDs for the 12 EU locale-aware countries from PR #3342 (Scope B):
# EUR/GBP/CHF currencies, locale-aware extraction templates.
SCOPE_B_COUNTRY_IDS: dict[str, int] = {
    "DE": 2921044,  # Germany
    "AT": 2782113,  # Austria
    "CH": 2658434,  # Switzerland
    "FR": 3017382,  # France
    "IT": 3175395,  # Italy
    "ES": 2510769,  # Spain
    "NL": 2750405,  # Netherlands
    "BE": 2802361,  # Belgium
    "UK": 2635167,  # United Kingdom
    "IE": 2963597,  # Ireland
    "PT": 2264397,  # Portugal
    "GR": 390903,  # Greece
}

# Backwards-compat alias; defaults to scope-a so prior CLI invocations
# (without --countries-set) keep their original behaviour.
COUNTRY_IDS: dict[str, int] = SCOPE_A_COUNTRY_IDS


def _resolve_country_ids(scope: str) -> dict[str, int]:
    """Return the country->GeoNames-id mapping for the requested scope."""
    if scope == "scope-a":
        return dict(SCOPE_A_COUNTRY_IDS)
    if scope == "scope-b":
        return dict(SCOPE_B_COUNTRY_IDS)
    if scope == "all":
        merged: dict[str, int] = {}
        merged.update(SCOPE_A_COUNTRY_IDS)
        merged.update(SCOPE_B_COUNTRY_IDS)
        return merged
    raise ValueError(f"unknown countries-set: {scope!r}")


FETCH_BATCH = 1000  # rows per fetch chunk


def _load_env_local() -> None:
    """Best-effort loader for apps/crawler/.env.local — KEY=VALUE lines."""
    env_path = APP_DIR / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


async def _descendant_ids(conn: asyncpg.Connection, country_id: int) -> list[int]:
    """Return all location ids in the subtree rooted at ``country_id``."""
    rows = await conn.fetch(
        """
        WITH RECURSIVE descendants AS (
            SELECT id, parent_id FROM location WHERE id = $1
            UNION ALL
            SELECT l.id, l.parent_id
              FROM location l
              JOIN descendants d ON l.parent_id = d.id
        )
        SELECT id FROM descendants
        """,
        country_id,
    )
    return [r["id"] for r in rows]


async def _currency_rates(conn: asyncpg.Connection) -> dict[str, float]:
    rows = await conn.fetch("SELECT currency, to_eur FROM currency_rate")
    return {r["currency"]: float(r["to_eur"]) for r in rows}


async def _before_counts(
    conn: asyncpg.Connection, descendants_by_label: dict[str, list[int]]
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for label, ids in descendants_by_label.items():
        rec = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE salary_eur > 0) AS with_salary
              FROM job_posting
             WHERE is_active AND location_ids && $1::int[]
            """,
            ids,
        )
        out[label] = dict(rec)
    return out


async def _iter_country_rows(read_conn: asyncpg.Connection, ids: list[int], limit: int | None):
    """Yield rows for one country via a server-side cursor (in a transaction)."""
    sql = """
        SELECT jp.id,
               jp.salary_min, jp.salary_max, jp.salary_currency,
               jp.salary_period, jp.salary_eur,
               jp.locales,
               d.html
          FROM job_posting jp
          JOIN LATERAL (
              SELECT html
                FROM descriptions
               WHERE posting_id = jp.id
               ORDER BY (locale = jp.locales[1]) DESC NULLS LAST, updated_at DESC
               LIMIT 1
          ) d ON true
         WHERE jp.is_active
           AND jp.location_ids && $1::int[]
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    async with read_conn.transaction():
        async for row in read_conn.cursor(sql, ids, prefetch=FETCH_BATCH):
            yield row


def _diff_payload(row, new) -> dict | None:
    """Return dict of changes (db field -> new value) or None if identical."""
    s_min, s_max, s_cur, s_per, s_eur = new
    changes: dict[str, object | None] = {}
    if row["salary_min"] != s_min:
        changes["salary_min"] = s_min
    if row["salary_max"] != s_max:
        changes["salary_max"] = s_max
    if row["salary_currency"] != s_cur:
        changes["salary_currency"] = s_cur
    if row["salary_period"] != s_per:
        changes["salary_period"] = s_per
    if row["salary_eur"] != s_eur:
        changes["salary_eur"] = s_eur
    return changes or None


def _classify(row, new) -> str:
    """Bucket the kind of change for reporting."""
    s_min, s_max, s_cur, s_per, s_eur = new
    had = row["salary_eur"] is not None and row["salary_eur"] > 0
    has = s_eur is not None and s_eur > 0
    if not had and has:
        return "added_salary_eur"
    if had and not has:
        return "lost_salary_eur"
    if had and has and row["salary_eur"] != s_eur:
        return "changed_salary_eur"
    if not had and not has and (row["salary_currency"] != s_cur or row["salary_min"] != s_min):
        # extracted raw values without EUR conversion (e.g. unknown rate)
        return "raw_only_change"
    return "other_change"


_UPDATE_SQL = """
UPDATE job_posting
   SET salary_min      = $2,
       salary_max      = $3,
       salary_currency = $4,
       salary_period   = $5,
       salary_eur      = $6,
       updated_at      = now()
 WHERE id = $1
"""


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--stats", action="store_true", help="Print before counts only")
    grp.add_argument("--dry-run", action="store_true", help="Compute changes; do not write")
    grp.add_argument("--live", action="store_true", help="Apply UPDATEs")
    parser.add_argument(
        "--samples", type=int, default=5, help="Sample examples per country (dry-run/live)"
    )
    parser.add_argument(
        "--limit-per-country", type=int, default=None, help="Cap rows per country (debug)"
    )
    parser.add_argument(
        "--countries-set",
        choices=("scope-a", "scope-b", "all"),
        default="scope-a",
        help=(
            "Country set to reprocess: scope-a (PL,CZ,SE,DK,HU,RO,BG — non-Eurozone "
            "currencies from PR #3269; default), scope-b (DE,AT,CH,FR,IT,ES,NL,BE,UK,"
            "IE,PT,GR — EUR/GBP/CHF locale-aware extraction from PR #3342), or all."
        ),
    )
    args = parser.parse_args(argv)

    country_ids = _resolve_country_ids(args.countries_set)
    print(
        f"[reprocess-salary-eu] countries_set={args.countries_set!r}"
        f" -> {len(country_ids)} countries: {sorted(country_ids)}"
    )

    _load_env_local()
    dsn = os.environ.get("LOCAL_DATABASE_URL")
    if not dsn:
        print("ERROR: LOCAL_DATABASE_URL not set", file=sys.stderr)
        return 2

    print(f"[reprocess-salary-eu] connecting to {dsn.rsplit('@', 1)[-1]}")
    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=4, command_timeout=300, statement_cache_size=0
    )
    try:
        async with pool.acquire() as conn:
            # Pre-compute per-country descendant id sets.
            descendants_by_label: dict[str, list[int]] = {}
            for label, cid in country_ids.items():
                descendants_by_label[label] = await _descendant_ids(conn, cid)
                print(f"  {label}: {len(descendants_by_label[label])} location ids in subtree")

            rates = await _currency_rates(conn)
            print(f"  currency rates loaded: {sorted(rates.keys())}")

            before = await _before_counts(conn, descendants_by_label)
        print("\n== BEFORE ==")
        for label, stats in before.items():
            print(
                f"  {label}: total={stats['total']:>5}  with_salary_eur>0={stats['with_salary']:>5}"
            )

        if args.stats:
            return 0

        # Process per country, accumulating proposed changes.
        bucket_counts: dict[str, Counter] = {label: Counter() for label in country_ids}
        per_country_samples: dict[str, list] = {label: [] for label in country_ids}
        total_seen = 0
        total_changes = 0
        total_writes = 0
        start = time.monotonic()

        for label, ids in descendants_by_label.items():
            read_conn = await pool.acquire()
            write_conn = await pool.acquire() if args.live else None
            try:
                async for row in _iter_country_rows(read_conn, ids, args.limit_per_country):
                    total_seen += 1
                    new = _extract_salary_fields(row["html"], rates)
                    changes = _diff_payload(row, new)
                    if changes is None:
                        continue
                    total_changes += 1
                    bucket = _classify(row, new)
                    bucket_counts[label][bucket] += 1
                    if len(per_country_samples[label]) < args.samples:
                        per_country_samples[label].append(
                            {
                                "id": str(row["id"]),
                                "before": {
                                    "min": row["salary_min"],
                                    "max": row["salary_max"],
                                    "currency": row["salary_currency"],
                                    "period": row["salary_period"],
                                    "eur": row["salary_eur"],
                                },
                                "after": {
                                    "min": new[0],
                                    "max": new[1],
                                    "currency": new[2],
                                    "period": new[3],
                                    "eur": new[4],
                                },
                                "bucket": bucket,
                            }
                        )

                    if args.live and write_conn is not None:
                        await write_conn.execute(
                            _UPDATE_SQL, row["id"], new[0], new[1], new[2], new[3], new[4]
                        )
                        total_writes += 1

                    if total_seen % 2000 == 0:
                        elapsed = time.monotonic() - start
                        rate = total_seen / elapsed if elapsed > 0 else 0
                        print(
                            f"  [progress] {label} seen={total_seen} changes={total_changes}"
                            f" writes={total_writes} elapsed={elapsed:.1f}s rate={rate:.0f}/s"
                        )
            finally:
                await pool.release(read_conn)
                if write_conn is not None:
                    await pool.release(write_conn)

        elapsed = time.monotonic() - start
        print(
            f"\n== SUMMARY ({'LIVE' if args.live else 'DRY-RUN'}) =="
            f"\n  rows scanned : {total_seen}"
            f"\n  rows changed : {total_changes}"
            f"\n  rows written : {total_writes}"
            f"\n  elapsed_s    : {elapsed:.1f}"
        )
        print("\n== CHANGE BUCKETS PER COUNTRY ==")
        for label, c in bucket_counts.items():
            if c:
                print(f"  {label}: {dict(c)}")
            else:
                print(f"  {label}: (no changes)")

        print("\n== SAMPLES ==")
        for label, samples in per_country_samples.items():
            if not samples:
                continue
            print(f"--- {label} ---")
            for s in samples:
                print("  ", json.dumps(s, default=str))

        # Recompute after counts (cheap — same descendants)
        async with pool.acquire() as conn:
            after = await _before_counts(conn, descendants_by_label)
        print("\n== AFTER ==")
        for label in country_ids:
            b = before[label]
            a = after[label]
            delta = a["with_salary"] - b["with_salary"]
            print(
                f"  {label}: total={a['total']:>5}  with_salary={a['with_salary']:>5}"
                f"  delta={delta:+}"
            )
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
