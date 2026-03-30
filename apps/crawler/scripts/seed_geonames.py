"""Seed location + location_name tables from GeoNames data.

Downloads GeoNames files, parses them, and bulk-inserts into Postgres.
Idempotent: TRUNCATEs tables before each run.

Usage:
  uv run python scripts/seed_geonames.py
  uv run python scripts/seed_geonames.py --dry-run
  uv run python scripts/seed_geonames.py --locale en --skip-download
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import httpx

from src.config import settings
from src.shared.slug import slugify

# ── Constants ────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "geonames"

GEONAMES_BASE = "https://download.geonames.org/export/dump"
FILES = {
    "countryInfo.txt": f"{GEONAMES_BASE}/countryInfo.txt",
    "admin1CodesASCII.txt": f"{GEONAMES_BASE}/admin1CodesASCII.txt",
    "cities15000.zip": f"{GEONAMES_BASE}/cities15000.zip",
    "alternateNamesV2.zip": f"{GEONAMES_BASE}/alternateNamesV2.zip",
}

SUPPORTED_LOCALES = {"en", "de", "fr", "it"}

# Alternate-name codes to skip (utility, not language names)
_SKIP_ALT_CODES = {
    "wkdt",
    "link",
    "post",
    "unlc",
    "iata",
    "icao",
    "faac",
    "lauc",
    "phon",
    "piny",
    "abbr",
    "tcid",
}

# ── Macro Regions ────────────────────────────────────────────────────

MACRO_REGIONS: list[dict] = [
    {"id": 1, "names": {"en": "EMEA", "de": "EMEA", "fr": "EMEA", "it": "EMEA"}},
    {"id": 2, "names": {"en": "APAC", "de": "APAC", "fr": "APAC", "it": "APAC"}},
    {"id": 3, "names": {"en": "Americas", "de": "Amerika", "fr": "Amériques", "it": "Americhe"}},
    {"id": 4, "names": {"en": "EU", "de": "EU", "fr": "UE", "it": "UE"}},
    {"id": 5, "names": {"en": "DACH", "de": "DACH", "fr": "DACH", "it": "DACH"}},
    {"id": 6, "names": {"en": "LATAM", "de": "LATAM", "fr": "LATAM", "it": "LATAM"}},
    {
        "id": 7,
        "names": {
            "en": "Nordics",
            "de": "Nordische Länder",
            "fr": "Pays nordiques",
            "it": "Paesi nordici",
        },
    },
    {"id": 8, "names": {"en": "MENA", "de": "MENA", "fr": "MENA", "it": "MENA"}},
    {"id": 9, "names": {"en": "Worldwide", "de": "Weltweit", "fr": "Mondial", "it": "Mondiale"}},
]

# ISO 3166-1 alpha-2 codes for macro region membership
_EU_COUNTRIES = {
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
}

_DACH_COUNTRIES = {"DE", "AT", "CH"}

_NORDIC_COUNTRIES = {"DK", "FI", "IS", "NO", "SE"}

_LATAM_COUNTRIES = {
    "AR",
    "BO",
    "BR",
    "CL",
    "CO",
    "CR",
    "CU",
    "DO",
    "EC",
    "SV",
    "GT",
    "HN",
    "MX",
    "NI",
    "PA",
    "PY",
    "PE",
    "PR",
    "UY",
    "VE",
}

# EMEA = Europe + Middle East + Africa (continents EU, AF, AS-partial)
_EMEA_CONTINENTS = {"EU", "AF"}
_EMEA_AS_COUNTRIES = {
    "AE",
    "BH",
    "CY",
    "EG",
    "IL",
    "IQ",
    "IR",
    "JO",
    "KW",
    "LB",
    "OM",
    "PS",
    "QA",
    "SA",
    "SY",
    "TR",
    "YE",
}

# APAC
_APAC_CONTINENTS = {"AS", "OC"}
# Exclude Middle East countries already in EMEA
_APAC_EXCLUDE = _EMEA_AS_COUNTRIES

# Americas
_AMERICAS_CONTINENTS = {"NA", "SA"}

# MENA
_MENA_COUNTRIES = {
    "DZ",
    "BH",
    "EG",
    "IR",
    "IQ",
    "IL",
    "JO",
    "KW",
    "LB",
    "LY",
    "MA",
    "OM",
    "PS",
    "QA",
    "SA",
    "SY",
    "TN",
    "AE",
    "YE",
}


# ── Download ─────────────────────────────────────────────────────────


async def download_files(locales: set[str] | None = None) -> None:
    """Download GeoNames files to DATA_DIR (skip if already present)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    files_to_download = dict(FILES)

    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        for filename, url in files_to_download.items():
            dest = DATA_DIR / filename
            if dest.exists():
                print(f"  skip {filename} (cached)")
                continue
            print(f"  downloading {filename}...")
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            print(f"  saved {filename} ({len(resp.content) / 1024 / 1024:.1f} MB)")


