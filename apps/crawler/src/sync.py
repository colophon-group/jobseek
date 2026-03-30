"""CSV -> DB sync script.

Reads data/companies.csv and data/boards.csv, upserts rows into the database.
The DB is derived state — CSVs are the source of truth.

Writes to three targets:
- Local Postgres: full board config (scheduling columns)
- Supabase: minimal board reference (display/admin)
- Redis: board config hashes + initial schedule

Usage:
    uv run python -m src.sync              # sync both CSVs
    uv run python -m src.sync --dry-run    # show what would change
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import polars as pl
import structlog

if TYPE_CHECKING:
    import asyncpg

from src.config import settings
from src.core.monitors import api_monitor_types, monitor_needs_browser
from src.core.occupation_resolve import match_occupation
from src.core.scrapers import scraper_needs_browser
from src.db import close_all_pools, create_local_pool, create_pool
from src.redis_queue import close_redis, enqueue_monitor
from src.shared.logging import setup_logging

_API_MONITOR_TYPES = api_monitor_types()

log = structlog.get_logger()

DATA_DIR = Path(__file__).parent.parent / "data"

_UPSERT_OCCUPATION_DOMAINS = """
INSERT INTO occupation_domain (slug)
SELECT * FROM unnest($1::text[])
ON CONFLICT (slug) DO NOTHING
"""

_MIRROR_OCCUPATION_DOMAINS = """
INSERT INTO occupation_domain (id, slug)
SELECT * FROM unnest($1::int[], $2::text[])
ON CONFLICT (slug) DO UPDATE SET id = EXCLUDED.id
"""

_MIRROR_OCCUPATIONS = """
INSERT INTO occupation (id, slug)
SELECT * FROM unnest($1::int[], $2::text[])
ON CONFLICT (slug) DO UPDATE SET id = EXCLUDED.id
"""

_MIRROR_SENIORITY = """
INSERT INTO seniority (id, slug)
SELECT * FROM unnest($1::int[], $2::text[])
ON CONFLICT (slug) DO UPDATE SET id = EXCLUDED.id
"""

_UPSERT_OCCUPATION_DOMAIN_NAMES = """
INSERT INTO occupation_domain_name (domain_id, locale, name, is_display)
SELECT d.id, n.locale, n.name, n.is_display
FROM unnest($1::text[], $2::text[], $3::text[], $4::boolean[])
  AS n(slug, locale, name, is_display)
JOIN occupation_domain d ON d.slug = n.slug
ON CONFLICT (domain_id, locale, name) DO UPDATE SET
  is_display = EXCLUDED.is_display
"""

_SET_OCCUPATION_DOMAINS = """
UPDATE occupation o
SET domain_id = d.id
FROM unnest($1::text[], $2::text[]) AS m(occ_slug, domain_slug)
JOIN occupation_domain d ON d.slug = m.domain_slug
WHERE o.slug = m.occ_slug
  AND o.domain_id IS DISTINCT FROM d.id
"""

_UPSERT_OCCUPATIONS = """
INSERT INTO occupation (slug)
SELECT * FROM unnest($1::text[])
ON CONFLICT (slug) DO NOTHING
"""

_SET_OCCUPATION_PARENTS = """
UPDATE occupation c
SET parent_id = p.id
FROM unnest($1::text[], $2::text[]) AS m(child_slug, parent_slug)
JOIN occupation p ON p.slug = m.parent_slug
WHERE c.slug = m.child_slug
  AND c.parent_id IS DISTINCT FROM p.id
"""

_CLEAR_OCCUPATION_PARENTS = """
UPDATE occupation
SET parent_id = NULL
WHERE parent_id IS NOT NULL
  AND slug != ALL($1::text[])
"""

_UPSERT_OCCUPATION_NAMES = """
INSERT INTO occupation_name (occupation_id, locale, name, is_display)
SELECT o.id, n.locale, n.name, n.is_display
FROM unnest($1::text[], $2::text[], $3::text[], $4::boolean[])
  AS n(slug, locale, name, is_display)
JOIN occupation o ON o.slug = n.slug
ON CONFLICT (occupation_id, locale, name) DO UPDATE SET
  is_display = EXCLUDED.is_display
"""

_DELETE_STALE_OCCUPATION_NAMES = """
DELETE FROM occupation_name otn
WHERE NOT EXISTS (
  SELECT 1 FROM unnest($1::text[], $2::text[], $3::text[])
    AS n(slug, locale, name)
  JOIN occupation o ON o.slug = n.slug
  WHERE o.id = otn.occupation_id AND otn.locale = n.locale AND otn.name = n.name
)
"""

_UPSERT_SENIORITY = """
INSERT INTO seniority (slug)
SELECT * FROM unnest($1::text[])
ON CONFLICT (slug) DO NOTHING
"""

_UPSERT_SENIORITY_NAMES = """
INSERT INTO seniority_name (seniority_id, locale, name, is_display)
SELECT s.id, n.locale, n.name, n.is_display
FROM unnest($1::text[], $2::text[], $3::text[], $4::boolean[])
  AS n(slug, locale, name, is_display)
