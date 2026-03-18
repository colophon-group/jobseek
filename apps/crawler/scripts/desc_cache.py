"""Local SQLite cache for R2 job descriptions.

Downloads posting metadata from Postgres and description HTML from the R2 CDN
into a local SQLite database for offline analysis. Idempotent — re-running
skips already-cached entries.

Requires env vars:
  DATABASE_URL  — Postgres connection string
  R2_DOMAIN_URL — Public CDN base URL (e.g. https://cdn.example.com)

Usage:
  uv run python scripts/desc_cache.py                           # Index + download all
  uv run python scripts/desc_cache.py --company roche            # Single company
  uv run python scripts/desc_cache.py --company roche --company novartis
  uv run python scripts/desc_cache.py --limit 500                # Cap downloads
  uv run python scripts/desc_cache.py --download-only            # Skip Postgres, download missing
  uv run python scripts/desc_cache.py --stats                    # Cache statistics
  uv run python scripts/desc_cache.py --active-only              # Only active postings
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import asyncpg
import httpx

# Load .env before importing settings
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "desc_cache.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posting (
    id TEXT PRIMARY KEY,
    company_slug TEXT NOT NULL,
    locales TEXT NOT NULL DEFAULT '[]',
    titles TEXT NOT NULL DEFAULT '[]',
    source_url TEXT NOT NULL,
    board_slug TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS description (
    posting_id TEXT NOT NULL,
    locale TEXT NOT NULL,
    html TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (posting_id, locale),
    FOREIGN KEY (posting_id) REFERENCES posting(id)
);

CREATE INDEX IF NOT EXISTS idx_posting_company ON posting(company_slug);
CREATE INDEX IF NOT EXISTS idx_posting_active ON posting(is_active);
CREATE INDEX IF NOT EXISTS idx_desc_posting ON description(posting_id);
"""


def _init_sqlite() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(_SCHEMA)
    return conn


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build local SQLite cache of R2 descriptions")
    p.add_argument("--company", action="append", default=[], help="Filter by company slug(s)")
    p.add_argument("--limit", type=int, default=0, help="Max postings to download (0 = all)")
    p.add_argument(
        "--download-only", action="store_true", help="Skip Postgres index, download missing"
    )
    p.add_argument("--active-only", action="store_true", help="Only include active postings")
    p.add_argument("--concurrency", type=int, default=20, help="Max concurrent R2 downloads")
    p.add_argument("--stats", action="store_true", help="Show cache statistics and exit")
    return p.parse_args()


