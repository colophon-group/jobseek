"""Backfill location.languages from GeoNames countryInfo.txt.

Sets spoken-language ISO 639-1 codes on countries, then propagates
to regions and cities via the parent_id chain.

Usage:
  uv run python scripts/backfill_location_languages.py
  uv run python scripts/backfill_location_languages.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import asyncpg
import httpx

from src.config import settings

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "geonames"
COUNTRY_INFO_URL = "https://download.geonames.org/export/dump/countryInfo.txt"


def parse_country_languages(path: Path) -> dict[int, list[str]]:
    """Parse countryInfo.txt → {geoname_id: [lang_codes]}."""
    result: dict[int, list[str]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 17:
                continue
            geoname_id = int(parts[16]) if parts[16] else None
            if not geoname_id:
                continue
            raw_langs = parts[15] if parts[15] else ""
            # "de-AT,hu,sl" → ["de", "hu", "sl"]
            langs = list(
                dict.fromkeys(code.split("-")[0] for code in raw_langs.split(",") if code.strip())
            )
            if langs:
                result[geoname_id] = langs
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill location languages")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Download countryInfo.txt if not cached
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "countryInfo.txt"
    if not path.exists():
        print("  downloading countryInfo.txt...")
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(COUNTRY_INFO_URL)
            resp.raise_for_status()
            path.write_bytes(resp.content)

    country_langs = parse_country_languages(path)
    print(f"  parsed {len(country_langs)} countries with language data")

    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    try:
        # Load all locations to build parent chain
        rows = await conn.fetch("SELECT id, parent_id, type::text AS type FROM location")

        entries: dict[int, dict] = {}
        for r in rows:
            entries[r["id"]] = {
                "parent_id": r["parent_id"],
                "type": r["type"],
            }

        # Walk parent chain to find country ancestor for each location
        def find_country_id(loc_id: int) -> int | None:
            current = loc_id
            depth = 0
            while current is not None and depth < 6:
                entry = entries.get(current)
                if not entry:
                    return None
                if entry["type"] == "country":
                    return current
                current = entry["parent_id"]
                depth += 1
            return None

        # Build updates: (id, languages)
        updates: list[tuple[int, list[str]]] = []
        for loc_id, entry in entries.items():
            if entry["type"] == "country":
                langs = country_langs.get(loc_id, [])
                if langs:
                    updates.append((loc_id, langs))
            elif entry["type"] in ("region", "city"):
                country_id = find_country_id(loc_id)
                if country_id:
                    langs = country_langs.get(country_id, [])
                    if langs:
                        updates.append((loc_id, langs))

        print(f"  will update {len(updates)} locations")

        # Show samples
        sample_countries = [
            (lid, langs) for lid, langs in updates if entries[lid]["type"] == "country"
        ][:5]
        for lid, langs in sample_countries:
            print(f"    country {lid}: {langs}")

        if args.dry_run:
            print("\n  DRY RUN — no changes written")
            return

        # Batch update
        await conn.executemany(
            "UPDATE location SET languages = $2 WHERE id = $1",
            updates,
        )
        print(f"\n  updated {len(updates)} locations")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