# ── Parsers ──────────────────────────────────────────────────────────


def parse_countries(path: Path) -> list[dict]:
    """Parse countryInfo.txt → list of country dicts."""
    countries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 17:
                continue
            iso = parts[0]
            geoname_id = int(parts[16]) if parts[16] else None
            if not geoname_id:
                continue
            name = parts[4]
            continent = parts[8]
            population = int(parts[7]) if parts[7] else 0
            # Lat/lng from centroid — not always in countryInfo, may be empty
            # We'll try columns not available in standard countryInfo, use 0.0 fallback
            raw_langs = parts[15] if len(parts) > 15 and parts[15] else ""
            languages = list(
                dict.fromkeys(code.split("-")[0] for code in raw_langs.split(",") if code.strip())
            )
            countries.append(
                {
                    "id": geoname_id,
                    "iso": iso,
                    "name": name,
                    "continent": continent,
                    "population": population,
                    "languages": languages,
                }
            )
    return countries


def parse_admin1(path: Path) -> list[dict]:
    """Parse admin1CodesASCII.txt → list of region dicts."""
    regions = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 4:
                continue
            code = parts[0]  # e.g. "CH.ZH"
            name = parts[1]
            ascii_name = parts[2]
            geoname_id = int(parts[3]) if parts[3] else None
            if not geoname_id:
                continue
            country_code = code.split(".")[0]
            regions.append(
                {
                    "id": geoname_id,
                    "code": code,
                    "name": name,
                    "ascii_name": ascii_name,
                    "country_code": country_code,
                }
            )
    return regions


def parse_cities(path: Path) -> list[dict]:
    """Parse cities15000.txt (from zip) → list of city dicts."""
    cities = []
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            txt_name = "cities15000.txt"
            with zf.open(txt_name) as f:
                content = f.read().decode("utf-8")
    else:
        content = path.read_text(encoding="utf-8")

    for line in content.splitlines():
        parts = line.split("\t")
        if len(parts) < 15:
            continue
        geoname_id = int(parts[0])
        name = parts[1]
        ascii_name = parts[2]
        lat = float(parts[4]) if parts[4] else None
        lng = float(parts[5]) if parts[5] else None
        country_code = parts[8]
        admin1_code = parts[10]  # e.g. "ZH" for Zurich
        population = int(parts[14]) if parts[14] else 0
        cities.append(
            {
                "id": geoname_id,
                "name": name,
                "ascii_name": ascii_name,
                "lat": lat,
                "lng": lng,
                "country_code": country_code,
                "admin1_code": admin1_code,
                "population": population,
            }
        )
    return cities


@dataclass
class AltName:
    locale: str
    name: str
    is_preferred: bool = False
    is_short: bool = False
    is_historic: bool = False


def parse_alternate_names(
    path: Path,
    valid_ids: set[int],
    locales: set[str] | None = None,
) -> dict[int, list[AltName]]:
    """Parse alternateNamesV2.zip → {geoname_id: [AltName, ...]}.

    When *locales* is None, accepts all language codes (2-3 letter + empty),
    skipping utility codes (wkdt, link, post, etc.).
    Stores ALL alternate names per location (not just one per locale).
    Parses isPreferredName/isShortName/isHistoric flags for display name selection.
    """
    names: dict[int, list[AltName]] = {}

    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            txt_name = "alternateNamesV2.txt"
            with zf.open(txt_name) as f:
                for raw_line in f:
                    line = raw_line.decode("utf-8", errors="replace")
                    parts = line.strip().split("\t")
                    if len(parts) < 4:
                        continue
                    geoname_id = int(parts[1]) if parts[1] else None
                    if geoname_id is None or geoname_id not in valid_ids:
                        continue
                    lang = parts[2]
                    alt_name = parts[3].strip()
                    if not alt_name:
                        continue
                    # Filter by locale set or accept all language codes
                    if locales is not None:
                        if lang not in locales:
                            continue
                    else:
                        # Skip utility codes
                        if lang in _SKIP_ALT_CODES:
                            continue
                        # Skip codes longer than 3 chars (not language codes)
                        if len(lang) > 3:
                            continue
                    is_preferred = len(parts) > 4 and parts[4] == "1"
                    is_short = len(parts) > 5 and parts[5] == "1"
                    is_historic = len(parts) > 7 and parts[7] == "1"
                    names.setdefault(geoname_id, []).append(
                        AltName(
                            locale=lang or "alt",
                            name=alt_name,
                            is_preferred=is_preferred,
                            is_short=is_short,
                            is_historic=is_historic,
                        )
                    )
    return names


