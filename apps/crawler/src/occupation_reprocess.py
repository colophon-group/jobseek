#!/usr/bin/env python
"""One-shot reprocess of occupation assignments after taxonomy splits.

Issue #3360 follows the same operator pattern as the salary reprocess
commands: scan historical rows whose stored ``occupation_id`` may be stale
after a taxonomy/matcher change, report what would change, and require an
explicit ``--live`` flag before writing anything.

The default scope is intentionally conservative:

* active postings only;
* rows currently assigned to parent slugs that were split by #3358;
* rows with ``occupation_id IS NULL`` so newly-added aliases can fill gaps.

Writes update only ``occupation_id`` and ``updated_at``. The updated timestamp
is what lets the CDC exporter reflow touched rows to Supabase and Typesense.

Usage:
    uv run crawler reprocess-occupations --stats
    uv run crawler reprocess-occupations --dry-run --samples 10
    uv run crawler reprocess-occupations --live

    # Include closed/historical postings as well.
    uv run crawler reprocess-occupations --include-inactive --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

# Make ``src`` importable when running directly from apps/crawler/.
HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from src.core.occupation_resolve import match_occupation  # noqa: E402

FETCH_BATCH = 1000

# Parent/current slugs called out in #3360. Rows already stored under these
# broader slugs are the likely stale set after #3358 split out more precise
# children or pruned over-broad aliases.
SPLIT_PARENT_SLUGS: tuple[str, ...] = (
    "devops-engineer",
    "data-engineer",
    "ml-engineer",
    "security-engineer",
    "embedded-engineer",
    "qa-engineer",
    "solutions-architect",
    "engineering-manager",
    "data-analyst",
    "research-engineer",
    "data-annotator",
)


@dataclass(frozen=True)
class OccupationChange:
    posting_id: Any
    title: str
    old_id: int | None
    old_slug: str | None
    new_id: int | None
    new_slug: str | None
    is_active: bool

    @property
    def pair(self) -> tuple[str, str]:
        return (self.old_slug or "NULL", self.new_slug or "NULL")


def _load_env_local() -> None:
    """Best-effort loader for apps/crawler/.env.local KEY=VALUE lines."""
    env_path = APP_DIR / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _candidate_rows_sql(
    limit: int | None,
    include_inactive: bool,
    include_nulls: bool,
) -> str:
    """Return the candidate-row query for occupation reprocessing."""
    active_clause = "" if include_inactive else "AND jp.is_active"
    null_clause = "OR jp.occupation_id IS NULL" if include_nulls else ""
    sql = f"""
        SELECT jp.id,
               jp.titles[1] AS title,
               jp.occupation_id AS old_id,
               o.slug AS old_slug,
               jp.is_active
          FROM job_posting jp
          LEFT JOIN occupation o ON o.id = jp.occupation_id
         WHERE jp.titles[1] IS NOT NULL
           {active_clause}
           AND (
                o.slug = ANY($1::text[])
                {null_clause}
           )
         ORDER BY jp.first_seen_at DESC NULLS LAST, jp.id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return sql


async def _occupation_ids(conn: Any) -> dict[str, int]:
    rows = await conn.fetch("SELECT id, slug FROM occupation")
    return {r["slug"]: int(r["id"]) for r in rows}


async def _before_counts(
    conn: Any,
    parent_slugs: tuple[str, ...],
    include_inactive: bool,
    include_nulls: bool,
) -> dict[str, int]:
    active_clause = "" if include_inactive else "AND jp.is_active"
    null_clause = "OR jp.occupation_id IS NULL" if include_nulls else ""
    rows = await conn.fetch(
        f"""
        SELECT COALESCE(o.slug, 'NULL') AS slug, COUNT(*)::int AS count
          FROM job_posting jp
          LEFT JOIN occupation o ON o.id = jp.occupation_id
         WHERE jp.titles[1] IS NOT NULL
           {active_clause}
           AND (
                o.slug = ANY($1::text[])
                {null_clause}
           )
         GROUP BY 1
         ORDER BY 2 DESC, 1
        """,
        list(parent_slugs),
    )
    return {str(r["slug"]): int(r["count"]) for r in rows}


async def _iter_candidate_rows(
    conn: Any,
    parent_slugs: tuple[str, ...],
    limit: int | None,
    include_inactive: bool,
    include_nulls: bool,
):
    sql = _candidate_rows_sql(
        limit=limit,
        include_inactive=include_inactive,
        include_nulls=include_nulls,
    )
    async with conn.transaction(readonly=True):
        async for row in conn.cursor(sql, list(parent_slugs), prefetch=FETCH_BATCH):
            yield row


def _diff_row(row: Any, slug_to_id: dict[str, int]) -> OccupationChange | None:
    new_slug = match_occupation(row["title"])
    new_id = slug_to_id.get(new_slug) if new_slug is not None else None
    old_id = row["old_id"]
    old_slug = row["old_slug"]

    if new_slug is not None and new_id is None:
        raise RuntimeError(f"matcher returned unknown occupation slug: {new_slug}")

    if old_id == new_id:
        return None

    return OccupationChange(
        posting_id=row["id"],
        title=row["title"],
        old_id=old_id,
        old_slug=old_slug,
        new_id=new_id,
        new_slug=new_slug,
        is_active=bool(row["is_active"]),
    )


_UPDATE_SQL = """
UPDATE job_posting
   SET occupation_id = $2,
       updated_at = now()
 WHERE id = $1
   AND occupation_id IS NOT DISTINCT FROM $3
"""