JOIN seniority s ON s.slug = n.slug
ON CONFLICT (seniority_id, locale, name) DO UPDATE SET
  is_display = EXCLUDED.is_display
"""

_UPSERT_TECHNOLOGIES = """
INSERT INTO technology (slug, name, category)
SELECT * FROM unnest($1::text[], $2::text[], $3::text[])
ON CONFLICT (slug) DO UPDATE SET
  name = COALESCE(EXCLUDED.name, technology.name),
  category = COALESCE(EXCLUDED.category, technology.category)
"""

_UPSERT_INDUSTRIES = """
INSERT INTO industry (id, name)
SELECT * FROM unnest($1::smallint[], $2::text[])
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
"""

_UPSERT_INDUSTRY_NAMES = """
INSERT INTO industry_name (industry_id, locale, name, is_display)
SELECT i.id, n.locale, n.name, n.is_display
FROM unnest($1::smallint[], $2::text[], $3::text[], $4::boolean[])
  AS n(industry_id, locale, name, is_display)
JOIN industry i ON i.id = n.industry_id
ON CONFLICT (industry_id, locale, name) DO UPDATE SET
  is_display = EXCLUDED.is_display
"""

_UPSERT_COMPANIES = """
INSERT INTO company (slug, name, website, logo, icon, logo_type,
                     industry, employee_count_range,
                     founded_year, extras)
SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::text[],
                     $5::text[], $6::text[], $7::smallint[],
                     $8::smallint[], $9::smallint[], $10::jsonb[])
ON CONFLICT (slug) DO UPDATE SET
    name = COALESCE(EXCLUDED.name, company.name),
    website = COALESCE(EXCLUDED.website, company.website),
    logo = COALESCE(EXCLUDED.logo, company.logo),
    icon = COALESCE(EXCLUDED.icon, company.icon),
    logo_type = COALESCE(EXCLUDED.logo_type, company.logo_type),
    industry = COALESCE(EXCLUDED.industry, company.industry),
    employee_count_range = COALESCE(EXCLUDED.employee_count_range, company.employee_count_range),
    founded_year = COALESCE(EXCLUDED.founded_year, company.founded_year),
    extras = CASE
        WHEN EXCLUDED.extras IS NOT NULL AND EXCLUDED.extras != '{}'::jsonb
        THEN EXCLUDED.extras
        ELSE COALESCE(company.extras, '{}'::jsonb)
    END,
    updated_at = now()
"""

_UPSERT_COMPANY_DESCRIPTIONS = """
INSERT INTO company_description (company_id, locale, description)
SELECT c.id, d.locale, d.description
FROM unnest($1::text[], $2::text[], $3::text[])
  AS d(slug, locale, description)
JOIN company c ON c.slug = d.slug
ON CONFLICT (company_id, locale) DO UPDATE SET
  description = EXCLUDED.description
"""

_UPSERT_BOARDS_SUPA = """
INSERT INTO job_board (company_id, board_slug, board_url, crawler_type, metadata,
                       throttle_key,
                       monitor_needs_browser, scraper_needs_browser)
SELECT c.id, b.board_slug, b.board_url, b.crawler_type, b.metadata::jsonb,
       b.throttle_key,
       b.monitor_needs_browser::boolean, b.scraper_needs_browser::boolean
FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[], $6::text[],
            $7::boolean[], $8::boolean[])
  AS b(company_slug, board_slug, board_url, crawler_type, metadata, throttle_key,
       monitor_needs_browser, scraper_needs_browser)
JOIN company c ON c.slug = b.company_slug
ON CONFLICT (board_url) DO UPDATE SET
    company_id = EXCLUDED.company_id,
    board_slug = COALESCE(EXCLUDED.board_slug, job_board.board_slug),
    crawler_type = EXCLUDED.crawler_type,
    metadata = EXCLUDED.metadata,
    throttle_key = EXCLUDED.throttle_key,
    monitor_needs_browser = EXCLUDED.monitor_needs_browser,
    scraper_needs_browser = EXCLUDED.scraper_needs_browser,
    is_enabled = true,
    updated_at = now()
"""

# Keep backward-compatible alias for tests
_UPSERT_BOARDS = _UPSERT_BOARDS_SUPA

_UPSERT_BOARD_LOCAL = """
INSERT INTO job_board (id, company_id, board_slug, board_url,
                       crawler_type, metadata,
                       check_interval_minutes, scrape_interval_hours,
                       throttle_key, monitor_needs_browser, scraper_needs_browser,
                       is_enabled)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (id) DO UPDATE SET
    company_id = EXCLUDED.company_id,
    board_slug = COALESCE(EXCLUDED.board_slug, job_board.board_slug),
    board_url = EXCLUDED.board_url,
    crawler_type = EXCLUDED.crawler_type,
    metadata = EXCLUDED.metadata,
    check_interval_minutes = EXCLUDED.check_interval_minutes,
    scrape_interval_hours = EXCLUDED.scrape_interval_hours,
    throttle_key = EXCLUDED.throttle_key,
    monitor_needs_browser = EXCLUDED.monitor_needs_browser,
    scraper_needs_browser = EXCLUDED.scraper_needs_browser,
    is_enabled = EXCLUDED.is_enabled,
    updated_at = now()
