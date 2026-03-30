"""Backfill region (ADM1) populations from GeoNames allCountries dump.

Downloads allCountries.zip (~350MB), streams through it extracting only
ADM1 features, then updates the location table with real populations.

Usage:
  uv run python scripts/backfill_region_pop.py
  uv run python scripts/backfill_region_pop.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import io
import zipfile
from pathlib import Path

import asyncpg
import httpx

from src.config import settings

CACHE_DIR = Path("/tmp/geonames_cache")
ALL_COUNTRIES_URL = "https://download.geonames.org/export/dump/allCountries.zip"


async def download_all_countries() -> Path:
    """Download allCountries.zip to cache (skip if exists)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / "allCountries.zip"
    if dest.exists():
        size_mb = dest.stat().st_size / 1024 / 1024
        print(f"  skip allCountries.zip (cached, {size_mb:.0f} MB)")
        return dest

    print("  downloading allCountries.zip (~350 MB, this may take a few minutes)...")
    async with httpx.AsyncClient(follow_redirects=True, timeout=600) as client:
        async with client.stream("GET", ALL_COUNTRIES_URL) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=1024 * 256):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = 100 * downloaded / total
                        print(
                            f"\r  {downloaded / 1024 / 1024:.0f} / {total / 1024 / 1024:.0f} MB ({pct:.0f}%)",
                            end="",
                            flush=True,
                        )
            print()  # newline after progress
    size_mb = dest.stat().st_size / 1024 / 1024
    print(f"  saved allCountries.zip ({size_mb:.0f} MB)")
    return dest


def extract_adm1_populations(zip_path: Path) -> dict[int, int]:
    """Stream allCountries.zip, return {geoname_id: population} for ADM1 features."""
    print("  extracting ADM1 populations from allCountries.zip...")
    adm1_pops: dict[int, int] = {}

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open("allCountries.txt") as f:
            line_count = 0
            for raw_line in f:
                line_count += 1
                if line_count % 5_000_000 == 0:
                    print(
                        f"    processed {line_count / 1_000_000:.0f}M lines, found {len(adm1_pops)} ADM1 features..."
                    )
                line = raw_line.decode("utf-8", errors="replace")
                parts = line.split("\t")
                if len(parts) < 15:
                    continue
                feature_code = parts[7]
                if feature_code != "ADM1":
                    continue
                geoname_id = int(parts[0])
                population = int(parts[14]) if parts[14] else 0
                adm1_pops[geoname_id] = population

    print(f"  found {len(adm1_pops)} ADM1 features total")
    return adm1_pops


async def update_db(adm1_pops: dict[int, int], dry_run: bool = False) -> None:
    """Update location table with ADM1 populations."""
    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    try:
        # Get current regions from DB (names from location_name for display)
        rows = await conn.fetch(
            """SELECT l.id, COALESCE(l.population, 0) as pop,
                      (SELECT ln.name FROM location_name ln WHERE ln.location_id = l.id AND ln.locale = 'en' LIMIT 1) as name
               FROM location l WHERE l.type = 'region'"""
        )
        print(f"\n  DB has {len(rows)} regions")

        matched = 0
        updated = 0
        missing = 0
        already_ok = 0

        updates: list[tuple[int, int]] = []

        for row in rows:
            loc_id = row["id"]
            current_pop = row["pop"]
            new_pop = adm1_pops.get(loc_id)

            if new_pop is None:
                missing += 1
                continue

            matched += 1
            if new_pop == current_pop:
                already_ok += 1
                continue

            updates.append((loc_id, new_pop))
            updated += 1

        print(
            f"  matched: {matched}, will update: {updated}, already correct: {already_ok}, not in dump: {missing}"
        )

        # Show some examples
        if updates:
            print("\n  Sample updates:")
            for loc_id, pop in updates[:10]:
                name = next((r["name"] for r in rows if r["id"] == loc_id), str(loc_id))
                print(f"    {name:30s}  0 → {pop:>12,}")

        if dry_run:
            print("\n  DRY RUN — no changes written")
            return

        if not updates:
            print("\n  nothing to update")
            return

        # Batch update
        await conn.executemany(
            "UPDATE location SET population = $2 WHERE id = $1",
            updates,
        )
        print(f"\n  updated {len(updates)} regions")

    finally:
        await conn.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill region populations from GeoNames")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing"
    )
    args = parser.parse_args()

    zip_path = await download_all_countries()
    adm1_pops = extract_adm1_populations(zip_path)
    await update_db(adm1_pops, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