def _show_stats(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM posting WHERE is_active = 1").fetchone()[0]
    descs = conn.execute("SELECT COUNT(*) FROM description").fetchone()[0]
    missing = conn.execute(
        """SELECT COUNT(*) FROM posting p
           WHERE NOT EXISTS (
               SELECT 1 FROM description d WHERE d.posting_id = p.id
           )"""
    ).fetchone()[0]

    print(f"SQLite cache: {DB_PATH}")
    print(f"  Postings indexed: {total:,} ({active:,} active)")
    print(f"  Descriptions cached: {descs:,}")
    print(f"  Missing descriptions: {missing:,}")

    print("\nTop 20 companies by posting count:")
    rows = conn.execute(
        """SELECT company_slug, COUNT(*) as n,
                  SUM(CASE WHEN EXISTS (
                      SELECT 1 FROM description d WHERE d.posting_id = p.id
                  ) THEN 1 ELSE 0 END) as cached
           FROM posting p GROUP BY company_slug ORDER BY n DESC LIMIT 20"""
    ).fetchall()
    for slug, n, cached in rows:
        print(f"  {slug:<30s} {n:>6,} postings  ({cached:>6,} cached)")

    print("\nLocale distribution (from indexed metadata):")
    locale_counts: dict[str, int] = {}
    for (locales_json,) in conn.execute("SELECT locales FROM posting"):
        for loc in json.loads(locales_json):
            locale_counts[loc] = locale_counts.get(loc, 0) + 1
    for loc, count in sorted(locale_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {loc:<6s} {count:>8,}")

    multi = conn.execute(
        "SELECT COUNT(*) FROM posting WHERE json_array_length(locales) > 1"
    ).fetchone()[0]
    print(f"\nPostings with multiple locales: {multi:,}")


async def _index_from_postgres(
    conn: sqlite3.Connection,
    companies: list[str],
    active_only: bool,
) -> int:
    """Fetch posting metadata from Postgres, insert into SQLite."""
    from src.config import settings

    pg = await asyncpg.connect(settings.database_url, timeout=30)

    where_clauses = []
    params: list = []
    idx = 1

    if active_only:
        where_clauses.append("jp.is_active = true")

    if companies:
        placeholders = ", ".join(f"${idx + i}" for i in range(len(companies)))
        where_clauses.append(f"c.slug IN ({placeholders})")
        params.extend(companies)
        idx += len(companies)

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    query = f"""
        SELECT jp.id::text, c.slug, jp.locales, jp.titles, jp.source_url,
               jb.board_slug, jp.is_active
        FROM job_posting jp
        JOIN company c ON c.id = jp.company_id
        LEFT JOIN job_board jb ON jb.id = jp.board_id
        {where}
    """

    rows = await pg.fetch(query, *params)
    await pg.close()

    inserted = 0
    for row in rows:
        locales_json = json.dumps(list(row["locales"]) if row["locales"] else [])
        titles_json = json.dumps(list(row["titles"]) if row["titles"] else [])
        try:
            conn.execute(
                """INSERT OR REPLACE INTO posting
                   (id, company_slug, locales, titles, source_url, board_slug, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["slug"],
                    locales_json,
                    titles_json,
                    row["source_url"],
                    row["board_slug"],
                    1 if row["is_active"] else 0,
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            print(f"  SQLite error for {row['id']}: {e}", file=sys.stderr)
    conn.commit()
    return inserted


async def _download_descriptions(
    conn: sqlite3.Connection,
    companies: list[str],
    limit: int,
    concurrency: int,
) -> tuple[int, int]:
    """Download missing descriptions from R2 CDN."""
    r2_domain = os.environ.get("R2_DOMAIN_URL", "").rstrip("/")
    if not r2_domain:
        print("ERROR: R2_DOMAIN_URL not set", file=sys.stderr)
        sys.exit(1)

    # Find postings with missing descriptions
    company_filter = ""
    if companies:
        placeholders = ", ".join(f"'{c}'" for c in companies)
        company_filter = f"AND p.company_slug IN ({placeholders})"

    rows = conn.execute(
        f"""SELECT p.id, p.locales FROM posting p
            WHERE NOT EXISTS (
                SELECT 1 FROM description d WHERE d.posting_id = p.id
            )
            {company_filter}
            ORDER BY p.company_slug"""
    ).fetchall()

    if limit > 0:
        rows = rows[:limit]

    if not rows:
        print("All descriptions already cached.")
        return 0, 0

    print(f"Downloading {len(rows):,} descriptions...")

    sem = asyncio.Semaphore(concurrency)
    downloaded = 0
    failed = 0
    t0 = time.monotonic()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0),
        limits=httpx.Limits(max_connections=concurrency + 5, max_keepalive_connections=concurrency),
        follow_redirects=True,
    ) as client:

        async def fetch_one(
            posting_id: str, locales_json: str
        ) -> tuple[str, str | None, str | None]:
            locales = json.loads(locales_json)
            locale = locales[0] if locales else "en"
            url = f"{r2_domain}/job/{posting_id}/{locale}/latest.html"
            async with sem:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return posting_id, locale, resp.text
                    return posting_id, locale, None
                except Exception:
                    return posting_id, locale, None

        tasks = [fetch_one(pid, loc_json) for pid, loc_json in rows]

        batch_size = 200
        for batch_start in range(0, len(tasks), batch_size):
            batch = tasks[batch_start : batch_start + batch_size]
            results = await asyncio.gather(*batch)

            for posting_id, locale, html in results:
                if html is not None:
                    sql = "INSERT OR REPLACE INTO description"
                    sql += " (posting_id, locale, html) VALUES (?, ?, ?)"
                    conn.execute(sql, (posting_id, locale, html))
                    downloaded += 1
                else:
                    failed += 1

            conn.commit()
            elapsed = time.monotonic() - t0
            total_done = batch_start + len(batch)
            rate = total_done / elapsed if elapsed > 0 else 0
            print(
                f"  {total_done:,}/{len(rows):,}  "
                f"({downloaded:,} ok, {failed:,} missing)  {rate:.0f}/s"
            )

    return downloaded, failed


async def _main() -> None:
    args = _parse_args()
    conn = _init_sqlite()

    if args.stats:
        _show_stats(conn)
        conn.close()
        return

    if not args.download_only:
        print("Indexing postings from Postgres...")
        n = await _index_from_postgres(conn, args.company, args.active_only)
        print(f"  Indexed {n:,} postings")

    downloaded, failed = await _download_descriptions(
        conn, args.company, args.limit, args.concurrency
    )
    print(f"\nDone. Downloaded: {downloaded:,}, Missing/failed: {failed:,}")
    _show_stats(conn)
    conn.close()


if __name__ == "__main__":
    asyncio.run(_main())