# ── Insert ───────────────────────────────────────────────────────────


async def seed(
    conn: asyncpg.Connection,
    locales: set[str],
    dry_run: bool = False,
    skip_download: bool = False,
) -> dict[str, int]:
    """Seed location tables. Returns row counts."""

    if not skip_download:
        print("Downloading GeoNames files...")
        await download_files(locales)

    print("Parsing GeoNames data...")
    countries = parse_countries(DATA_DIR / "countryInfo.txt")
    regions = parse_admin1(DATA_DIR / "admin1CodesASCII.txt")
    cities = parse_cities(DATA_DIR / "cities15000.zip")

    # Build lookup maps
    iso_to_geoname: dict[str, int] = {c["iso"]: c["id"] for c in countries}
    iso_to_continent: dict[str, str] = {c["iso"]: c["continent"] for c in countries}
    country_languages: dict[int, list[str]] = {c["id"]: c["languages"] for c in countries}
    admin1_key_to_id: dict[str, int] = {}
    for r in regions:
        admin1_key_to_id[r["code"]] = r["id"]

    # Assign parent_ids for regions (country) and cities (region or country)
    for r in regions:
        r["parent_id"] = iso_to_geoname.get(r["country_code"])

    for c in cities:
        # Try to find admin1 region
        admin1_key = f"{c['country_code']}.{c['admin1_code']}"
        c["parent_id"] = admin1_key_to_id.get(admin1_key) or iso_to_geoname.get(c["country_code"])

    # Parse alternate names
    all_ids = {c["id"] for c in countries} | {r["id"] for r in regions} | {c["id"] for c in cities}
    alt_names: dict[int, list[tuple[str, str]]] = {}
    alt_names_path = DATA_DIR / "alternateNamesV2.zip"
    if alt_names_path.exists():
        print("Parsing alternate names (this may take a moment)...")
        # Pass None to accept all language codes, or specific locales
        alt_locales = None if locales == SUPPORTED_LOCALES else locales
        alt_names = parse_alternate_names(alt_names_path, all_ids, alt_locales)

    # Compute region lat/lng from largest city per region
    region_best_city: dict[int, dict] = {}
    for c in cities:
        parent = c["parent_id"]
        if parent and parent in admin1_key_to_id.values():
            if (
                parent not in region_best_city
                or c["population"] > region_best_city[parent]["population"]
            ):
                region_best_city[parent] = c

    for r in regions:
        best = region_best_city.get(r["id"])
        if best:
            r["lat"] = best["lat"]
            r["lng"] = best["lng"]
        else:
            r["lat"] = None
            r["lng"] = None

    # Country lat/lng: derive from largest city
    country_best_city: dict[str, dict] = {}
    for c in cities:
        cc = c["country_code"]
        if cc not in country_best_city or c["population"] > country_best_city[cc]["population"]:
            country_best_city[cc] = c
    for co in countries:
        best = country_best_city.get(co["iso"])
        if best:
            co["lat"] = best["lat"]
            co["lng"] = best["lng"]
        else:
            co["lat"] = None
            co["lng"] = None

    # ── Build insert records ─────────────────────────────────────────

    # Build reverse lookup: geoname_id → country ISO code
    geoname_to_iso: dict[int, str] = {c["id"]: c["iso"] for c in countries}

    # Build reverse lookup: region geoname_id → admin1 code suffix (e.g. "ZH")
    region_id_to_admin1: dict[int, str] = {}
    region_id_to_country: dict[int, str] = {}
    for r in regions:
        code_parts = r["code"].split(".")
        if len(code_parts) == 2:
            region_id_to_admin1[r["id"]] = code_parts[1].lower()
            region_id_to_country[r["id"]] = code_parts[0].lower()

    # Slug generation with collision detection
    seen_slugs: dict[str, int] = {}  # slug → geoname_id (first owner)

    def _make_slug(slug: str, geoname_id: int) -> str:
        """Register a slug, appending -geoname_id on collision."""
        if slug in seen_slugs and seen_slugs[slug] != geoname_id:
            slug = f"{slug}-{geoname_id}"
        seen_slugs[slug] = geoname_id
        return slug

    # location records: (id, parent_id, type, slug, population, lat, lng, languages)
    location_records: list[tuple] = []

    # Macro regions (synthetic IDs 1-99)
    for macro in MACRO_REGIONS:
        macro_slug = slugify(macro["names"]["en"])
        macro_slug = _make_slug(macro_slug, macro["id"])
        location_records.append((macro["id"], None, "macro", macro_slug, None, None, None, None))

    # Countries
    for co in countries:
        country_slug = co["iso"].lower()
        country_slug = _make_slug(country_slug, co["id"])
        location_records.append(
            (
                co["id"],
                None,
                "country",
                country_slug,
                co["population"],
                co.get("lat"),
                co.get("lng"),
                co["languages"] or None,
            )
        )

    # Regions — inherit languages from parent country
    for r in regions:
        parent_id = r["parent_id"]
        langs = country_languages.get(parent_id, []) if parent_id else []
        cc = region_id_to_country.get(r["id"], "")
        admin1 = region_id_to_admin1.get(r["id"], "")
        region_slug = f"{cc}-{admin1}" if cc and admin1 else slugify(r["ascii_name"])
        region_slug = _make_slug(region_slug, r["id"])
        location_records.append(
            (
                r["id"],
                r["parent_id"],
                "region",
                region_slug,
                None,
                r.get("lat"),
                r.get("lng"),
                langs or None,
            )
        )

    # Cities — inherit languages from parent country
    for c in cities:
        country_id = iso_to_geoname.get(c["country_code"])
        langs = country_languages.get(country_id, []) if country_id else []
        cc = c["country_code"].lower()
        admin1_key = f"{c['country_code']}.{c['admin1_code']}"
        region_id = admin1_key_to_id.get(admin1_key)
        admin1 = region_id_to_admin1.get(region_id, "") if region_id else c["admin1_code"].lower()
        city_name_slug = slugify(c["ascii_name"])
        city_slug = f"{cc}-{admin1}-{city_name_slug}" if admin1 else f"{cc}-{city_name_slug}"
        city_slug = _make_slug(city_slug, c["id"])
        location_records.append(
            (
                c["id"],
                c["parent_id"],
                "city",
                city_slug,
                c["population"],
                c["lat"],
                c["lng"],
                langs or None,
            )
        )

    # location_name records: (location_id, locale, name, is_display)
    name_records: list[tuple] = []

    def _pick_display(names_with_flags: list[tuple[str, bool, bool, bool]]) -> str:
        """Pick best display name: preferred > short > first (all non-historic)."""
        for name, is_pref, _is_short, is_hist in names_with_flags:
            if is_pref and not is_hist:
                return name
        for name, _is_pref, is_short, is_hist in names_with_flags:
            if is_short and not is_hist:
                return name
        return names_with_flags[0][0]

    def _add_entity_names(
        entity_id: int,
        primary_name: str,
    ) -> None:
        """Add primary + alternate names for an entity, marking the best display name per locale."""
        alts = alt_names.get(entity_id, [])

        # Group names by locale (with flags for display selection)
        by_locale: dict[str, list[tuple[str, bool, bool, bool]]] = {}
        # English always includes the primary GeoNames name
        by_locale["en"] = [(primary_name, False, False, False)]
        for alt in alts:
            by_locale.setdefault(alt.locale, []).append(
                (alt.name, alt.is_preferred, alt.is_short, alt.is_historic)
            )

        # Pick the best display name per locale
        display_per_locale: dict[str, str] = {}
        for loc, names_with_flags in by_locale.items():
            display_per_locale[loc] = _pick_display(names_with_flags)

        # Add primary name record for en
        name_records.append(
            (entity_id, "en", primary_name, primary_name == display_per_locale["en"])
        )

        # Add all alternate names with per-locale is_display
        for alt in alts:
            is_display = alt.name == display_per_locale.get(alt.locale)
            name_records.append((entity_id, alt.locale, alt.name, is_display))

    # Macro region names (display in every locale)
    for macro in MACRO_REGIONS:
        for locale, name in macro["names"].items():
            if locale in locales:
                name_records.append((macro["id"], locale, name, True))

    # Country names
    for co in countries:
        _add_entity_names(co["id"], co["name"])

    # Region names
    for r in regions:
        _add_entity_names(r["id"], r["name"])

    # City names
    for c in cities:
        _add_entity_names(c["id"], c["name"])

    # Macro membership records: (macro_id, country_id)
    macro_member_records: list[tuple] = []
    for co in countries:
        iso = co["iso"]
        continent = co.get("continent", "")

        # EMEA
        if continent in _EMEA_CONTINENTS or iso in _EMEA_AS_COUNTRIES:
            macro_member_records.append((1, co["id"]))

        # APAC
        if continent in _APAC_CONTINENTS and iso not in _APAC_EXCLUDE:
            macro_member_records.append((2, co["id"]))

        # Americas
        if continent in _AMERICAS_CONTINENTS:
            macro_member_records.append((3, co["id"]))

        # EU
        if iso in _EU_COUNTRIES:
            macro_member_records.append((4, co["id"]))

        # DACH
        if iso in _DACH_COUNTRIES:
            macro_member_records.append((5, co["id"]))

        # LATAM
        if iso in _LATAM_COUNTRIES:
            macro_member_records.append((6, co["id"]))

        # Nordics
        if iso in _NORDIC_COUNTRIES:
            macro_member_records.append((7, co["id"]))

        # MENA
        if iso in _MENA_COUNTRIES:
            macro_member_records.append((8, co["id"]))

        # Worldwide — all countries
        macro_member_records.append((9, co["id"]))

    counts = {
        "locations": len(location_records),
        "names": len(name_records),
        "macro_members": len(macro_member_records),
        "countries": len(countries),
        "regions": len(regions),
        "cities": len(cities),
    }

    print(
        f"Prepared: {counts['locations']} locations, {counts['names']} names, "
        f"{counts['macro_members']} macro members"
    )
    print(
        f"  countries={counts['countries']} regions={counts['regions']} cities={counts['cities']}"
    )

    if dry_run:
        print("DRY RUN — skipping DB insert")
        return counts

    # ── Insert into DB ───────────────────────────────────────────────

    print("Inserting into database...")
    async with conn.transaction():
        # Truncate in correct order (children first)
        await conn.execute("TRUNCATE location_macro_member CASCADE")
        await conn.execute("TRUNCATE location_name CASCADE")
        await conn.execute("TRUNCATE location CASCADE")

        # Insert locations in order: macros → countries → regions → cities
        await conn.copy_records_to_table(
            "location",
            records=location_records,
            columns=["id", "parent_id", "type", "slug", "population", "lat", "lng", "languages"],
        )

        # Deduplicate by primary key (location_id, locale, name).
        # If a duplicate exists, preserve is_display=True from either copy.
        seen_name_keys: dict[tuple[int, str, str], int] = {}
        deduped_names: list[tuple] = []
        for rec in name_records:
            key = (rec[0], rec[1], rec[2])
            if key not in seen_name_keys:
                seen_name_keys[key] = len(deduped_names)
                deduped_names.append(rec)
            elif rec[3] and not deduped_names[seen_name_keys[key]][3]:
                # Upgrade is_display to True
                deduped_names[seen_name_keys[key]] = rec

        # Add is_display column if it doesn't exist yet
        await conn.execute("""
            DO $$ BEGIN
                ALTER TABLE location_name ADD COLUMN is_display boolean NOT NULL DEFAULT false;
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$
        """)

        await conn.copy_records_to_table(
            "location_name",
            records=deduped_names,
            columns=["location_id", "locale", "name", "is_display"],
        )

        # Insert macro memberships
        await conn.copy_records_to_table(
            "location_macro_member",
            records=macro_member_records,
            columns=["macro_id", "country_id"],
        )

    counts["names"] = len(deduped_names)
    print(
        f"Inserted: {counts['locations']} locations, {counts['names']} names, "
        f"{counts['macro_members']} macro members"
    )

    return counts


# ── CLI ──────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed location tables from GeoNames")
    parser.add_argument(
        "--locale",
        type=str,
        default=None,
        help="Comma-separated locales (default: en,de,fr,it). Use 'en' for fast testing.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse only, skip DB insert")
    parser.add_argument("--skip-download", action="store_true", help="Use cached files only")
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()

    locales = SUPPORTED_LOCALES
    if args.locale:
        locales = {l.strip() for l in args.locale.split(",")}

    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    try:
        counts = await seed(conn, locales, dry_run=args.dry_run, skip_download=args.skip_download)
        print(f"\nDone. Summary: {counts}")
    finally:
        await conn.close()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
