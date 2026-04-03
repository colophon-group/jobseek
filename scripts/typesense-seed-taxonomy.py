"""Seed taxonomy collections from Hetzner Postgres.

Populates: location, occupation, seniority, technology, company collections.

Usage:
    cd apps/crawler
    LOCAL_DATABASE_URL="postgresql://crawler:<pwd>@<host>:5432/crawler" \
    TYPESENSE_HOST=localhost TYPESENSE_PORT=8108 TYPESENSE_PROTOCOL=http \
    TYPESENSE_ADMIN_KEY=local_dev_typesense_key \
    uv run python ../../scripts/typesense-seed-taxonomy.py
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys

import asyncpg
import typesense

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "apps", "crawler", "data")


def _ts() -> typesense.Client:
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


def _upsert(client: typesense.Client, collection: str, docs: list[dict]):
    if not docs:
        return
    result = client.collections[collection].documents.import_(docs, {"action": "upsert"})
    errors = sum(1 for r in result if isinstance(r, dict) and not r.get("success", True))
    print(f"  {collection}: {len(docs)} docs, {errors} errors")


async def seed():
    db_url = os.environ.get("LOCAL_DATABASE_URL")
    if not db_url:
        print("LOCAL_DATABASE_URL not set")
        sys.exit(1)

    ts = _ts()
    conn = await asyncpg.connect(db_url, ssl="disable")

    # Active posting counts
    print("Computing posting counts...")
    loc_counts: dict[int, int] = {}
    rows = await conn.fetch(
        "SELECT unnest(location_ids) AS lid, count(*)::int AS cnt "
        "FROM job_posting WHERE is_active = true GROUP BY 1"
    )
    for r in rows:
        loc_counts[r["lid"]] = r["cnt"]

    occ_counts: dict[int, int] = {}
    rows = await conn.fetch(
        "SELECT occupation_id, count(*)::int AS cnt "
        "FROM job_posting WHERE is_active = true AND occupation_id IS NOT NULL GROUP BY 1"
    )
    for r in rows:
        occ_counts[r["occupation_id"]] = r["cnt"]

    sen_counts: dict[int, int] = {}
    rows = await conn.fetch(
        "SELECT seniority_id, count(*)::int AS cnt "
        "FROM job_posting WHERE is_active = true AND seniority_id IS NOT NULL GROUP BY 1"
    )
    for r in rows:
        sen_counts[r["seniority_id"]] = r["cnt"]

    tech_counts: dict[int, int] = {}
    rows = await conn.fetch(
        "SELECT unnest(technology_ids) AS tid, count(*)::int AS cnt "
        "FROM job_posting WHERE is_active = true GROUP BY 1"
    )
    for r in rows:
        tech_counts[r["tid"]] = r["cnt"]

    company_counts: dict[str, dict] = {}
    rows = await conn.fetch(
        "SELECT company_id, count(*)::int AS active, "
        "count(*) FILTER (WHERE first_seen_at > now() - interval '1 year')::int AS year_cnt "
        "FROM job_posting WHERE is_active = true GROUP BY 1"
    )
    for r in rows:
        company_counts[str(r["company_id"])] = {
            "active": r["active"],
            "year": r["year_cnt"],
        }

    # --- Locations ---
    print("Seeding locations...")
    loc_rows = await conn.fetch(
        "SELECT l.id, l.type::text AS type, l.population, l.lat, l.lng, l.parent_id, "
        "lo.slug "
        "FROM location l LEFT JOIN location lo ON lo.id = l.id "
        "WHERE l.id IN (SELECT DISTINCT unnest(location_ids) FROM job_posting WHERE is_active)"
    )
    # Actually slug is on location table itself in newer schema
    loc_rows = await conn.fetch(
        "SELECT id, type::text AS type, population, lat, lng, parent_id, slug "
        "FROM location"
    )

    # Load all locale names
    name_rows = await conn.fetch(
        "SELECT location_id, locale, name, is_display FROM location_name"
    )
    loc_name_map: dict[int, dict[str, str]] = {}
    for r in name_rows:
        lid = r["location_id"]
        if lid not in loc_name_map:
            loc_name_map[lid] = {}
        if r["is_display"]:
            loc_name_map[lid][r["locale"]] = r["name"]

    # Parent name lookup
    parent_names = {}
    for r in loc_rows:
        names = loc_name_map.get(r["id"], {})
        parent_names[r["id"]] = names.get("en", names.get("", ""))

    docs = []
    for r in loc_rows:
        lid = r["id"]
        names = loc_name_map.get(lid, {})
        cnt = loc_counts.get(lid, 0)
        doc: dict = {
            "id": str(lid),
            "location_id": lid,
            "slug": r["slug"] or f"loc-{lid}",
            "name_en": names.get("en", names.get("", f"Location {lid}")),
            "type": r["type"],
            "has_active_postings": cnt > 0,
            "active_posting_count": cnt,
        }
        for loc in ["de", "fr", "it"]:
            if loc in names:
                doc[f"name_{loc}"] = names[loc]
        if r["lat"] is not None and r["lng"] is not None:
            doc["coordinates"] = [float(r["lat"]), float(r["lng"])]
        if r["population"]:
            doc["population"] = r["population"]
        if r["parent_id"] and r["parent_id"] in parent_names:
            doc["parent_name"] = parent_names[r["parent_id"]]
        docs.append(doc)

    _upsert(ts, "location", docs)

    # --- Occupations ---
    print("Seeding occupations...")
    occ_rows = await conn.fetch("SELECT id, slug FROM occupation")
    occ_name_rows = await conn.fetch(
        "SELECT occupation_id, locale, name, is_display FROM occupation_name"
    )
    # Build per-locale docs
    occ_name_map: dict[int, dict[str, list]] = {}
    occ_display: dict[int, dict[str, str]] = {}
    for r in occ_name_rows:
        oid = r["occupation_id"]
        loc = r["locale"]
        if oid not in occ_name_map:
            occ_name_map[oid] = {}
            occ_display[oid] = {}
        if loc not in occ_name_map[oid]:
            occ_name_map[oid][loc] = []
        occ_name_map[oid][loc].append(r["name"])
        if r["is_display"]:
            occ_display[oid][loc] = r["name"]

    occ_slugs = {r["id"]: r["slug"] for r in occ_rows}
    docs = []
    for oid, locales in occ_name_map.items():
        cnt = occ_counts.get(oid, 0)
        slug = occ_slugs.get(oid, f"occ-{oid}")
        for loc, names in locales.items():
            if loc in ("", "*"):
                continue
            display = occ_display.get(oid, {}).get(loc, names[0] if names else f"Occ {oid}")
            aliases = [n for n in names if n != display]
            docs.append(
                {
                    "id": f"{oid}-{loc}",
                    "occupation_id": oid,
                    "slug": slug,
                    "name": display,
                    "aliases": aliases,
                    "locale": loc,
                    "has_active_postings": cnt > 0,
                    "active_posting_count": cnt,
                }
            )
    _upsert(ts, "occupation", docs)

    # --- Seniorities ---
    print("Seeding seniorities...")
    sen_rows = await conn.fetch("SELECT id, slug FROM seniority")
    sen_name_rows = await conn.fetch(
        "SELECT seniority_id, locale, name, is_display FROM seniority_name"
    )
    sen_name_map: dict[int, dict[str, list]] = {}
    sen_display: dict[int, dict[str, str]] = {}
    for r in sen_name_rows:
        sid = r["seniority_id"]
        loc = r["locale"]
        if sid not in sen_name_map:
            sen_name_map[sid] = {}
            sen_display[sid] = {}
        if loc not in sen_name_map[sid]:
            sen_name_map[sid][loc] = []
        sen_name_map[sid][loc].append(r["name"])
        if r["is_display"]:
            sen_display[sid][loc] = r["name"]

    sen_slugs = {r["id"]: r["slug"] for r in sen_rows}
    docs = []
    for sid, locales in sen_name_map.items():
        cnt = sen_counts.get(sid, 0)
        slug = sen_slugs.get(sid, f"sen-{sid}")
        for loc, names in locales.items():
            if loc in ("", "*"):
                continue
            display = sen_display.get(sid, {}).get(loc, names[0] if names else f"Sen {sid}")
            aliases = [n for n in names if n != display]
            docs.append(
                {
                    "id": f"{sid}-{loc}",
                    "seniority_id": sid,
                    "slug": slug,
                    "name": display,
                    "aliases": aliases,
                    "locale": loc,
                    "has_active_postings": cnt > 0,
                    "active_posting_count": cnt,
                }
            )
    _upsert(ts, "seniority", docs)

    # --- Technologies ---
    print("Seeding technologies...")
    tech_rows = await conn.fetch("SELECT id, slug, name, category FROM technology")
    docs = []
    for r in tech_rows:
        tid = r["id"]
        cnt = tech_counts.get(tid, 0)
        docs.append(
            {
                "id": str(tid),
                "technology_id": tid,
                "slug": r["slug"],
                "name": r["name"] or r["slug"],
                "category": r["category"],
                "has_active_postings": cnt > 0,
                "active_posting_count": cnt,
            }
        )
    _upsert(ts, "technology", docs)

    # --- Companies ---
    print("Seeding companies...")
    csv_companies = {}
    csv_path = os.path.join(DATA_DIR, "companies.csv")
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            csv_companies[row["slug"]] = row

    # Map company_id to slug via job_board
    board_rows = await conn.fetch(
        "SELECT DISTINCT company_id, board_slug FROM job_board"
    )
    cid_to_slug: dict[str, str] = {}
    for r in board_rows:
        cid = str(r["company_id"])
        slug = r["board_slug"]
        for csv_slug in csv_companies:
            if slug.startswith(csv_slug):
                cid_to_slug[cid] = csv_slug
                break

    docs = []
    for cid, csv_slug in cid_to_slug.items():
        csv_row = csv_companies.get(csv_slug, {})
        counts = company_counts.get(cid, {"active": 0, "year": 0})
        doc: dict = {
            "id": cid,
            "name": csv_row.get("name", csv_slug),
            "slug": csv_slug,
            "active_posting_count": counts["active"],
            "year_posting_count": counts["year"],
        }
        if csv_row.get("icon_url"):
            doc["icon"] = csv_row["icon_url"]
        if csv_row.get("industry"):
            try:
                doc["industry_id"] = int(csv_row["industry"])
            except ValueError:
                pass
        docs.append(doc)

    _upsert(ts, "company", docs)

    await conn.close()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(seed())
