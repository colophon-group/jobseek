"""Backfill company enrichment from Wikidata and company homepage JSON-LD.

Iterates all companies with a website, fetches metadata, and updates
the DB directly (industry, employee_count_range, founded_year, extras, description).

Usage:
  uv run python scripts/backfill_company_enrichment.py
  uv run python scripts/backfill_company_enrichment.py --dry-run
  uv run python scripts/backfill_company_enrichment.py --slug stripe
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

import asyncpg
import structlog

from src.config import settings
from src.core.enrich.company import CompanyMeta, enrich_company, get_industry_name, range_to_label
from src.shared.http import create_http_client

log = structlog.get_logger()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INDUSTRIES_CSV = DATA_DIR / "industries.csv"


async def _ensure_industry_table(conn: asyncpg.Connection) -> None:
    """Sync industries.csv to the industry table."""
    if not INDUSTRIES_CSV.exists():
        log.warning("backfill.no_industries_csv")
        return

    rows: list[tuple[int, str]] = []
    with open(INDUSTRIES_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("en") or row.get("name", "")
            rows.append((int(row["id"]), name))

    if not rows:
        return

    await conn.executemany(
        """
        INSERT INTO industry (id, name) VALUES ($1, $2)
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
        """,
        rows,
    )
    log.info("backfill.synced_industries", count=len(rows))


def _print_meta(slug: str, meta: CompanyMeta) -> None:
    """Pretty-print enrichment results."""
    tier_desc = {"A": "full", "B": "partial", "C": "nothing found"}
    print(f"\n  {slug} — tier {meta.tier} ({tier_desc.get(meta.tier, '?')})")

    if meta.description:
        desc_preview = meta.description[:80] + ("..." if len(meta.description) > 80 else "")
        print(f"    description:  {desc_preview} ({meta.sources.get('description', '?')})")

    if meta.industry_id:
        name = get_industry_name(meta.industry_id)
        print(
            f"    industry:     {meta.industry_id} — {name} ({meta.sources.get('industry', '?')})"
        )
    elif meta.industry_raw:
        print(f"    industry:     raw={meta.industry_raw!r} — NO MATCH")

    if meta.employee_count_range:
        label = range_to_label(meta.employee_count_range)
        src = meta.sources.get("employee_count_range", "?")
        print(f"    employees:    {label} ({src})")

    if meta.founded_year:
        print(f"    founded:      {meta.founded_year} ({meta.sources.get('founded_year', '?')})")

    if meta.same_as:
        print(f"    sameAs:       {', '.join(meta.same_as)}")

    if meta.parent_org_name:
        print(f"    parent:       {meta.parent_org_name}")

    if meta.wikidata_id:
        print(f"    wikidata:     {meta.wikidata_id}")

    if not any(
        [
            meta.description,
            meta.industry_id,
            meta.employee_count_range,
            meta.founded_year,
            meta.wikidata_id,
        ]
    ):
        print("    (no data found)")


async def _update_company(
    conn: asyncpg.Connection,
    company_id: str,
    meta: CompanyMeta,
) -> None:
    """Write enrichment fields to the company row and company_description table."""
    extras_json = json.dumps(meta.extras) if meta.extras else "{}"

    await conn.execute(
        """
        UPDATE company SET
            industry = COALESCE($2, industry),
            employee_count_range = COALESCE($3, employee_count_range),
            founded_year = COALESCE($4, founded_year),
            extras = CASE
                WHEN $5::jsonb != '{}'::jsonb THEN $5::jsonb
                ELSE COALESCE(extras, '{}'::jsonb)
            END,
            updated_at = now()
        WHERE id = $1
        """,
        company_id,
        meta.industry_id,
        meta.employee_count_range,
        meta.founded_year,
        extras_json,
    )

    # Write description to the separate company_description table
    if meta.description:
        await conn.execute(
            """
            INSERT INTO company_description (company_id, locale, description)
            VALUES ($1, 'en', $2)
            ON CONFLICT (company_id, locale) DO UPDATE SET description = EXCLUDED.description
            """,
            company_id,
            meta.description,
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill company enrichment")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--slug", help="Enrich a single company by slug")
    parser.add_argument(
        "--no-wikidata", action="store_true", help="Skip Wikidata queries (JSON-LD + meta only)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Delay between companies (seconds, default: 2.0 with wikidata, 0.5 without)",
    )
    args = parser.parse_args()

    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    try:
        # Sync industries table first
        if not args.dry_run:
            await _ensure_industry_table(conn)

        # Fetch companies
        if args.slug:
            rows = await conn.fetch(
                "SELECT id, slug, name, website FROM company WHERE slug = $1 AND website IS NOT NULL",
                args.slug,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, slug, name, website FROM company WHERE website IS NOT NULL ORDER BY slug"
            )

        if not rows:
            print("  no companies found")
            return

        print(f"  enriching {len(rows)} companies...")

        stats = {"A": 0, "B": 0, "C": 0, "errors": 0, "updated": 0}

        async with create_http_client() as http:
            for i, row in enumerate(rows):
                slug = row["slug"]
                name = row["name"]
                website = row["website"]

                try:
                    meta = await enrich_company(website, name, http, skip_wikidata=args.no_wikidata)
                    stats[meta.tier] += 1
                    _print_meta(slug, meta)

                    if not args.dry_run and meta.tier != "C":
                        await _update_company(conn, str(row["id"]), meta)
                        stats["updated"] += 1

                except Exception as e:
                    log.warning("backfill.company_error", slug=slug, error=str(e))
                    stats["errors"] += 1

                # Rate limit (be polite to Wikidata; lighter delay for website-only)
                delay = (
                    args.delay if args.delay is not None else (2.0 if not args.no_wikidata else 0.5)
                )
                if i < len(rows) - 1 and delay > 0:
                    await asyncio.sleep(delay)

        print(
            f"\n  done: A={stats['A']} B={stats['B']} C={stats['C']} "
            f"errors={stats['errors']} updated={stats['updated']}"
        )

        if args.dry_run:
            print("  DRY RUN — no changes written")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