def add_occupation_reprocess_arguments(parser: argparse.ArgumentParser) -> None:
    """Register occupation reprocess options on ``parser``."""
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--stats", action="store_true", help="Print candidate counts only")
    grp.add_argument("--dry-run", action="store_true", help="Compute changes; do not write")
    grp.add_argument("--live", action="store_true", help="Apply UPDATEs")
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Scan inactive/closed postings too. Default is active-only for safety.",
    )
    parser.add_argument(
        "--skip-nulls",
        action="store_true",
        help="Do not scan occupation_id IS NULL rows; only re-resolve split parent slugs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap candidate rows scanned (debug/staged production runs).",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="Sample changed posting ids/titles per old->new bucket.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=5000,
        help="Print scan progress every N candidate rows (default 5000).",
    )


async def run_from_args(args: argparse.Namespace) -> int:
    """Run the occupation reprocess workflow from parsed CLI args."""
    if args.progress_every <= 0:
        print("ERROR: --progress-every must be positive", file=sys.stderr, flush=True)
        return 2
    if args.samples < 0:
        print("ERROR: --samples must be non-negative", file=sys.stderr, flush=True)
        return 2

    include_nulls = not args.skip_nulls
    print(
        "[reprocess-occupations]"
        f" include_inactive={args.include_inactive}"
        f" include_nulls={include_nulls}"
        f" parents={list(SPLIT_PARENT_SLUGS)}",
        flush=True,
    )

    _load_env_local()
    dsn = os.environ.get("LOCAL_DATABASE_URL")
    if not dsn:
        print("ERROR: LOCAL_DATABASE_URL not set", file=sys.stderr, flush=True)
        return 2

    print(f"[reprocess-occupations] connecting to {dsn.rsplit('@', 1)[-1]}", flush=True)
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=4,
        command_timeout=300,
        statement_cache_size=0,
    )
    try:
        async with pool.acquire() as conn:
            slug_to_id = await _occupation_ids(conn)
            before = await _before_counts(
                conn,
                SPLIT_PARENT_SLUGS,
                include_inactive=args.include_inactive,
                include_nulls=include_nulls,
            )

        print("\n== BEFORE CANDIDATES ==", flush=True)
        for slug, count in before.items():
            print(f"  {slug}: {count}", flush=True)

        if args.stats:
            return 0

        seen = 0
        changed = 0
        written = 0
        skipped_concurrent = 0
        active_changes = 0
        inactive_changes = 0
        by_pair: Counter[tuple[str, str]] = Counter()
        samples: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
        start = time.monotonic()

        read_conn = await pool.acquire()
        write_conn = await pool.acquire() if args.live else None
        try:
            async for row in _iter_candidate_rows(
                read_conn,
                SPLIT_PARENT_SLUGS,
                args.limit,
                include_inactive=args.include_inactive,
                include_nulls=include_nulls,
            ):
                seen += 1
                diff = _diff_row(row, slug_to_id)
                if diff is None:
                    continue
                changed += 1
                if diff.is_active:
                    active_changes += 1
                else:
                    inactive_changes += 1
                by_pair[diff.pair] += 1
                if len(samples[diff.pair]) < args.samples:
                    samples[diff.pair].append(
                        {
                            "id": str(diff.posting_id),
                            "is_active": diff.is_active,
                            "title": diff.title,
                        }
                    )

                if args.live and write_conn is not None:
                    result = await write_conn.execute(
                        _UPDATE_SQL,
                        diff.posting_id,
                        diff.new_id,
                        diff.old_id,
                    )
                    if result == "UPDATE 1":
                        written += 1
                    else:
                        skipped_concurrent += 1

                if seen % args.progress_every == 0:
                    elapsed = time.monotonic() - start
                    rate = seen / elapsed if elapsed > 0 else 0
                    print(
                        f"  [progress] seen={seen} changed={changed}"
                        f" written={written} elapsed={elapsed:.1f}s rate={rate:.0f}/s",
                        flush=True,
                    )
        finally:
            await pool.release(read_conn)
            if write_conn is not None:
                await pool.release(write_conn)

        elapsed = time.monotonic() - start
        print(
            f"\n== SUMMARY ({'LIVE' if args.live else 'DRY-RUN'}) =="
            f"\n  rows scanned       : {seen}"
            f"\n  rows changed       : {changed}"
            f"\n    active           : {active_changes}"
            f"\n    inactive         : {inactive_changes}"
            f"\n  rows written       : {written}"
            f"\n  skipped concurrent : {skipped_concurrent}"
            f"\n  elapsed_s          : {elapsed:.1f}",
            flush=True,
        )

        print("\n== CHANGE BUCKETS ==", flush=True)
        for (old_slug, new_slug), count in by_pair.most_common():
            print(f"  {old_slug} -> {new_slug}: {count}", flush=True)
            for sample in samples[(old_slug, new_slug)]:
                print("    ", json.dumps(sample, default=str), flush=True)

        async with pool.acquire() as conn:
            after = await _before_counts(
                conn,
                SPLIT_PARENT_SLUGS,
                include_inactive=args.include_inactive,
                include_nulls=include_nulls,
            )
        print("\n== AFTER CANDIDATES ==", flush=True)
        for slug, after_count in after.items():
            delta = after_count - before.get(slug, 0)
            print(f"  {slug}: {after_count}  delta={delta:+}", flush=True)

        return 0
    finally:
        await pool.close()


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_occupation_reprocess_arguments(parser)
    return await run_from_args(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