"""

_DISABLE_REMOVED_BOARDS = """
UPDATE job_board
SET is_enabled = false, board_status = 'disabled', updated_at = now()
WHERE board_url NOT IN (SELECT unnest($1::text[]))
  AND is_enabled = true
"""

_FETCH_BOARD_IDS = """
SELECT id, company_id, board_url FROM job_board WHERE board_url = ANY($1::text[])
"""


def _load_companies() -> pl.DataFrame:
    path = DATA_DIR / "companies.csv"
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_companies", count=len(df), path=str(path))
    return df


def _load_boards() -> pl.DataFrame:
    path = DATA_DIR / "boards.csv"
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_boards", count=len(df), path=str(path))
    return df


def _or_none(val: str | None) -> str | None:
    return val if val else None


def _compute_throttle_key(monitor_type: str, board_url: str) -> str:
    """Compute rate-limit grouping key from monitor type and board URL."""
    if monitor_type in _API_MONITOR_TYPES:
        return monitor_type
    return urlparse(board_url).hostname or board_url


def _int_or_none(val: str | None) -> int | None:
    if not val:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _load_occupation_domains() -> pl.DataFrame:
    path = DATA_DIR / "occupation_domains.csv"
    if not path.exists():
        return pl.DataFrame()
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_occupation_domains", count=len(df), path=str(path))
    return df


def _load_occupations() -> pl.DataFrame:
    path = DATA_DIR / "occupations.csv"
    if not path.exists():
        return pl.DataFrame()
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_occupations", count=len(df), path=str(path))
    return df


def _load_seniority() -> pl.DataFrame:
    path = DATA_DIR / "seniority.csv"
    if not path.exists():
        return pl.DataFrame()
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_seniority", count=len(df), path=str(path))
    return df


def _load_industries() -> pl.DataFrame:
    path = DATA_DIR / "industries.csv"
    if not path.exists():
        return pl.DataFrame()
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_industries", count=len(df), path=str(path))
    return df


def _load_company_descriptions() -> pl.DataFrame:
    path = DATA_DIR / "company_descriptions.csv"
    if not path.exists():
        return pl.DataFrame()
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_company_descriptions", count=len(df), path=str(path))
    return df


def _load_technologies() -> pl.DataFrame:
    path = DATA_DIR / "technologies.csv"
    if not path.exists():
        return pl.DataFrame()
    df = pl.read_csv(path, infer_schema_length=0)
    log.info("sync.loaded_technologies", count=len(df), path=str(path))
    return df


async def sync_technologies(
    conn: asyncpg.Connection, technologies: pl.DataFrame, dry_run: bool
) -> None:
    """Upsert technology slugs, names, and categories."""
    if len(technologies) == 0:
        return

    slugs = technologies["slug"].to_list()
    names = (
        technologies["name"].to_list() if "name" in technologies.columns else [None] * len(slugs)
    )
    categories = (
        technologies["category"].to_list()
        if "category" in technologies.columns
        else [None] * len(slugs)
    )

    if dry_run:
        log.info("sync.technologies.dry_run", slugs=len(slugs))
        return

    await conn.execute(_UPSERT_TECHNOLOGIES, slugs, names, categories)
    log.info("sync.technologies.upserted", slugs=len(slugs))


async def resolve_pending_misses(conn: asyncpg.Connection) -> None:
    """Resolve taxonomy misses that now match after CSV updates.

    For each pending miss, re-attempt matching. If successful:
    - Mark the miss as resolved
    - Backfill the FK on matching job_posting rows
    """
    pending = await conn.fetch(
        "SELECT id, taxonomy, raw_value FROM taxonomy_miss WHERE status = 'pending'"
    )
    if not pending:
        return

    # Load current ID maps
    occ_rows = await conn.fetch("SELECT id, slug FROM occupation")
    occ_ids = {r["slug"]: r["id"] for r in occ_rows}

    sen_rows = await conn.fetch("SELECT id, slug FROM seniority")
    sen_ids = {r["slug"]: r["id"] for r in sen_rows}

    tech_rows = await conn.fetch("SELECT id, slug FROM technology")
    tech_ids = {r["slug"]: r["id"] for r in tech_rows}

    # Build tech name -> slug map from CSV
    tech_csv = _load_technologies()
    tech_name_to_slug: dict[str, str] = {}
    if len(tech_csv) > 0:
        for row in tech_csv.iter_rows(named=True):
            name = row.get("name", "")
            if name:
                tech_name_to_slug[name.strip().lower()] = row["slug"]

    resolved_count = 0

    for miss in pending:
        miss_id = miss["id"]
        taxonomy = miss["taxonomy"]
        raw_value = miss["raw_value"]

        if taxonomy == "occupation":
            slug = match_occupation(raw_value)
            if slug and slug in occ_ids:
                fk_id = occ_ids[slug]
                # Backfill postings with this occupation string
                await conn.execute(
                    """
                    UPDATE job_posting
                    SET occupation_id = $1
                    WHERE lower(enrichment->>'occupation') = $2
                      AND occupation_id IS NULL
                    """,
                    fk_id,
                    raw_value,
                )
                await conn.execute(
                    "UPDATE taxonomy_miss SET status = 'resolved', resolved_to = $1 WHERE id = $2",
                    slug,
                    miss_id,
                )
                resolved_count += 1

        elif taxonomy == "seniority":
            # raw_value is the slug the LLM returned
            if raw_value in sen_ids:
                fk_id = sen_ids[raw_value]
                await conn.execute(
                    """
                    UPDATE job_posting
                    SET seniority_id = $1
                    WHERE enrichment->>'seniority' = $2
                      AND seniority_id IS NULL
                    """,
                    fk_id,
                    raw_value,
                )
                await conn.execute(
                    "UPDATE taxonomy_miss SET status = 'resolved', resolved_to = $1 WHERE id = $2",
                    raw_value,
                    miss_id,
                )
                resolved_count += 1

        elif taxonomy == "technology":
            slug = tech_name_to_slug.get(raw_value)
            if slug and slug in tech_ids:
                fk_id = tech_ids[slug]
                # Append technology ID to matching postings
                await conn.execute(
                    """
                    UPDATE job_posting
                    SET technology_ids = array_append(technology_ids, $1)
                    WHERE id IN (
                        SELECT jp.id
                        FROM job_posting jp,
                             jsonb_array_elements_text(jp.enrichment->'technologies') AS t
                        WHERE lower(t) = $2
                          AND (jp.technology_ids IS NULL OR NOT jp.technology_ids @> ARRAY[$1])
                    )
                    """,
                    fk_id,
                    raw_value,
                )
                await conn.execute(
                    "UPDATE taxonomy_miss SET status = 'resolved', resolved_to = $1 WHERE id = $2",
                    slug,
                    miss_id,
                )
                resolved_count += 1

    if resolved_count:
        log.info("sync.resolve_misses.resolved", count=resolved_count, total=len(pending))


async def sync_occupation_domains(
    conn: asyncpg.Connection, domains: pl.DataFrame, dry_run: bool
) -> None:
    """Upsert occupation domain slugs and their localized names."""
    if len(domains) == 0:
        return

    locales = ["en", "de", "fr", "it"]
    slugs: list[str] = []
    name_slugs: list[str] = []
    name_locales: list[str] = []
    name_values: list[str] = []
    name_is_display: list[bool] = []

    for row in domains.iter_rows(named=True):
        slug = row["slug"]
        slugs.append(slug)
        for locale in locales:
            name = row.get(locale)
            if name and name.strip():
                name_slugs.append(slug)
                name_locales.append(locale)
                name_values.append(name.strip())
                name_is_display.append(True)

    if dry_run:
        log.info("sync.occupation_domains.dry_run", slugs=len(slugs), names=len(name_slugs))
        return

    await conn.execute(_UPSERT_OCCUPATION_DOMAINS, slugs)
    if name_slugs:
        await conn.execute(
            _UPSERT_OCCUPATION_DOMAIN_NAMES, name_slugs, name_locales, name_values, name_is_display
        )
    log.info("sync.occupation_domains.upserted", slugs=len(slugs), names=len(name_slugs))


async def sync_occupations(
    conn: asyncpg.Connection, occupations: pl.DataFrame, dry_run: bool
) -> None:
    """Upsert occupation slugs and their display names."""
    if len(occupations) == 0:
        return

    locales = ["en", "de", "fr", "it"]
    slugs: list[str] = []
    name_slugs: list[str] = []
    name_locales: list[str] = []
    name_values: list[str] = []
    name_is_display: list[bool] = []

    for row in occupations.iter_rows(named=True):
        slug = row["slug"]
        slugs.append(slug)

        for locale in locales:
            name = row.get(locale)
            if name and name.strip():
                name_slugs.append(slug)
                name_locales.append(locale)
                name_values.append(name.strip())
                name_is_display.append(True)

        # Parse pipe-separated aliases
        aliases_raw = row.get("aliases")
        if aliases_raw and aliases_raw.strip():
            for alias in aliases_raw.split("|"):
                alias = alias.strip()
                if alias:
                    name_slugs.append(slug)
                    name_locales.append("*")
                    name_values.append(alias)
                    name_is_display.append(False)

    # Collect parent relationships
    child_slugs: list[str] = []
    parent_slugs: list[str] = []
    for row in occupations.iter_rows(named=True):
        parent = row.get("parent")
        if parent and parent.strip():
            child_slugs.append(row["slug"])
            parent_slugs.append(parent.strip())

    # Collect domain relationships
    domain_occ_slugs: list[str] = []
    domain_slugs: list[str] = []
    for row in occupations.iter_rows(named=True):
        domain = row.get("domain")
        if domain and domain.strip():
            domain_occ_slugs.append(row["slug"])
            domain_slugs.append(domain.strip())

    if dry_run:
        log.info(
            "sync.occupations.dry_run",
            slugs=len(slugs),
            names=len(name_slugs),
            parents=len(child_slugs),
            domains=len(domain_occ_slugs),
        )
        return

    await conn.execute(_UPSERT_OCCUPATIONS, slugs)
    if name_slugs:
        await conn.execute(
            _UPSERT_OCCUPATION_NAMES, name_slugs, name_locales, name_values, name_is_display
        )
        # Remove stale names no longer in CSV (e.g. removed aliases)
        deleted = await conn.execute(
            _DELETE_STALE_OCCUPATION_NAMES, name_slugs, name_locales, name_values
        )
        log.info("sync.occupations.deleted_stale_names", deleted=deleted)
    # Set parent relationships (must run after all slugs are inserted)
    if child_slugs:
        await conn.execute(_SET_OCCUPATION_PARENTS, child_slugs, parent_slugs)
        await conn.execute(_CLEAR_OCCUPATION_PARENTS, child_slugs)
    else:
        # No parents in CSV — clear all
        await conn.execute("UPDATE occupation SET parent_id = NULL WHERE parent_id IS NOT NULL")
    # Set domain relationships (must run after domains are synced)
    if domain_occ_slugs:
        await conn.execute(_SET_OCCUPATION_DOMAINS, domain_occ_slugs, domain_slugs)
    log.info(
        "sync.occupations.upserted",
        slugs=len(slugs),
        names=len(name_slugs),
        parents=len(child_slugs),
        domains=len(domain_occ_slugs),
    )


async def sync_seniority(
    conn: asyncpg.Connection, seniority_df: pl.DataFrame, dry_run: bool
) -> None:
    """Upsert seniority slugs and their display names."""
    if len(seniority_df) == 0:
        return

    locales = ["en", "de", "fr", "it"]
    slugs: list[str] = []
    name_slugs: list[str] = []
    name_locales: list[str] = []
    name_values: list[str] = []
    name_is_display: list[bool] = []

    for row in seniority_df.iter_rows(named=True):
        slug = row["slug"]
        slugs.append(slug)

        for locale in locales:
            name = row.get(locale)
            if name and name.strip():
                name_slugs.append(slug)
                name_locales.append(locale)
                name_values.append(name.strip())
                name_is_display.append(True)

        # Parse pipe-separated aliases
        aliases_raw = row.get("aliases")
        if aliases_raw and aliases_raw.strip():
            for alias in aliases_raw.split("|"):
                alias = alias.strip()
                if alias:
                    name_slugs.append(slug)
                    name_locales.append("*")
                    name_values.append(alias)
                    name_is_display.append(False)

    if dry_run:
        log.info("sync.seniority.dry_run", slugs=len(slugs), names=len(name_slugs))
        return

    await conn.execute(_UPSERT_SENIORITY, slugs)
    if name_slugs:
        await conn.execute(
            _UPSERT_SENIORITY_NAMES, name_slugs, name_locales, name_values, name_is_display
        )
    log.info("sync.seniority.upserted", slugs=len(slugs), names=len(name_slugs))


async def sync_industries(
    conn: asyncpg.Connection, industries: pl.DataFrame, dry_run: bool
) -> None:
    """Batch upsert industries and their localized names."""
    if len(industries) == 0:
        return

    locales = ["en", "de", "fr", "it"]
    ids: list[int] = []
    names: list[str] = []
    name_ids: list[int] = []
    name_locales: list[str] = []
    name_values: list[str] = []
    name_is_display: list[bool] = []

    for row in industries.iter_rows(named=True):
        ind_id = int(row["id"])
        # Use 'en' as the canonical name in the industry table
        en_name = row.get("en") or row.get("name", "")
        ids.append(ind_id)
        names.append(en_name)

        for locale in locales:
            val = row.get(locale)
            if val and val.strip():
                name_ids.append(ind_id)
                name_locales.append(locale)
                name_values.append(val.strip())
                name_is_display.append(True)

    if dry_run:
        log.info("sync.industries.dry_run", count=len(ids), names=len(name_ids))
        return

    await conn.execute(_UPSERT_INDUSTRIES, ids, names)
    if name_ids:
        await conn.execute(
            _UPSERT_INDUSTRY_NAMES, name_ids, name_locales, name_values, name_is_display
        )
    log.info("sync.industries.upserted", count=len(ids), names=len(name_ids))


async def sync_company_descriptions(
    conn: asyncpg.Connection, descriptions: pl.DataFrame, dry_run: bool
) -> None:
    """Upsert company descriptions from company_descriptions.csv."""
    if len(descriptions) == 0:
        return

    # CSV format: slug,en (more locales can be added as columns)
    locales = [c for c in descriptions.columns if c != "slug"]
    slugs: list[str] = []
    desc_locales: list[str] = []
    desc_values: list[str] = []

    for row in descriptions.iter_rows(named=True):
        slug = row["slug"]
        for locale in locales:
            val = row.get(locale)
            if val and val.strip():
                slugs.append(slug)
                desc_locales.append(locale)
                desc_values.append(val.strip())

    if dry_run:
        log.info("sync.company_descriptions.dry_run", count=len(slugs))
        return

    if slugs:
        await conn.execute(_UPSERT_COMPANY_DESCRIPTIONS, slugs, desc_locales, desc_values)
    log.info("sync.company_descriptions.upserted", count=len(slugs))


async def sync_companies(conn: asyncpg.Connection, companies: pl.DataFrame, dry_run: bool) -> None:
    """Batch upsert companies."""
    if len(companies) == 0:
        return

    slugs: list[str] = []
    names: list[str] = []
    websites: list[str | None] = []
    logos: list[str | None] = []
    icons: list[str | None] = []
    logo_types: list[str | None] = []
    industries: list[int | None] = []
    employee_ranges: list[int | None] = []
    founded_years: list[int | None] = []
    extras_list: list[str | None] = []

    for row in companies.iter_rows(named=True):
        slugs.append(row["slug"])
        names.append(row["name"])
        websites.append(_or_none(row.get("website")))
        logos.append(_or_none(row.get("logo_url")))
        icons.append(_or_none(row.get("icon_url")))
        logo_types.append(_or_none(row.get("logo_type")))
        industries.append(_int_or_none(row.get("industry")))
        employee_ranges.append(_int_or_none(row.get("employee_count_range")))
        founded_years.append(_int_or_none(row.get("founded_year")))
        extras_raw = _or_none(row.get("extras"))
        # Validate JSON
        if extras_raw:
            try:
                json.loads(extras_raw)
            except json.JSONDecodeError:
                log.error("sync.company.invalid_extras", slug=row["slug"], extras=extras_raw)
                extras_raw = None
        extras_list.append(extras_raw)

    if dry_run:
        log.info("sync.companies.dry_run", count=len(slugs))
        return

    await conn.execute(
        _UPSERT_COMPANIES,
        slugs,
        names,
        websites,
        logos,
        icons,
        logo_types,
        industries,
        employee_ranges,
        founded_years,
        extras_list,
    )
    log.info("sync.companies.upserted", count=len(slugs))


async def sync_boards(
    conn: asyncpg.Connection,
    boards: pl.DataFrame,
    dry_run: bool,
    *,
    local_conn: asyncpg.Connection | None = None,
) -> None:
    """Batch upsert boards to Supabase + local Postgres + Redis.

    Target 1 (Supabase): minimal board reference (display/admin only).
    Target 2 (local Postgres): full board config with scheduling columns.
    Target 3 (Redis): board config hashes + initial schedule.

    ``local_conn`` is optional for backward compatibility (tests, dry-run).
    """
    if len(boards) == 0:
        return

    company_slugs: list[str] = []
    board_slugs: list[str | None] = []
    board_urls: list[str] = []
    crawler_types: list[str] = []
    metadatas: list[str | None] = []
    metadata_objs: list[dict] = []
    throttle_keys: list[str] = []
    monitor_browser_flags: list[bool] = []
    scraper_browser_flags: list[bool] = []
    skipped = 0

    for row in boards.iter_rows(named=True):
        monitor_config_str = row.get("monitor_config") or None
        scraper_type = _or_none(row.get("scraper_type"))
        scraper_config_str = row.get("scraper_config") or None
        metadata_obj: dict = {}

        if monitor_config_str:
            try:
                parsed = json.loads(monitor_config_str)
                if not isinstance(parsed, dict):
                    raise ValueError("monitor_config must be a JSON object")
                metadata_obj.update(parsed)
            except json.JSONDecodeError:
                log.error(
                    "sync.board.invalid_config",
                    board_url=row["board_url"],
                    config=monitor_config_str,
                )
                skipped += 1
                continue
            except ValueError:
                log.error(
                    "sync.board.invalid_config",
                    board_url=row["board_url"],
                    config=monitor_config_str,
                )
                skipped += 1
                continue

        if scraper_type:
            metadata_obj["scraper_type"] = scraper_type

        if scraper_config_str:
            try:
                scraper_cfg = json.loads(scraper_config_str)
                if not isinstance(scraper_cfg, dict):
                    raise ValueError("scraper_config must be a JSON object")
                metadata_obj["scraper_config"] = scraper_cfg
            except json.JSONDecodeError:
                log.error(
                    "sync.board.invalid_scraper_config",
                    board_url=row["board_url"],
                    config=scraper_config_str,
                )
                skipped += 1
                continue
            except ValueError:
                log.error(
                    "sync.board.invalid_scraper_config",
                    board_url=row["board_url"],
                    config=scraper_config_str,
                )
                skipped += 1
                continue

        metadata: str | None = json.dumps(metadata_obj) if metadata_obj else None

        # Compute browser-need flags from crawler_type + config
        mon_type = row["monitor_type"]
        mon_browser = monitor_needs_browser(mon_type, metadata_obj)
        scr_type = metadata_obj.get("scraper_type")
        scr_cfg = metadata_obj.get("scraper_config")
        scr_browser = scraper_needs_browser(scr_type, scr_cfg) if scr_type else False
        # Also check fallback chain
        if not scr_browser and scr_cfg and isinstance(scr_cfg, dict):
            for fb in scr_cfg.get("fallback", []):
                fb_type = fb if isinstance(fb, str) else fb.get("type", "")
                fb_cfg = None if isinstance(fb, str) else fb.get("config")
                if scraper_needs_browser(fb_type, fb_cfg):
                    scr_browser = True
                    break

        company_slugs.append(row["company_slug"])
        board_slugs.append(_or_none(row.get("board_slug")))
        board_urls.append(row["board_url"])
        crawler_types.append(mon_type)
        metadatas.append(metadata)
        metadata_objs.append(metadata_obj)
        throttle_keys.append(_compute_throttle_key(mon_type, row["board_url"]))
        monitor_browser_flags.append(mon_browser)
        scraper_browser_flags.append(scr_browser)

    if dry_run:
        log.info("sync.boards.dry_run", count=len(board_urls), skipped=skipped)
        return

    if not board_urls:
        log.info("sync.boards.all_skipped", skipped=skipped)
        return

    # Supabase board sync removed — frontend never queries job_board.
    # Boards are only needed on local Postgres (worker scheduling) and Redis (queue).

    # --- Targets 2 & 3: local Postgres + Redis ---
    if local_conn is None:
        return

    # Fetch resolved board IDs + company IDs from Supabase
    id_rows = await conn.fetch(_FETCH_BOARD_IDS, board_urls)
    url_to_ids: dict[str, tuple] = {r["board_url"]: (r["id"], r["company_id"]) for r in id_rows}

    redis_enqueued = 0
    local_upserted = 0

    for i, board_url in enumerate(board_urls):
        ids = url_to_ids.get(board_url)
        if not ids:
            log.warning("sync.board.missing_id", board_url=board_url)
            continue

        board_id, company_id = ids
        mon_type = crawler_types[i]
        mon_browser = monitor_browser_flags[i]
        scr_browser = scraper_browser_flags[i]
        metadata_str = metadatas[i]
        throttle_key = throttle_keys[i]
        check_interval = 60  # default
        scrape_interval = 24  # default

        # Target 2: local Postgres (full board config with scheduling)
        await local_conn.execute(
            _UPSERT_BOARD_LOCAL,
            board_id,
            company_id,
            board_slugs[i],
            board_url,
            mon_type,
            metadata_str,
            check_interval,
            scrape_interval,
            throttle_key,
            mon_browser,
            scr_browser,
            True,  # is_enabled
        )
        local_upserted += 1

        # Target 3: Redis (board config hash + initial schedule)
        config = {
            "board_url": board_url,
            "crawler_type": mon_type,
            "company_id": str(company_id),
            "metadata": json.dumps(metadata_objs[i]) if metadata_objs[i] else "{}",
            "check_interval_minutes": str(check_interval),
            "scrape_interval_hours": str(scrape_interval),
            "throttle_key": throttle_key,
            "monitor_needs_browser": "1" if mon_browser else "0",
            "scraper_needs_browser": "1" if scr_browser else "0",
        }
        await enqueue_monitor(
            throttle_key,
            str(board_id),
            time.time(),
            config,
            browser=mon_browser,
            first_time=True,
        )
        redis_enqueued += 1

    # Disable removed boards in local Postgres too
    await local_conn.execute(_DISABLE_REMOVED_BOARDS, board_urls)

    log.info(
        "sync.boards.local_redis",
        local_upserted=local_upserted,
        redis_enqueued=redis_enqueued,
    )


async def _mirror_table(
    local_conn: asyncpg.Connection,
    table: str,
    mirror_sql: str,
    ids: list[int],
    slugs: list[str],
) -> None:
    """Re-insert rows into a local lookup table with Supabase-assigned IDs.

    Caller is responsible for deleting rows in the correct FK order before
    calling this.  After insert, advances the serial sequence past max(ids)
    to prevent future auto-increment collisions.
    """
    await local_conn.execute(mirror_sql, ids, slugs)
    max_id = max(ids)
    await local_conn.execute(
        "SELECT setval(pg_get_serial_sequence($1, 'id'), $2, true)",
        table,
        max_id,
    )


async def sync_lookup_tables_local(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection,
    occupation_domains: pl.DataFrame,
    occupations: pl.DataFrame,
    seniority_df: pl.DataFrame,
    technologies: pl.DataFrame,
    industries: pl.DataFrame,
    dry_run: bool,
) -> None:
    """Sync lookup tables to local Postgres using Supabase-assigned IDs.

    For occupation_domain, occupation, and seniority the local DB must use
    the exact same IDs as Supabase so that the exporter can copy FK references
    (occupation_id, seniority_id on job_posting) without translation.

    Strategy: delete all rows in FK-safe order (children before parents),
    re-insert with Supabase IDs, then sync names/parents/domains.
    """
    # Fetch Supabase-assigned IDs for all three tables
    domain_rows = (
        await supa_conn.fetch("SELECT id, slug FROM occupation_domain")
        if len(occupation_domains) > 0
        else []
    )
    occ_rows = (
        await supa_conn.fetch("SELECT id, slug FROM occupation") if len(occupations) > 0 else []
    )
    sen_rows = (
        await supa_conn.fetch("SELECT id, slug FROM seniority") if len(seniority_df) > 0 else []
    )

    # --- Drop FK constraints, delete, re-insert, re-add FKs ---
    # job_posting references occupation(id) and seniority(id), so we must
    # temporarily drop those constraints to replace the lookup rows.
    await local_conn.execute(
        "ALTER TABLE job_posting DROP CONSTRAINT IF EXISTS job_posting_occupation_id_fkey"
    )
    await local_conn.execute(
        "ALTER TABLE job_posting DROP CONSTRAINT IF EXISTS job_posting_seniority_id_fkey"
    )
    if occ_rows:
        await local_conn.execute("DELETE FROM occupation")
    if domain_rows:
        await local_conn.execute("DELETE FROM occupation_domain")
    if sen_rows:
        await local_conn.execute("DELETE FROM seniority")

    # --- Re-insert with explicit Supabase IDs ---
    if domain_rows:
        await _mirror_table(
            local_conn,
            "occupation_domain",
            _MIRROR_OCCUPATION_DOMAINS,
            [r["id"] for r in domain_rows],
            [r["slug"] for r in domain_rows],
        )
    if occ_rows:
        await _mirror_table(
            local_conn,
            "occupation",
            _MIRROR_OCCUPATIONS,
            [r["id"] for r in occ_rows],
            [r["slug"] for r in occ_rows],
        )
    if sen_rows:
        await _mirror_table(
            local_conn,
            "seniority",
            _MIRROR_SENIORITY,
            [r["id"] for r in sen_rows],
            [r["slug"] for r in sen_rows],
        )

    # --- Sync names, parents, domains (references the now-correct IDs) ---
    if len(occupation_domains) > 0:
        await sync_occupation_domains(local_conn, occupation_domains, dry_run)
    if len(occupations) > 0:
        await sync_occupations(local_conn, occupations, dry_run)
    if len(seniority_df) > 0:
        await sync_seniority(local_conn, seniority_df, dry_run)

    log.info(
        "sync.lookup_tables_local.mirrored",
        occupation_domains=len(domain_rows),
        occupations=len(occ_rows),
        seniority=len(sen_rows),
    )

    # Technologies and industries don't have the same problem:
    # - technologies use slug as the natural key (not auto-increment FK)
    # - industries have explicit IDs in the CSV
    # Sync them normally.
    await sync_technologies(local_conn, technologies, dry_run)
    await sync_industries(local_conn, industries, dry_run)

    # --- Re-add FK constraints (dropped above) ---
    await local_conn.execute(
        "ALTER TABLE job_posting ADD CONSTRAINT "
        "job_posting_occupation_id_fkey FOREIGN KEY (occupation_id) REFERENCES occupation(id)"
    )
    await local_conn.execute(
        "ALTER TABLE job_posting ADD CONSTRAINT "
        "job_posting_seniority_id_fkey FOREIGN KEY (seniority_id) REFERENCES seniority(id)"
    )

    log.info("sync.lookup_tables_local.complete")


async def run_sync(dry_run: bool = False) -> None:
    setup_logging(settings.log_level)

    occupation_domains = _load_occupation_domains()
    occupations = _load_occupations()
    seniority_df = _load_seniority()
    technologies = _load_technologies()
    industries = _load_industries()
    companies = _load_companies()
    company_descs = _load_company_descriptions()
    boards = _load_boards()

    if len(companies) == 0 and len(boards) == 0:
        log.info("sync.empty", msg="No data in CSVs, nothing to sync")
        return

    supa_pool = await create_pool()
    local_pool = None if dry_run else await create_local_pool()
    try:
        async with supa_pool.acquire() as supa_conn, supa_conn.transaction():
            await supa_conn.execute("SET lock_timeout = '30s'")

            # Lookup tables -> Supabase (web app queries these)
            await sync_occupation_domains(supa_conn, occupation_domains, dry_run)
            await sync_occupations(supa_conn, occupations, dry_run)
            await sync_seniority(supa_conn, seniority_df, dry_run)
            await sync_technologies(supa_conn, technologies, dry_run)
            await sync_industries(supa_conn, industries, dry_run)

            # Company data -> Supabase only (display data)
            await sync_companies(supa_conn, companies, dry_run)
            await sync_company_descriptions(supa_conn, company_descs, dry_run)

            # Boards -> Supabase + local Postgres + Redis
            local_conn = None
            if local_pool is not None:
                local_conn = await local_pool.acquire()
            try:
                await sync_boards(supa_conn, boards, dry_run, local_conn=local_conn)
            finally:
                if local_conn is not None:
                    await local_pool.release(local_conn)

            if not dry_run:
                await resolve_pending_misses(supa_conn)

            # Lookup tables -> local Postgres too (workers need them for CPU
            # processing: location_resolve, technology_resolve, etc.)
            # Must run inside the Supabase transaction so we can read back
            # the IDs that Supabase assigned to occupation/seniority rows.
            if local_pool is not None and not dry_run:
                async with local_pool.acquire() as local_conn:
                    await sync_lookup_tables_local(
                        supa_conn,
                        local_conn,
                        occupation_domains,
                        occupations,
                        seniority_df,
                        technologies,
                        industries,
                        dry_run,
                    )

        log.info(
            "sync.complete",
            occupation_domains=len(occupation_domains),
            occupations=len(occupations),
            seniority=len(seniority_df),
            technologies=len(technologies),
            industries=len(industries),
            companies=len(companies),
            company_descriptions=len(company_descs),
            boards=len(boards),
            dry_run=dry_run,
        )
    finally:
        await close_all_pools()
        await close_redis()


def main():
    parser = argparse.ArgumentParser(description="Sync CSV config to database")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()
    asyncio.run(run_sync(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
