"""One-shot backfill: Hetzner Postgres → local Typesense.

Reads job data from production Hetzner Postgres and company metadata from CSVs.
For local development/testing only — not for production use.

Usage:
    cd apps/crawler
    LOCAL_DATABASE_URL="postgresql://crawler:<pwd>@<host>:5432/crawler" \
    TYPESENSE_HOST=localhost TYPESENSE_PORT=8108 TYPESENSE_PROTOCOL=http \
    TYPESENSE_ADMIN_KEY=local_dev_typesense_key \
    uv run python ../../scripts/typesense-backfill-local.py [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import os
import sys
import time

import asyncpg
import structlog
import typesense

log = structlog.get_logger()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "apps", "crawler", "data")
BATCH_SIZE = 500
EXPERIENCE_MAX_OPEN_ENDED = 99


def _build_typesense_client() -> typesense.Client:
    return typesense.Client(
        {
            "nodes": [
                {
                    "host": os.environ.get("TYPESENSE_HOST", "localhost"),
                    "port": os.environ.get("TYPESENSE_PORT", "8108"),
                    "protocol": os.environ.get("TYPESENSE_PROTOCOL", "http"),
                }
            ],
            "api_key": os.environ["TYPESENSE_ADMIN_KEY"],
            "connection_timeout_seconds": 10,
        }
    )


def _encode_experience(exp_min: object, exp_max: object) -> tuple[int, int, float, float]:
    if exp_min is None:
        return -1, -1, -1.0, -1.0

    min_years = float(exp_min)
    if exp_max is None:
        max_years = float(EXPERIENCE_MAX_OPEN_ENDED)
        legacy_max = EXPERIENCE_MAX_OPEN_ENDED
    else:
        max_years = float(exp_max)
        legacy_max = math.floor(max_years)
    legacy_min = math.ceil(min_years)
    return legacy_min, legacy_max, min_years, max_years


def _load_companies_csv() -> dict[str, dict]:
    """Load company slug → {name, icon_url} from CSV."""
    path = os.path.join(DATA_DIR, "companies.csv")
    companies = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            companies[row["slug"]] = {
                "name": row["name"],
                "icon": row.get("icon_url") or None,
            }
    return companies


async def _load_taxonomy_maps(conn: asyncpg.Connection) -> dict:
    """Load all taxonomy lookup maps from Hetzner Postgres."""
    from collections import defaultdict

    # Location names (en only for denormalization)
    loc_names = {}
    rows = await conn.fetch(
        "SELECT location_id, name FROM location_name WHERE locale = 'en' AND is_display = true"
    )
    for r in rows:
        loc_names[r["location_id"]] = r["name"]

    # Location types (geo type)
    loc_types = {}
    rows = await conn.fetch("SELECT id, type::text AS type FROM location")
    for r in rows:
        loc_types[r["id"]] = r["type"]

    # Location ancestors (self + parents + macro regions)
    loc_parents: dict[int, int | None] = {}
    rows = await conn.fetch("SELECT id, parent_id FROM location")
    for r in rows:
        loc_parents[r["id"]] = r["parent_id"]

    macro_members: dict[int, list[int]] = defaultdict(list)
    rows = await conn.fetch("SELECT country_id, macro_id FROM location_macro_member")
    for r in rows:
        macro_members[r["country_id"]].append(r["macro_id"])

    loc_ancestors: dict[int, list[int]] = {}
    for lid in loc_parents:
        ancestors: set[int] = set()
        current: int | None = lid
        while current is not None:
            ancestors.add(current)
            if current in macro_members:
                ancestors.update(macro_members[current])
            current = loc_parents.get(current)
        loc_ancestors[lid] = list(ancestors)

    # Occupation names
    occ_names = {}
    rows = await conn.fetch(
        "SELECT occupation_id, name FROM occupation_name WHERE locale = 'en' AND is_display = true"
    )
    for r in rows:
        occ_names[r["occupation_id"]] = r["name"]

    # Occupation ancestors (self + parents)
    occ_parents: dict[int, int | None] = {}
    rows = await conn.fetch("SELECT id, parent_id FROM occupation")
    for r in rows:
        occ_parents[r["id"]] = r["parent_id"]

    occ_ancestors: dict[int, list[int]] = {}
    for oid in occ_parents:
        ancestors_set: set[int] = set()
        current_occ: int | None = oid
        while current_occ is not None:
            ancestors_set.add(current_occ)
            current_occ = occ_parents.get(current_occ)
        occ_ancestors[oid] = list(ancestors_set)

    # Seniority names
    sen_names = {}
    rows = await conn.fetch(
        "SELECT seniority_id, name FROM seniority_name WHERE locale = 'en' AND is_display = true"
    )
    for r in rows:
        sen_names[r["seniority_id"]] = r["name"]

    # Technology names
    tech_names = {}
    rows = await conn.fetch("SELECT id, name FROM technology")
    for r in rows:
        tech_names[r["id"]] = r["name"]

    # Company ID → slug mapping via job_board
    company_slugs = {}
    rows = await conn.fetch(
        "SELECT DISTINCT company_id, "
        "split_part(board_slug, '-', 1) AS slug_prefix, "
        "board_slug "
        "FROM job_board"
    )
    # board_slug format: "company-slug-ats-type" — extract company slug
    # by removing the last segment (ats type)
    for r in rows:
        cid = str(r["company_id"])
        slug = r["board_slug"]
        # Remove trailing ATS suffix (greenhouse, lever, workday, etc.)
        parts = slug.rsplit("-", 1)
        if len(parts) == 2 and len(parts[1]) < 20:
            # Heuristic: keep full slug, match against CSV
            company_slugs[cid] = slug
        else:
            company_slugs[cid] = slug

    return {
        "loc_names": loc_names,
        "loc_types": loc_types,
        "loc_ancestors": loc_ancestors,
        "occ_names": occ_names,
        "occ_ancestors": occ_ancestors,
        "sen_names": sen_names,
        "tech_names": tech_names,
        "company_slugs": company_slugs,
    }


def _build_doc(row: asyncpg.Record, maps: dict, csv_companies: dict) -> dict:
    """Build a Typesense document from a job_posting row."""
    company_id = str(row["company_id"])

    # Resolve company info — match the longest CSV slug that is a prefix
    # of board_slug (e.g., "stripe-careers" should match "stripe" not "str")
    board_slug = maps["company_slugs"].get(company_id, "")
    best_slug = ""
    company_info = None
    for csv_slug, info in csv_companies.items():
        if board_slug.startswith(csv_slug) and len(csv_slug) > len(best_slug):
            best_slug = csv_slug
            company_info = info
    company_slug = best_slug or board_slug or "unknown"
    if not company_info:
        company_info = {"name": board_slug or "Unknown", "icon": None}

    titles = row["titles"] or []
    title = titles[0] if titles else ""

    raw_location_ids = row["location_ids"] or []
    location_names = [maps["loc_names"].get(lid, f"loc-{lid}") for lid in raw_location_ids]
    location_types = row["location_types"] or []
    location_geo_types = [maps["loc_types"].get(lid, "city") for lid in raw_location_ids]

    # Pad location_types to match raw location_ids length
    while len(location_types) < len(raw_location_ids):
        location_types.append("onsite")
    location_types = location_types[: len(raw_location_ids)]

    # Expand location_ids: leaf IDs first (aligned with names/geo_types), then ancestors
    ancestor_only: set[int] = set()
    for lid in raw_location_ids:
        ancestor_only.update(maps["loc_ancestors"].get(lid, [lid]))
    ancestor_only -= set(raw_location_ids)
    expanded_location_ids = list(raw_location_ids) + list(ancestor_only)

    tech_ids = row["technology_ids"] or []
    tech_names = [maps["tech_names"].get(tid, f"tech-{tid}") for tid in tech_ids]

    exp_min, exp_max, exp_min_years, exp_max_years = _encode_experience(
        row["experience_min"],
        row["experience_max"],
    )

    locales = list(row["locales"]) if row["locales"] else ["_none"]
    if not locales:
        locales = ["_none"]

    first_seen = row["first_seen_at"]
    last_seen = row["last_seen_at"]

    doc = {
        "id": str(row["id"]),
        "company_id": company_id,
        "company_name": company_info["name"],
        "company_slug": company_slug,
        "title": title,
        "is_active": row["is_active"],
        "location_ids": expanded_location_ids,
        "location_names": location_names,
        "location_types": location_types,
        "location_geo_types": location_geo_types,
        "technology_ids": tech_ids,
        "technology_names": tech_names,
        "employment_type": row["employment_type"] or None,
        "experience_min": exp_min,
        "experience_max": exp_max,
        "experience_min_years": exp_min_years,
        "experience_max_years": exp_max_years,
        "locales": locales,
        "source_url": row["source_url"] or None,
        "first_seen_at": int(first_seen.timestamp()) if first_seen else 0,
    }

    if company_info["icon"]:
        doc["company_icon"] = company_info["icon"]

    if row["occupation_id"] is not None:
        doc["occupation_id"] = row["occupation_id"]
        doc["occupation_ids"] = maps["occ_ancestors"].get(
            row["occupation_id"], [row["occupation_id"]]
        )
        doc["occupation_name"] = maps["occ_names"].get(
            row["occupation_id"], f"occ-{row['occupation_id']}"
        )

    if row["seniority_id"] is not None:
        doc["seniority_id"] = row["seniority_id"]
        doc["seniority_name"] = maps["sen_names"].get(
            row["seniority_id"], f"sen-{row['seniority_id']}"
        )

    if row["salary_eur"] is not None and row["salary_eur"] > 0:
        doc["salary_eur"] = row["salary_eur"]

    if last_seen:
        doc["last_seen_at"] = int(last_seen.timestamp())

    return doc


async def backfill(limit: int | None = None):
    db_url = os.environ.get("LOCAL_DATABASE_URL")
    if not db_url:
        print("LOCAL_DATABASE_URL not set")
        sys.exit(1)

    ts = _build_typesense_client()
    try:
        health = ts.api_call.get("/health")
        print(f"Typesense health: {health}")
    except Exception:
        # Try alternate health check
        import requests

        host = os.environ.get("TYPESENSE_HOST", "localhost")
        port = os.environ.get("TYPESENSE_PORT", "8108")
        proto = os.environ.get("TYPESENSE_PROTOCOL", "http")
        r = requests.get(
            f"{proto}://{host}:{port}/health",
            headers={"X-TYPESENSE-API-KEY": os.environ["TYPESENSE_ADMIN_KEY"]},
        )
        print(f"Typesense health: {r.json()}")

    print("Loading company CSV...")
    csv_companies = _load_companies_csv()
    print(f"  {len(csv_companies)} companies from CSV")

    print("Connecting to Hetzner Postgres...")
    conn = await asyncpg.connect(db_url, ssl="disable")

    print("Loading taxonomy maps...")
    maps = await _load_taxonomy_maps(conn)
    print(
        f"  locations: {len(maps['loc_names'])}, "
        f"occupations: {len(maps['occ_names'])}, "
        f"technologies: {len(maps['tech_names'])}, "
        f"company mappings: {len(maps['company_slugs'])}"
    )

    # Count total
    total = await conn.fetchval("SELECT count(*) FROM job_posting")
    target = min(total, limit) if limit else total
    print(f"Backfilling {target:,} of {total:,} job postings...")

    t0 = time.monotonic()
    exported = 0
    errors = 0
    offset = 0

    while exported < target:
        batch_limit = min(BATCH_SIZE, target - exported)
        rows = await conn.fetch(
            "SELECT id, company_id, board_id, is_active, locales, titles, "
            "location_ids, location_types, employment_type, source_url, "
            "first_seen_at, last_seen_at, salary_eur, experience_min, "
            "experience_max, occupation_id, seniority_id, technology_ids "
            f"FROM job_posting ORDER BY first_seen_at, id "
            f"LIMIT {batch_limit} OFFSET {offset}"
        )
        if not rows:
            break

        docs = []
        for row in rows:
            try:
                docs.append(_build_doc(row, maps, csv_companies))
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  Error building doc {row['id']}: {e}")

        if docs:
            try:
                result = ts.collections["job_posting"].documents.import_(docs, {"action": "upsert"})
                # Count failures in batch
                batch_errors = sum(
                    1 for r in result if isinstance(r, dict) and not r.get("success", True)
                )
                if batch_errors:
                    errors += batch_errors
            except Exception as e:
                print(f"  Typesense batch error at offset {offset}: {e}")
                errors += len(docs)

        exported += len(rows)
        offset += len(rows)
        elapsed = time.monotonic() - t0
        rate = exported / elapsed if elapsed > 0 else 0

        if exported % 10000 < BATCH_SIZE:
            print(
                f"  {exported:>8,} / {target:,} "
                f"({exported * 100 // target}%) "
                f"[{rate:.0f} docs/s, {errors} errors]"
            )

    elapsed = time.monotonic() - t0
    print(
        f"\nDone: {exported:,} docs in {elapsed:.1f}s "
        f"({exported / elapsed:.0f} docs/s), {errors} errors"
    )

    # Verify
    info = ts.collections["job_posting"].retrieve()
    print(f"Typesense job_posting: {info['num_documents']:,} documents")

    await conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max rows to export")
    args = parser.parse_args()
    asyncio.run(backfill(args.limit))


if __name__ == "__main__":
    main()
