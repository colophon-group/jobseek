"""CSV -> DB sync script.

Reads data/companies.csv and data/boards.csv, upserts rows into the database.
The DB is derived state — CSVs are the source of truth.

Writes to four targets:
- Local Postgres: full board config (scheduling columns)
- Supabase: minimal board reference (display/admin)
- Redis: board config hashes + initial schedule
- Typesense: taxonomy, company, and watchlist collections (fire-and-forget)

Usage:
    uv run python -m src.sync              # sync both CSVs
    uv run python -m src.sync --dry-run    # show what would change
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import polars as pl
import structlog
from typesense.exceptions import ObjectNotFound

if TYPE_CHECKING:
    import asyncpg
    import typesense

from src.config import settings
from src.core.monitors import api_monitor_types, monitor_needs_browser
from src.core.occupation_resolve import match_occupation
from src.core.scrapers import scraper_needs_browser
from src.db import close_all_pools, create_local_pool, create_pool
from src.redis_queue import close_redis, enqueue_monitor, remove_monitor
from src.shared.logging import setup_logging
from src.typesense_client import get_typesense_client

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

# When ``board_url`` is renamed in CSV but the slug stays (e.g. changing
# the Greenhouse token from ``abodo`` to ``apartmentiq`` while keeping
# slug ``apartmentiq-greenhouse``), ``_UPSERT_BOARDS_SUPA`` below would
# hit the ``board_slug`` unique constraint: the INSERT path fires for
# the new URL, and the old-URL row still owns the slug. Run this first
# to rewrite the URL on the existing row so the subsequent UPSERT can
# take the ``ON CONFLICT (board_url)`` branch cleanly.
_REALIGN_RENAMED_BOARD_URLS_SUPA = """
UPDATE job_board jb
SET board_url = b.board_url,
    updated_at = now()
FROM unnest($1::text[], $2::text[], $3::text[])
  AS b(company_slug, board_slug, board_url)
JOIN company c ON c.slug = b.company_slug
WHERE jb.company_id = c.id
  AND jb.board_slug IS NOT NULL
  AND jb.board_slug = b.board_slug
  AND jb.board_url IS DISTINCT FROM b.board_url
"""

_UPSERT_BOARDS_SUPA = """
INSERT INTO job_board (company_id, board_slug, board_url, crawler_type, metadata)
SELECT c.id, b.board_slug, b.board_url, b.crawler_type, b.metadata::jsonb
FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[])
  AS b(company_slug, board_slug, board_url, crawler_type, metadata)
JOIN company c ON c.slug = b.company_slug
ON CONFLICT (board_url) DO UPDATE SET
    company_id = EXCLUDED.company_id,
    board_slug = COALESCE(EXCLUDED.board_slug, job_board.board_slug),
    crawler_type = EXCLUDED.crawler_type,
    metadata = EXCLUDED.metadata,
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
    -- Preserve runtime-written metadata subkeys that the pipeline persists
    -- via _UPDATE_METADATA during normal operation. Without this, every
    -- `crawler sync` wipes out:
    --   * ``sitemap_url`` — written by monitors that discover the sitemap
    --     URL dynamically (eightfold, api_sniffer-based boards)
    --   * ``pcsx_watermark`` — the eightfold incremental high-water mark
    --   * ``recent_discovered_counts`` / ``suspect_streak`` — rolling
    --     state for the gone-detection guards (#2723/#2724). Wiping
    --     these every CSV push silently neuters the drop guard for
    --     ``_DROP_GUARD_MIN_HISTORY`` cycles after each sync.
    --
    -- ``sitemap_url`` is a pure runtime signal (CSV never sets it), so
    -- preserve it verbatim from the existing row.
    --
    -- ``pcsx_watermark`` is a mixed subkey: some fields are runtime state
    -- (``max_ts``, ``last_full_at``, ``last_incremental_at``, ``enabled``,
    -- ``extra``) and some are CSV-controlled configuration (``auto_full_crawl``,
    -- ``interval_days``). We layer them so that CSV wins for config and
    -- runtime wins for state:
    --
    --   final_pcsx_watermark = csv_pcsx_watermark
    --                          || runtime_state_fields_from_existing
    --
    -- This means an operator who edits ``auto_full_crawl`` in the CSV and
    -- re-syncs will see the change take effect immediately, but the watermark
    -- itself (max_ts and friends) stays intact so the next scheduled run
    -- still knows where incremental pagination left off.
    --
    -- ``delist_threshold`` (#2725), ``drop_threshold``, and ``blast_radius_floor``
    -- are CSV-controllable per-board overrides. CSV wins when set; otherwise
    -- the existing runtime value (typically unset) is kept.
    -- ``recent_discovered_counts`` and ``suspect_streak`` are runtime state
    -- preserved verbatim from the existing row.
    metadata = EXCLUDED.metadata || jsonb_strip_nulls(jsonb_build_object(
        'sitemap_url', job_board.metadata -> 'sitemap_url',
        'recent_discovered_counts', job_board.metadata -> 'recent_discovered_counts',
        'suspect_streak', job_board.metadata -> 'suspect_streak',
        'delist_threshold', COALESCE(
            EXCLUDED.metadata -> 'delist_threshold',
            job_board.metadata -> 'delist_threshold'
        ),
        'drop_threshold', COALESCE(
            EXCLUDED.metadata -> 'drop_threshold',
            job_board.metadata -> 'drop_threshold'
        ),
        'blast_radius_floor', COALESCE(
            EXCLUDED.metadata -> 'blast_radius_floor',
            job_board.metadata -> 'blast_radius_floor'
        ),
        'pcsx_watermark', CASE
            WHEN job_board.metadata -> 'pcsx_watermark' IS NULL THEN NULL
            ELSE COALESCE(EXCLUDED.metadata -> 'pcsx_watermark', '{}'::jsonb)
                 || jsonb_strip_nulls(jsonb_build_object(
                     'max_ts', job_board.metadata -> 'pcsx_watermark' -> 'max_ts',
                     'last_full_at',
                         job_board.metadata -> 'pcsx_watermark' -> 'last_full_at',
                     'last_incremental_at',
                         job_board.metadata -> 'pcsx_watermark' -> 'last_incremental_at',
                     'enabled', job_board.metadata -> 'pcsx_watermark' -> 'enabled',
                     'extra', job_board.metadata -> 'pcsx_watermark' -> 'extra'
                 ))
        END
    )),
    check_interval_minutes = EXCLUDED.check_interval_minutes,
    scrape_interval_hours = EXCLUDED.scrape_interval_hours,
    throttle_key = EXCLUDED.throttle_key,
    monitor_needs_browser = EXCLUDED.monitor_needs_browser,
    scraper_needs_browser = EXCLUDED.scraper_needs_browser,
    -- Preserve runtime-driven disables. ``_RECORD_FAILURE`` and
    -- ``_RECORD_BOARD_GONE`` set ``is_enabled = false`` plus a
    -- ``board_status`` of ``'disabled'`` or ``'gone'`` when the
    -- board has been failing or its upstream slug returned 404.
    -- Without this CASE, every ``crawler sync`` resurrects
    -- ``is_enabled = true`` and the (admittedly already-orthogonal-
    -- to-the-Redis-claim-path) Postgres state diverges from
    -- the runtime truth. To re-enable, an operator removes the
    -- ``board_status`` row via SQL or via deleting+re-adding
    -- the CSV entry. See issue #2215.
    is_enabled = CASE
        WHEN job_board.board_status IN ('disabled', 'gone') THEN false
        ELSE EXCLUDED.is_enabled
    END,
    updated_at = now()
"""

_DISABLE_REMOVED_BOARDS = """
UPDATE job_board
SET is_enabled = false, board_status = 'disabled', updated_at = now()
WHERE board_url NOT IN (SELECT unnest($1::text[]))
  AND is_enabled = true
"""

# Every row that should NOT be in Redis. A board appearing here while its
# board_id is still live in ``monitors_*:{domain}`` is why dead boards keep
# producing ``batch.monitor.error`` after being removed from ``boards.csv`` —
# Postgres gets disabled but the worker claims from Redis, not Postgres.
_FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP = """
SELECT id::text AS board_id, throttle_key
FROM job_board
WHERE is_enabled = false OR board_status IN ('disabled', 'gone')
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


async def _mirror_companies_to_local(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection,
) -> None:
    """Copy companies from Supabase to local Postgres, preserving UUIDs.

    job_posting.company_id references Supabase-generated UUIDs, so local
    must have the same IDs for the exporter's company_info lookup to work.
    """
    rows = await supa_conn.fetch(
        "SELECT id, slug, name, website, logo, icon, logo_type, "
        "industry, employee_count_range, founded_year, extras "
        "FROM company"
    )
    if not rows:
        return

    # Delete any local rows whose slug matches but UUID differs (stale from
    # earlier sync that generated new UUIDs instead of preserving Supabase's).
    await local_conn.execute(
        "DELETE FROM company WHERE slug = ANY($1::text[]) AND id != ALL($2::uuid[])",
        [r["slug"] for r in rows],
        [r["id"] for r in rows],
    )

    await local_conn.execute(
        "INSERT INTO company (id, slug, name, website, logo, icon, logo_type, "
        "industry, employee_count_range, founded_year, extras) "
        "SELECT * FROM unnest($1::uuid[], $2::text[], $3::text[], $4::text[], "
        "$5::text[], $6::text[], $7::text[], $8::smallint[], $9::smallint[], "
        "$10::smallint[], $11::jsonb[]) "
        "ON CONFLICT (id) DO UPDATE SET "
        "slug = EXCLUDED.slug, name = EXCLUDED.name, "
        "website = EXCLUDED.website, logo = EXCLUDED.logo, "
        "icon = EXCLUDED.icon, logo_type = EXCLUDED.logo_type, "
        "industry = EXCLUDED.industry, "
        "employee_count_range = EXCLUDED.employee_count_range, "
        "founded_year = EXCLUDED.founded_year, "
        "extras = EXCLUDED.extras, updated_at = now()",
        [r["id"] for r in rows],
        [r["slug"] for r in rows],
        [r["name"] for r in rows],
        [r.get("website") for r in rows],
        [r.get("logo") for r in rows],
        [r.get("icon") for r in rows],
        [r.get("logo_type") for r in rows],
        [r.get("industry") for r in rows],
        [r.get("employee_count_range") for r in rows],
        [r.get("founded_year") for r in rows],
        [r.get("extras") for r in rows],
    )
    log.info("sync.companies.mirrored_to_local", count=len(rows))


async def _mirror_companies_to_supabase(
    local_conn: asyncpg.Connection,
    supa_conn: asyncpg.Connection,
) -> None:
    """Push all companies from local Postgres to Supabase.

    Local is the source of truth. Uses ON CONFLICT (slug) since Supabase
    may have rows with different UUIDs from before the migration. Updates
    the Supabase row's id to match local so all references are consistent.
    """
    rows = await local_conn.fetch(
        "SELECT id, slug, name, website, logo, icon, logo_type, "
        "industry, employee_count_range, founded_year, extras "
        "FROM company"
    )
    if not rows:
        return

    # Delete Supabase rows whose slug matches but UUID differs, then upsert
    await supa_conn.execute(
        "DELETE FROM company WHERE slug = ANY($1::text[]) AND id != ALL($2::uuid[])",
        [r["slug"] for r in rows],
        [r["id"] for r in rows],
    )

    await supa_conn.execute(
        "INSERT INTO company (id, slug, name, website, logo, icon, logo_type, "
        "industry, employee_count_range, founded_year, extras) "
        "SELECT * FROM unnest($1::uuid[], $2::text[], $3::text[], $4::text[], "
        "$5::text[], $6::text[], $7::text[], $8::smallint[], $9::smallint[], "
        "$10::smallint[], $11::jsonb[]) "
        "ON CONFLICT (id) DO UPDATE SET "
        "slug = EXCLUDED.slug, name = EXCLUDED.name, "
        "website = EXCLUDED.website, logo = EXCLUDED.logo, "
        "icon = EXCLUDED.icon, logo_type = EXCLUDED.logo_type, "
        "industry = EXCLUDED.industry, "
        "employee_count_range = EXCLUDED.employee_count_range, "
        "founded_year = EXCLUDED.founded_year, "
        "extras = EXCLUDED.extras, updated_at = now()",
        [r["id"] for r in rows],
        [r["slug"] for r in rows],
        [r["name"] for r in rows],
        [r.get("website") for r in rows],
        [r.get("logo") for r in rows],
        [r.get("icon") for r in rows],
        [r.get("logo_type") for r in rows],
        [r.get("industry") for r in rows],
        [r.get("employee_count_range") for r in rows],
        [r.get("founded_year") for r in rows],
        [r.get("extras") for r in rows],
    )
    log.info("sync.companies.mirrored_to_supabase", count=len(rows))


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

    # --- Target 1: Supabase (minimal board reference) ---
    # Realign any stale ``board_url`` before the UPSERT so that a
    # slug-stable URL rename (see comment on the SQL constant) doesn't
    # trip the ``board_slug`` unique constraint.
    await conn.execute(
        _REALIGN_RENAMED_BOARD_URLS_SUPA,
        company_slugs,
        board_slugs,
        board_urls,
    )
    await conn.execute(
        _UPSERT_BOARDS_SUPA,
        company_slugs,
        board_slugs,
        board_urls,
        crawler_types,
        metadatas,
    )
    log.info("sync.boards.upserted_supa", count=len(board_urls), skipped=skipped)

    await conn.execute(_DISABLE_REMOVED_BOARDS, board_urls)

    # --- Targets 2 & 3: local Postgres + Redis ---
    if local_conn is None:
        return

    # Fetch resolved board IDs + company IDs from Supabase
    id_rows = await conn.fetch(_FETCH_BOARD_IDS, board_urls)
    url_to_ids: dict[str, tuple] = {r["board_url"]: (r["id"], r["company_id"]) for r in id_rows}

    # Defensively drop stale local rows whose ``board_slug`` matches one
    # we're about to upsert but whose ``id`` is not the Supabase-assigned
    # one. Without this, an earlier partial sync that inserted a
    # locally-generated UUID into ``job_board`` (before the outer Supa
    # transaction later rolled back) leaves an orphan row. The next sync
    # then hits ``job_board_board_slug_key`` as a unique violation, which
    # propagates out of ``async with supa_conn.transaction()`` and rolls
    # the whole Supabase mirror back — a self-perpetuating chicken-and-
    # egg that strands every new company in local-only state. Mirrors
    # the slug/id defensive DELETE in ``_mirror_companies_to_local``.
    # ``job_posting.board_id`` uses ``ON DELETE SET NULL`` so postings
    # survive; they get re-linked by ``board_url`` when this sync
    # re-inserts the row below.
    if local_conn is not None:
        stale_slugs: list[str] = []
        stale_supa_ids: list[str] = []
        for i, board_url in enumerate(board_urls):
            slug = board_slugs[i]
            ids = url_to_ids.get(board_url)
            if slug is None or ids is None:
                continue
            stale_slugs.append(slug)
            stale_supa_ids.append(str(ids[0]))
        if stale_slugs:
            await local_conn.execute(
                "DELETE FROM job_board WHERE board_slug = ANY($1::text[]) "
                "AND id != ALL($2::uuid[])",
                stale_slugs,
                stale_supa_ids,
            )

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

    # Purge Redis monitor queue for any board that's no longer eligible to run
    # (just-disabled or previously disabled/gone). Without this, the per-domain
    # ``monitors_{wtype}:{domain}`` key retains the stale board_id and the
    # worker keeps claiming it every cycle, producing ``batch.monitor.error``
    # 404s that no CSV update can silence.
    orphan_rows = await local_conn.fetch(_FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP)
    for row in orphan_rows:
        domain = row["throttle_key"] or ""
        if not domain:
            continue
        await remove_monitor(domain, row["board_id"])

    log.info(
        "sync.boards.local_redis",
        local_upserted=local_upserted,
        redis_enqueued=redis_enqueued,
        redis_orphans_removed=len(orphan_rows),
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


async def _populate_locations_if_empty(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection,
) -> None:
    """One-time population of GeoNames data from Supabase into local Postgres.

    Local DB is the source of truth.  This only runs when the local tables
    are empty (fresh deploy).  After that, GeoNames data lives in local
    Postgres and is never overwritten from Supabase.

    Also populates ``location_macro_member`` (macro region -> member country
    mappings) idempotently. Prior to issue #2978 this table was never seeded
    on the Hetzner local Postgres, so macros never got stamped onto postings
    via ancestor expansion in the exporter. We seed it whenever it is empty
    so a fresh deploy / restored DB still gets the EU/EMEA/DACH/... links.
    """
    local_count = await local_conn.fetchval("SELECT count(*) FROM location")
    if local_count > 0:
        log.info("sync.locations.already_populated", count=local_count)
    else:
        loc_rows = await supa_conn.fetch(
            "SELECT id, parent_id, type, population, languages FROM location"
        )
        if not loc_rows:
            log.warning("sync.locations.supabase_empty")
            return

        name_rows = await supa_conn.fetch(
            "SELECT location_id, locale, name, is_display FROM location_name"
        )

        await local_conn.copy_records_to_table(
            "location",
            records=[
                (r["id"], r["parent_id"], r["type"], r["population"], r["languages"])
                for r in loc_rows
            ],
            columns=["id", "parent_id", "type", "population", "languages"],
        )

        if name_rows:
            await local_conn.copy_records_to_table(
                "location_name",
                records=[
                    (r["location_id"], r["locale"], r["name"], r["is_display"]) for r in name_rows
                ],
                columns=["location_id", "locale", "name", "is_display"],
            )

        log.info(
            "sync.locations.populated_from_supabase",
            locations=len(loc_rows),
            names=len(name_rows),
        )

    # Macro-member seed (idempotent, runs every sync). Tracked separately
    # from the location/location_name guard above because that guard
    # short-circuits when the location table was already populated (which
    # was the live state on Hetzner) — and the prior code missed seeding
    # this table entirely. See issue #2978.
    macro_count = await local_conn.fetchval("SELECT count(*) FROM location_macro_member")
    macro_rows = await supa_conn.fetch("SELECT macro_id, country_id FROM location_macro_member")
    if not macro_rows:
        log.warning("sync.location_macro_member.supabase_empty")
        return
    if macro_count == len(macro_rows):
        log.info("sync.location_macro_member.up_to_date", count=macro_count)
        return
    # Use INSERT ... ON CONFLICT DO NOTHING for idempotency. Falls back to
    # row-by-row when the table has rows already (rare drift). The set is
    # small (<1k rows), so this is cheap.
    await local_conn.executemany(
        "INSERT INTO location_macro_member (macro_id, country_id) "
        "VALUES ($1, $2) "
        "ON CONFLICT (macro_id, country_id) DO NOTHING",
        [(r["macro_id"], r["country_id"]) for r in macro_rows],
    )
    new_count = await local_conn.fetchval("SELECT count(*) FROM location_macro_member")
    log.info(
        "sync.location_macro_member.populated_from_supabase",
        before=macro_count,
        after=new_count,
        supabase=len(macro_rows),
    )


async def _populate_currency_rates_if_empty(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection,
) -> None:
    """One-time population of currency rates from Supabase into local Postgres.

    Local DB is the source of truth.  After initial population, rates
    should be refreshed by a local script (e.g. ECB daily feed).
    """
    local_count = await local_conn.fetchval("SELECT count(*) FROM currency_rate")
    if local_count > 0:
        log.info("sync.currency_rates.already_populated", count=local_count)
        return

    rows = await supa_conn.fetch("SELECT currency, to_eur, updated_at FROM currency_rate")
    if not rows:
        return

    await local_conn.copy_records_to_table(
        "currency_rate",
        records=[(r["currency"], r["to_eur"], r["updated_at"]) for r in rows],
        columns=["currency", "to_eur", "updated_at"],
    )
    log.info("sync.currency_rates.populated_from_supabase", count=len(rows))


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

    # --- GeoNames + currency: one-time population from Supabase if empty ---
    await _populate_locations_if_empty(supa_conn, local_conn)
    await _populate_currency_rates_if_empty(supa_conn, local_conn)

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


# ---------------------------------------------------------------------------
# Typesense helpers
# ---------------------------------------------------------------------------

_TYPESENSE_BATCH_SIZE = 1000


def _ts_bulk_upsert(
    client: typesense.Client,
    collection: str,
    docs: list[dict],
    action: str = "upsert",
) -> None:
    """Bulk write documents to a Typesense collection.

    ``action`` is a Typesense import action:
    - ``"upsert"`` (default): replaces each doc; requires all non-optional fields
    - ``"update"``: partial merge into an existing doc; 404s if the doc doesn't exist
    - ``"emplace"``: partial merge if the doc exists, otherwise creates it

    Splits into batches of ``_TYPESENSE_BATCH_SIZE``. Logs errors but does
    not raise — Typesense writes are fire-and-forget.
    """
    if not docs:
        return
    for i in range(0, len(docs), _TYPESENSE_BATCH_SIZE):
        batch = docs[i : i + _TYPESENSE_BATCH_SIZE]
        results = client.collections[collection].documents.import_(batch, {"action": action})
        errors = [r for r in results if not r.get("success", True)]
        if errors:
            log.warning(
                "typesense.bulk_upsert.errors",
                collection=collection,
                action=action,
                error_count=len(errors),
                sample=errors[:3],
            )
    log.info(
        "typesense.bulk_upsert.done",
        collection=collection,
        action=action,
        doc_count=len(docs),
    )


def _ts_bulk_delete_ids(
    client: typesense.Client,
    collection: str,
    ids: list[str],
) -> None:
    """Delete documents by id from a Typesense collection.

    Iterates per-id (cheap at the scale this is used — excluding trivial
    watchlists). 404s are expected for ids that were never indexed.
    """
    if not ids:
        return
    deleted = 0
    for doc_id in ids:
        try:
            client.collections[collection].documents[doc_id].delete()
            deleted += 1
        except ObjectNotFound:
            # Doc may never have been indexed — that's the whole point.
            pass
        except Exception as exc:
            log.warning(
                "typesense.delete.error",
                collection=collection,
                doc_id=doc_id,
                error=str(exc),
            )
    log.info(
        "typesense.delete.done",
        collection=collection,
        requested=len(ids),
        deleted=deleted,
    )


# ---------------------------------------------------------------------------
# Typesense taxonomy sync
# ---------------------------------------------------------------------------


# Natural-language synonyms for macro-region location rows, keyed by
# slug. Populated onto Typesense ``location.aliases`` so the autocomplete's
# prefix search (``query_by=name_en,aliases``) surfaces e.g. the EU row
# when the user types "Europe" or "European Union" — the canonical
# ``name_en`` for these rows is just the abbreviation, which has no
# user-typeable prefix overlap with the obvious natural-language phrases.
# Hard-coded rather than column-driven because the macro set is small,
# stable, and the alias choices are an editorial decision (see #2939).
_LOCATION_MACRO_ALIASES: dict[str, list[str]] = {
    "eu": ["European Union", "Europe", "EEA", "Schengen"],
    "emea": [
        "Europe Middle East Africa",
        "Europe & Middle East",
        "EMEA region",
    ],
    "dach": [
        "D-A-CH",
        "German-speaking countries",
        "Germany Austria Switzerland",
    ],
    "apac": [
        "Asia Pacific",
        "Asia-Pacific",
        "Asia and the Pacific",
    ],
    "americas": [
        "North America",
        "South America",
        "Western Hemisphere",
    ],
    "latam": [
        "Latin America",
        "South America",
        "Central America",
    ],
    "nordics": [
        "Nordic countries",
        "Scandinavia",
        "Northern Europe",
    ],
    "mena": [
        "Middle East and North Africa",
        "Middle East North Africa",
        "Arab world",
    ],
    "worldwide": [
        "Global",
        "Anywhere",
        "Remote",
        "International",
    ],
}


async def sync_locations_typesense(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection | None,
    client: typesense.Client,
) -> None:
    """Sync locations to the Typesense ``location`` collection.

    Queries Supabase for location data (lat/lng/slug) and local Postgres
    for active posting counts.
    """
    rows = await supa_conn.fetch(
        """
        SELECT l.id, l.type, l.lat, l.lng, l.slug, l.population,
               pn.name AS parent_name
        FROM location l
        LEFT JOIN location parent ON parent.id = l.parent_id
        LEFT JOIN LATERAL (
            SELECT ln.name
            FROM location_name ln
            WHERE ln.location_id = parent.id AND ln.locale = 'en' AND ln.is_display
            LIMIT 1
        ) pn ON true
        """
    )
    if not rows:
        log.info("typesense.locations.empty")
        return

    # Fetch locale names from Supabase
    name_rows = await supa_conn.fetch(
        "SELECT location_id, locale, name FROM location_name WHERE is_display"
    )
    names_by_id: dict[int, dict[str, str]] = {}
    for nr in name_rows:
        names_by_id.setdefault(nr["location_id"], {})[nr["locale"]] = nr["name"]

    # Count active postings per location. We read the count from the
    # Typesense ``job_posting`` facet on ``location_ids`` (post ancestor
    # expansion in ``exporter._build_typesense_docs``) so country / region
    # / macro counts include their descendants — matching what filtering
    # by that id returns. Reading ``unnest(location_ids)`` from local
    # Postgres returned leaf-only counts and silently diverged from
    # filter results (issue #2978).
    counts: dict[int, int] = {}
    loop = asyncio.get_event_loop()
    try:
        facet_counts = await loop.run_in_executor(
            None, _fetch_active_facet_counts, client, "location_ids"
        )
        counts = {int(k): v for k, v in facet_counts.items()}
    except Exception as exc:
        # First-time bootstrap: job_posting collection / index may not
        # exist yet. Fall back to leaf-only Postgres counts so locations
        # still get *some* count rather than zeros.
        log.warning("typesense.locations.facet_unavailable", error=str(exc))
        if local_conn is not None:
            count_rows = await local_conn.fetch(
                """
                SELECT unnest(location_ids) AS loc_id, COUNT(*) AS cnt
                FROM job_posting
                WHERE is_active
                GROUP BY 1
                """
            )
            counts = {r["loc_id"]: r["cnt"] for r in count_rows}

    docs: list[dict] = []
    for r in rows:
        loc_id = r["id"]
        loc_names = names_by_id.get(loc_id, {})
        count = counts.get(loc_id, 0)

        doc: dict = {
            "id": str(loc_id),
            "location_id": loc_id,
            "slug": r["slug"] or "",
            "name_en": loc_names.get("en", ""),
            "type": r["type"] or "city",
            "has_active_postings": count > 0,
            "active_posting_count": count,
        }
        # Optional fields
        if loc_names.get("de"):
            doc["name_de"] = loc_names["de"]
        if loc_names.get("fr"):
            doc["name_fr"] = loc_names["fr"]
        if loc_names.get("it"):
            doc["name_it"] = loc_names["it"]
        if r["lat"] is not None and r["lng"] is not None:
            doc["coordinates"] = [float(r["lat"]), float(r["lng"])]
        if r["parent_name"]:
            doc["parent_name"] = r["parent_name"]
        if r["population"] is not None:
            doc["population"] = r["population"]
        # Macro-region aliases (#2939): natural-language synonyms so
        # "Europe" / "European Union" / "DACH" / "Asia Pacific" / etc.
        # match the macro row whose ``name_en`` is just the abbreviation.
        if r["type"] == "macro" and r["slug"]:
            aliases = _LOCATION_MACRO_ALIASES.get(r["slug"])
            if aliases:
                doc["aliases"] = aliases

        docs.append(doc)

    await loop.run_in_executor(None, _ts_bulk_upsert, client, "location", docs)
    log.info("typesense.locations.synced", count=len(docs))


async def sync_occupations_typesense(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection | None,
    client: typesense.Client,
) -> None:
    """Sync occupations to the Typesense ``occupation`` collection.

    One document per (occupation, locale) pair.
    """
    rows = await supa_conn.fetch(
        """
        SELECT o.id, o.slug,
               on2.locale, on2.name, on2.is_display,
               d.slug AS domain_slug
        FROM occupation o
        JOIN occupation_name on2 ON on2.occupation_id = o.id
        LEFT JOIN occupation_domain d ON d.id = o.domain_id
        ORDER BY o.id, on2.locale
        """
    )
    if not rows:
        log.info("typesense.occupations.empty")
        return

    # Fetch domain display names
    domain_name_rows = await supa_conn.fetch(
        "SELECT domain_id, locale, name FROM occupation_domain_name WHERE is_display"
    )
    domain_names: dict[int, dict[str, str]] = {}
    for dr in domain_name_rows:
        domain_names.setdefault(dr["domain_id"], {})[dr["locale"]] = dr["name"]

    # Domain slug -> id mapping
    domain_rows = await supa_conn.fetch("SELECT id, slug FROM occupation_domain")
    domain_slug_to_id = {r["slug"]: r["id"] for r in domain_rows}

    # Active posting counts from local Postgres
    # Counts come from the Typesense ``job_posting`` facet on
    # ``occupation_ids`` (post ancestor expansion in
    # ``exporter._build_typesense_docs``) so a parent occupation's count
    # includes all descendants — matching what filtering by it returns.
    # Reading ``occupation_id`` from local Postgres was leaf-only
    # (issue #2978).
    counts: dict[int, int] = {}
    loop = asyncio.get_event_loop()
    try:
        facet_counts = await loop.run_in_executor(
            None, _fetch_active_facet_counts, client, "occupation_ids"
        )
        counts = {int(k): v for k, v in facet_counts.items()}
    except Exception as exc:
        log.warning("typesense.occupations.facet_unavailable", error=str(exc))
        if local_conn is not None:
            count_rows = await local_conn.fetch(
                """
                SELECT occupation_id, COUNT(*) AS cnt
                FROM job_posting
                WHERE is_active AND occupation_id IS NOT NULL
                GROUP BY 1
                """
            )
            counts = {r["occupation_id"]: r["cnt"] for r in count_rows}

    # Group by (occupation_id, locale)
    # display names vs aliases
    occ_data: dict[tuple[int, str], dict] = {}
    for r in rows:
        occ_id = r["id"]
        locale = r["locale"]
        key = (occ_id, locale)

        if key not in occ_data:
            domain_id = domain_slug_to_id.get(r["domain_slug"]) if r["domain_slug"] else None
            domain_name_map = domain_names.get(domain_id, {}) if domain_id else {}
            occ_data[key] = {
                "occ_id": occ_id,
                "slug": r["slug"],
                "locale": locale,
                "name": None,
                "aliases": [],
                "domain_name": domain_name_map.get(locale) or domain_name_map.get("en"),
            }

        if r["is_display"]:
            occ_data[key]["name"] = r["name"]
        else:
            occ_data[key]["aliases"].append(r["name"])

    # Also include wildcard aliases (locale='*') for all real locales
    wildcard_aliases: dict[int, list[str]] = {}
    for r in rows:
        if r["locale"] == "*":
            wildcard_aliases.setdefault(r["id"], []).append(r["name"])

    docs: list[dict] = []
    for (occ_id, locale), data in occ_data.items():
        if locale == "*":
            continue  # Skip wildcard-only entries
        if not data["name"]:
            continue  # Skip occupations without a display name for this locale

        count = counts.get(occ_id, 0)
        aliases = data["aliases"] + wildcard_aliases.get(occ_id, [])

        doc: dict = {
            "id": f"{occ_id}-{locale}",
            "occupation_id": occ_id,
            "slug": data["slug"],
            "name": data["name"],
            "aliases": aliases,
            "locale": locale,
            "has_active_postings": count > 0,
            "active_posting_count": count,
        }
        if data["domain_name"]:
            doc["domain_name"] = data["domain_name"]

        docs.append(doc)

    await loop.run_in_executor(None, _ts_bulk_upsert, client, "occupation", docs)
    log.info("typesense.occupations.synced", count=len(docs))


async def sync_seniority_typesense(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection | None,
    client: typesense.Client,
) -> None:
    """Sync seniorities to the Typesense ``seniority`` collection.

    One document per (seniority, locale) pair.
    """
    rows = await supa_conn.fetch(
        """
        SELECT s.id, s.slug,
               sn.locale, sn.name, sn.is_display
        FROM seniority s
        JOIN seniority_name sn ON sn.seniority_id = s.id
        ORDER BY s.id, sn.locale
        """
    )

    if not rows:
        log.info("typesense.seniority.empty")
        return

    # Active posting counts from local Postgres
    counts: dict[int, int] = {}
    if local_conn is not None:
        count_rows = await local_conn.fetch(
            """
            SELECT seniority_id, COUNT(*) AS cnt
            FROM job_posting
            WHERE is_active AND seniority_id IS NOT NULL
            GROUP BY 1
            """
        )
        counts = {r["seniority_id"]: r["cnt"] for r in count_rows}

    # Group by (seniority_id, locale)
    sen_data: dict[tuple[int, str], dict] = {}
    for r in rows:
        sen_id = r["id"]
        locale = r["locale"]
        key = (sen_id, locale)

        if key not in sen_data:
            sen_data[key] = {
                "sen_id": sen_id,
                "slug": r["slug"],
                "locale": locale,
                "name": None,
                "aliases": [],
            }

        if r["is_display"]:
            sen_data[key]["name"] = r["name"]
        else:
            sen_data[key]["aliases"].append(r["name"])

    # Wildcard aliases
    wildcard_aliases: dict[int, list[str]] = {}
    for r in rows:
        if r["locale"] == "*":
            wildcard_aliases.setdefault(r["id"], []).append(r["name"])

    docs: list[dict] = []
    for (sen_id, locale), data in sen_data.items():
        if locale == "*":
            continue
        if not data["name"]:
            continue

        count = counts.get(sen_id, 0)
        aliases = data["aliases"] + wildcard_aliases.get(sen_id, [])

        doc: dict = {
            "id": f"{sen_id}-{locale}",
            "seniority_id": sen_id,
            "slug": data["slug"],
            "name": data["name"],
            "aliases": aliases,
            "locale": locale,
            "has_active_postings": count > 0,
            "active_posting_count": count,
        }
        docs.append(doc)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _ts_bulk_upsert, client, "seniority", docs)
    log.info("typesense.seniority.synced", count=len(docs))


async def sync_technologies_typesense(
    local_conn: asyncpg.Connection | None,
    client: typesense.Client,
) -> None:
    """Sync technologies to the Typesense ``technology`` collection.

    One document per technology. Queries local Postgres for both
    technology data and active posting counts.
    """
    if local_conn is None:
        log.info("typesense.technologies.no_local_conn")
        return

    tech_rows = await local_conn.fetch("SELECT id, slug, name, category FROM technology")
    if not tech_rows:
        log.info("typesense.technologies.empty")
        return

    # Active posting counts
    count_rows = await local_conn.fetch(
        """
        SELECT unnest(technology_ids) AS tech_id, COUNT(*) AS cnt
        FROM job_posting
        WHERE is_active
        GROUP BY 1
        """
    )
    counts = {r["tech_id"]: r["cnt"] for r in count_rows}

    docs: list[dict] = []
    for r in tech_rows:
        tech_id = r["id"]
        count = counts.get(tech_id, 0)
        doc: dict = {
            "id": str(tech_id),
            "technology_id": tech_id,
            "slug": r["slug"],
            "name": r["name"] or r["slug"],
            "has_active_postings": count > 0,
            "active_posting_count": count,
        }
        if r["category"]:
            doc["category"] = r["category"]
        docs.append(doc)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _ts_bulk_upsert, client, "technology", docs)
    log.info("typesense.technologies.synced", count=len(docs))


# ---------------------------------------------------------------------------
# Typesense company sync
# ---------------------------------------------------------------------------


async def sync_companies_typesense(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection | None,
    client: typesense.Client,
) -> None:
    """Sync companies to the Typesense ``company`` collection.

    Populates the per-locale description / industry_name variants used by the
    company detail page reader (``getCompanyBySlug``) so that page can serve
    from Typesense without a Supabase round-trip.
    """
    rows = await supa_conn.fetch(
        """
        SELECT c.id, c.name, c.slug, c.icon, c.logo, c.website,
               c.description, c.industry,
               c.employee_count_range, c.founded_year,
               i.name AS industry_name
        FROM company c
        LEFT JOIN industry i ON i.id = c.industry
        """
    )
    if not rows:
        log.info("typesense.companies.empty")
        return

    desc_rows = await supa_conn.fetch(
        "SELECT company_id, locale, description FROM company_description"
    )
    descs_by_locale: dict[str, dict] = {}
    for r in desc_rows:
        descs_by_locale.setdefault(r["locale"], {})[r["company_id"]] = r["description"]

    ind_name_rows = await supa_conn.fetch(
        "SELECT industry_id, locale, name FROM industry_name WHERE is_display"
    )
    ind_names_by_locale: dict[str, dict] = {}
    for r in ind_name_rows:
        ind_names_by_locale.setdefault(r["locale"], {})[r["industry_id"]] = r["name"]

    active_counts: dict[str, int] = {}
    year_counts: dict[str, int] = {}
    if local_conn is not None:
        active_rows = await local_conn.fetch(
            """
            SELECT company_id::text, COUNT(*) AS cnt
            FROM job_posting
            WHERE is_active
            GROUP BY 1
            """
        )
        active_counts = {r["company_id"]: r["cnt"] for r in active_rows}

        year_rows = await local_conn.fetch(
            """
            SELECT company_id::text, COUNT(*) AS cnt
            FROM job_posting
            WHERE first_seen_at > now() - interval '1 year'
            GROUP BY 1
            """
        )
        year_counts = {r["company_id"]: r["cnt"] for r in year_rows}

    docs: list[dict] = []
    for r in rows:
        company_id = str(r["id"])
        doc: dict = {
            "id": company_id,
            "name": r["name"],
            "slug": r["slug"],
            "active_posting_count": active_counts.get(company_id, 0),
            "year_posting_count": year_counts.get(company_id, 0),
        }
        if r["icon"]:
            doc["icon"] = r["icon"]
        if r["logo"]:
            doc["logo"] = r["logo"]
        if r["website"]:
            doc["website"] = r["website"]
        if r["employee_count_range"] is not None:
            doc["employee_count_range"] = r["employee_count_range"]
        if r["founded_year"] is not None:
            doc["founded_year"] = r["founded_year"]

        # description: per-locale company_description takes precedence over
        # the canonical c.description (English) for matching locale.
        en_desc = descs_by_locale.get("en", {}).get(r["id"]) or r["description"]
        if en_desc:
            doc["description"] = en_desc
        for loc in ("de", "fr", "it"):
            text = descs_by_locale.get(loc, {}).get(r["id"])
            if text:
                doc[f"description_{loc}"] = text

        if r["industry"] is not None:
            doc["industry_id"] = r["industry"]
        if r["industry_name"]:
            doc["industry_name"] = r["industry_name"]
        for loc in ("de", "fr", "it"):
            name = ind_names_by_locale.get(loc, {}).get(r["industry"])
            if name:
                doc[f"industry_name_{loc}"] = name

        docs.append(doc)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _ts_bulk_upsert, client, "company", docs)
    log.info("typesense.companies.synced", count=len(docs))


# ---------------------------------------------------------------------------
# Typesense watchlist sync
# ---------------------------------------------------------------------------


def _is_trivial_watchlist(filters: dict | None, company_count: int) -> bool:
    """Mirror of the web app's ``isTrivialWatchlist``.

    A watchlist is "trivial" when it tracks no companies and carries no
    meaningful filters — effectively a blank shell. We exclude these from
    the public ``watchlist`` collection so they don't dilute search/popular
    listings. ``anyCompany`` and ``salaryCurrency`` alone don't count
    (they're defaults/prefs). Keep in sync with
    ``apps/web/src/lib/watchlist-utils.ts``.
    """
    if company_count > 0:
        return False
    f = filters or {}
    return not (
        f.get("keywords")
        or f.get("locationSlugs")
        or f.get("occupationSlugs")
        or f.get("senioritySlugs")
        or f.get("technologySlugs")
        or f.get("salaryMin") is not None
        or f.get("salaryMax") is not None
        or f.get("experienceMin") is not None
        or f.get("experienceMax") is not None
    )


async def sync_watchlists_typesense(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection | None,
    client: typesense.Client,
) -> None:
    """Sync public watchlists to the Typesense ``watchlist`` collection.

    Watchlists only exist in Supabase, so metadata and ``watchlist_company``
    pairs come from there. The active-posting count per company is computed
    against local Postgres (the job_posting source of truth) and aggregated
    per watchlist in Python; company UUIDs are identical across both DBs
    (see ``_mirror_companies_*``) so the counts match. This avoids a
    watchlist_company ⨝ job_posting WHERE is_active hash join on Supabase,
    which dominated Supabase compute spend. Falls back to the Supabase JOIN
    when ``local_conn`` is None (dry-run, local Postgres unreachable).

    Trivial watchlists (no companies, no meaningful filters) are deleted
    from Typesense rather than upserted.
    """
    rows = await supa_conn.fetch(
        """
        SELECT w.id, w.slug, w.title, w.description,
               w.is_public, w.created_at, w.filters,
               u.name AS owner_name, u.username AS owner_username
        FROM watchlist w
        JOIN "user" u ON u.id = w.user_id
        WHERE w.is_public = true
        """
    )
    if not rows:
        log.info("typesense.watchlists.empty")
        return

    watchlist_ids = [r["id"] for r in rows]

    wc_pairs = await supa_conn.fetch(
        """
        SELECT watchlist_id, company_id
        FROM watchlist_company
        WHERE watchlist_id = ANY($1::uuid[])
        """,
        watchlist_ids,
    )
    company_counts: dict[str, int] = defaultdict(int)
    for r in wc_pairs:
        company_counts[str(r["watchlist_id"])] += 1

    job_counts: dict[str, int]
    if local_conn is not None:
        distinct_company_ids = list({r["company_id"] for r in wc_pairs})
        per_company: dict = {}
        if distinct_company_ids:
            active_rows = await local_conn.fetch(
                """
                SELECT company_id, COUNT(*) AS cnt
                FROM job_posting
                WHERE is_active AND company_id = ANY($1::uuid[])
                GROUP BY 1
                """,
                distinct_company_ids,
            )
            per_company = {r["company_id"]: r["cnt"] for r in active_rows}
        job_counts = defaultdict(int)
        for r in wc_pairs:
            job_counts[str(r["watchlist_id"])] += per_company.get(r["company_id"], 0)
    else:
        job_count_rows = await supa_conn.fetch(
            """
            SELECT wc.watchlist_id, COUNT(jp.id) AS cnt
            FROM watchlist_company wc
            JOIN job_posting jp ON jp.company_id = wc.company_id AND jp.is_active
            WHERE wc.watchlist_id = ANY($1::uuid[])
            GROUP BY 1
            """,
            watchlist_ids,
        )
        job_counts = {str(r["watchlist_id"]): r["cnt"] for r in job_count_rows}

    # Mirror counts
    mirror_count_rows = await supa_conn.fetch(
        """
        SELECT source_watchlist_id, COUNT(*) AS cnt
        FROM watchlist
        WHERE source_watchlist_id = ANY($1::uuid[])
        GROUP BY 1
        """,
        watchlist_ids,
    )
    mirror_counts = {str(r["source_watchlist_id"]): r["cnt"] for r in mirror_count_rows}

    docs: list[dict] = []
    trivial_ids: list[str] = []
    for r in rows:
        wid = str(r["id"])
        created_ts = int(r["created_at"].timestamp()) if r["created_at"] else 0
        company_count = company_counts.get(wid, 0)

        raw_filters = r["filters"]
        filters: dict | None
        if isinstance(raw_filters, str):
            try:
                filters = json.loads(raw_filters)
            except (ValueError, TypeError):
                filters = None
        else:
            filters = raw_filters

        if _is_trivial_watchlist(filters, company_count):
            trivial_ids.append(wid)
            continue

        doc: dict = {
            "id": wid,
            "slug": r["slug"] or "",
            "title": r["title"] or "",
            "owner_name": r["owner_name"] or "",
            "company_count": company_count,
            "active_job_count": job_counts.get(wid, 0),
            "mirror_count": mirror_counts.get(wid, 0),
            "is_featured": (r["owner_username"] or "").lower() == "colophongroup",
            "has_description": bool(r["description"]),
            "created_at": created_ts,
            "is_public": True,
        }
        if r["description"]:
            doc["description"] = r["description"]
        if r["owner_username"]:
            doc["owner_username"] = r["owner_username"]
        docs.append(doc)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _ts_bulk_upsert, client, "watchlist", docs)
    # Drop any trivial watchlists that were previously indexed (e.g. pre-#2177
    # or a web-side write hook that got skipped). This only touches Typesense;
    # the rows still exist in Postgres for their owner.
    await loop.run_in_executor(None, _ts_bulk_delete_ids, client, "watchlist", trivial_ids)
    log.info(
        "typesense.watchlists.synced",
        upserted=len(docs),
        trivial_deleted=len(trivial_ids),
    )


# ---------------------------------------------------------------------------
# refresh_typesense_counts
# ---------------------------------------------------------------------------


# Cap for the location/occupation facet count refresh. Typesense returns at
# most this many distinct ids per facet field; we set it well above the
# total number of unique ancestor-expanded ids that ever appear in
# job_posting.location_ids (~11k as of 2026-05) so the count refresh
# covers every taxonomy id with at least one posting. Higher values are
# safe — Typesense streams the facet aggregation, memory is the only
# constraint, and at this scale it's a sub-second query.
_TS_FACET_REFRESH_MAX = 100000


def _fetch_active_facet_counts(
    client: typesense.Client,
    field: str,
) -> dict[str, int]:
    """Read active-posting facet counts from the Typesense ``job_posting``
    collection.

    Returns ``{facet_value: count}`` for every distinct value of ``field``
    among active postings. ``field`` is the Typesense facet field name —
    ``location_ids`` and ``occupation_ids`` are *post* ancestor expansion
    in the indexer (``exporter._build_typesense_docs``), so the resulting
    counts include city -> country -> macro fan-in. This is the count the
    user sees when clicking the facet in the UI; counting from local
    Postgres ``unnest(location_ids)`` is leaf-only and silently diverges
    from filter results (issue #2978).

    Synchronous — designed to be called from
    ``loop.run_in_executor(None, _fetch_active_facet_counts, ...)``.
    """
    resp = client.collections["job_posting"].documents.search(
        {
            "q": "*",
            "query_by": "title",
            "filter_by": "is_active:true",
            "facet_by": field,
            "max_facet_values": _TS_FACET_REFRESH_MAX,
            "facet_strategy": "exhaustive",
            "per_page": 0,
        }
    )
    facets = resp.get("facet_counts", []) or []
    if not facets:
        return {}
    counts = facets[0].get("counts", []) or []
    return {fc["value"]: fc["count"] for fc in counts}


async def refresh_typesense_counts(
    local_conn: asyncpg.Connection,
    client: typesense.Client,
) -> None:
    """Refresh active_posting_count on all taxonomy and company collections.

    Idempotent — can be called after each sync run or on a timer.
    Counts are approximate.

    Location and occupation counts are read from the Typesense ``job_posting``
    facet (post ancestor expansion) so the count an operator sees on a
    location/occupation card matches the count they get when they filter by
    it. Reading ``unnest(location_ids)`` from local Postgres returned only
    leaf ids and silently diverged from filter results (issue #2978).
    """
    loop = asyncio.get_event_loop()

    # Count refresh uses action="update" (partial merge). The taxonomy and
    # company docs are fully written by sync_*_typesense; here we only touch
    # the *_posting_count fields, so we must not require the schema's other
    # non-optional fields like `name`. See issue #2622.

    # --- Locations (read from Typesense facet — see #2978) ---
    loc_facet = await loop.run_in_executor(None, _fetch_active_facet_counts, client, "location_ids")
    if loc_facet:
        loc_docs = [
            {
                "id": str(loc_id),
                "active_posting_count": cnt,
                "has_active_postings": True,
            }
            for loc_id, cnt in loc_facet.items()
        ]
        await loop.run_in_executor(None, _ts_bulk_upsert, client, "location", loc_docs, "update")

    # --- Occupations (read from Typesense facet on `occupation_ids` —
    # which carries the leaf occupation + its ancestors in
    # exporter._build_typesense_docs) ---
    occ_facet = await loop.run_in_executor(
        None, _fetch_active_facet_counts, client, "occupation_ids"
    )
    if occ_facet:
        # Update all locale variants
        occ_docs: list[dict] = []
        for occ_id, cnt in occ_facet.items():
            for locale in ("en", "de", "fr", "it"):
                occ_docs.append(
                    {
                        "id": f"{occ_id}-{locale}",
                        "active_posting_count": cnt,
                        "has_active_postings": True,
                    }
                )
        await loop.run_in_executor(None, _ts_bulk_upsert, client, "occupation", occ_docs, "update")

    # --- Seniorities ---
    sen_rows = await local_conn.fetch(
        """
        SELECT seniority_id, COUNT(*) AS cnt
        FROM job_posting WHERE is_active AND seniority_id IS NOT NULL GROUP BY 1
        """
    )
    if sen_rows:
        sen_docs: list[dict] = []
        for r in sen_rows:
            for locale in ("en", "de", "fr", "it"):
                sen_docs.append(
                    {
                        "id": f"{r['seniority_id']}-{locale}",
                        "active_posting_count": r["cnt"],
                        "has_active_postings": True,
                    }
                )
        await loop.run_in_executor(None, _ts_bulk_upsert, client, "seniority", sen_docs, "update")

    # --- Technologies ---
    tech_rows = await local_conn.fetch(
        """
        SELECT unnest(technology_ids) AS tech_id, COUNT(*) AS cnt
        FROM job_posting WHERE is_active GROUP BY 1
        """
    )
    if tech_rows:
        tech_docs = [
            {
                "id": str(r["tech_id"]),
                "active_posting_count": r["cnt"],
                "has_active_postings": True,
            }
            for r in tech_rows
        ]
        await loop.run_in_executor(None, _ts_bulk_upsert, client, "technology", tech_docs, "update")

    # --- Companies ---
    # The web reads `company.active_posting_count` directly (unfiltered
    # `listTopCompanies` path) but the live filtered path facets
    # `job_posting` with `is_active:true && has_content:!=false`
    # (POSTING_BASE_FILTER, see apps/web/src/lib/search/typesense-filters.ts).
    # If we count without `has_content` here, the precomputed and live
    # numbers structurally diverge — issue #3009 (McDonald's: 55,591 vs
    # 44,161 on 2026-05-10).
    #
    # `has_content` is computed in `exporter._build_typesense_docs` as
    # `bool(title and title.strip()) and (description_r2_hash is not None)`,
    # where `title = titles[0] if titles else ""`. The SQL predicate
    # mirrors that formula so the precomputed counts equal the live
    # facet counts modulo locale filtering (which is the user's
    # deliberate choice).
    _HAS_CONTENT = (
        "description_r2_hash IS NOT NULL "
        "AND cardinality(titles) > 0 "
        "AND length(trim(titles[1])) > 0"
    )
    active_rows = await local_conn.fetch(
        f"""
        SELECT company_id::text, COUNT(*) AS cnt
        FROM job_posting
        WHERE is_active AND {_HAS_CONTENT}
        GROUP BY 1
        """
    )
    year_rows = await local_conn.fetch(
        f"""
        SELECT company_id::text, COUNT(*) AS cnt
        FROM job_posting
        WHERE first_seen_at > now() - interval '1 year'
          AND {_HAS_CONTENT}
        GROUP BY 1
        """
    )
    if active_rows or year_rows:
        active_map = {r["company_id"]: r["cnt"] for r in active_rows}
        year_map = {r["company_id"]: r["cnt"] for r in year_rows}
        all_ids = set(active_map) | set(year_map)
        company_docs = [
            {
                "id": cid,
                "active_posting_count": active_map.get(cid, 0),
                "year_posting_count": year_map.get(cid, 0),
            }
            for cid in all_ids
        ]
        await loop.run_in_executor(None, _ts_bulk_upsert, client, "company", company_docs, "update")

    log.info("typesense.refresh_counts.done")


# ---------------------------------------------------------------------------
# Taxonomy rename detection
# ---------------------------------------------------------------------------


async def _snapshot_name_maps(
    supa_conn: asyncpg.Connection,
) -> dict[str, dict[int, str]]:
    """Snapshot current display names for rename detection.

    Returns a dict keyed by taxonomy type with {id: display_name_en} maps.
    """
    occ_rows = await supa_conn.fetch(
        """
        SELECT o.id, on2.name
        FROM occupation o
        JOIN occupation_name on2 ON on2.occupation_id = o.id
        WHERE on2.is_display AND on2.locale = 'en'
        """
    )
    sen_rows = await supa_conn.fetch(
        """
        SELECT s.id, sn.name
        FROM seniority s
        JOIN seniority_name sn ON sn.seniority_id = s.id
        WHERE sn.is_display AND sn.locale = 'en'
        """
    )
    tech_rows = await supa_conn.fetch("SELECT id, name FROM technology")

    return {
        "occupation": {r["id"]: r["name"] for r in occ_rows},
        "seniority": {r["id"]: r["name"] for r in sen_rows},
        "technology": {r["id"]: r["name"] for r in tech_rows},
    }


async def _apply_taxonomy_renames(
    before: dict[str, dict[int, str]],
    after: dict[str, dict[int, str]],
    local_conn: asyncpg.Connection,
    client: typesense.Client,
) -> None:
    """Detect taxonomy renames and update affected job_posting docs in Typesense."""
    loop = asyncio.get_event_loop()

    # Occupation renames
    for occ_id, new_name in after.get("occupation", {}).items():
        old_name = before.get("occupation", {}).get(occ_id)
        if old_name and old_name != new_name:
            log.info(
                "typesense.rename.occupation",
                id=occ_id,
                old=old_name,
                new=new_name,
            )
            posting_rows = await local_conn.fetch(
                "SELECT id FROM job_posting WHERE occupation_id = $1", occ_id
            )
            if posting_rows:
                docs = [{"id": str(r["id"]), "occupation_name": new_name} for r in posting_rows]
                await loop.run_in_executor(
                    None, _ts_bulk_upsert, client, "job_posting", docs, "update"
                )

    # Seniority renames
    for sen_id, new_name in after.get("seniority", {}).items():
        old_name = before.get("seniority", {}).get(sen_id)
        if old_name and old_name != new_name:
            log.info(
                "typesense.rename.seniority",
                id=sen_id,
                old=old_name,
                new=new_name,
            )
            posting_rows = await local_conn.fetch(
                "SELECT id FROM job_posting WHERE seniority_id = $1", sen_id
            )
            if posting_rows:
                docs = [{"id": str(r["id"]), "seniority_name": new_name} for r in posting_rows]
                await loop.run_in_executor(
                    None, _ts_bulk_upsert, client, "job_posting", docs, "update"
                )

    # Technology renames
    for tech_id, new_name in after.get("technology", {}).items():
        old_name = before.get("technology", {}).get(tech_id)
        if old_name and old_name != new_name:
            log.info(
                "typesense.rename.technology",
                id=tech_id,
                old=old_name,
                new=new_name,
            )
            posting_rows = await local_conn.fetch(
                "SELECT id FROM job_posting WHERE technology_ids @> ARRAY[$1]",
                tech_id,
            )
            if posting_rows:
                # Need to rebuild the full technology_names array for each posting
                for pr in posting_rows:
                    posting = await local_conn.fetchrow(
                        "SELECT technology_ids FROM job_posting WHERE id = $1",
                        pr["id"],
                    )
                    if posting and posting["technology_ids"]:
                        tech_names = []
                        for tid in posting["technology_ids"]:
                            name = after["technology"].get(tid)
                            if name:
                                tech_names.append(name)
                        if tech_names:
                            await loop.run_in_executor(
                                None,
                                _ts_bulk_upsert,
                                client,
                                "job_posting",
                                [{"id": str(pr["id"]), "technology_names": tech_names}],
                                "update",
                            )


# ---------------------------------------------------------------------------
# Typesense sync orchestrator
# ---------------------------------------------------------------------------


async def sync_typesense(
    supa_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection | None,
    client: typesense.Client,
) -> None:
    """Sync all taxonomy, company, and watchlist data to Typesense.

    Called outside the Supabase transaction. Failures are logged but do not
    break the sync pipeline.
    """
    try:
        await sync_locations_typesense(supa_conn, local_conn, client)
    except Exception:
        log.exception("typesense.sync.locations.failed")

    try:
        await sync_occupations_typesense(supa_conn, local_conn, client)
    except Exception:
        log.exception("typesense.sync.occupations.failed")

    try:
        await sync_seniority_typesense(supa_conn, local_conn, client)
    except Exception:
        log.exception("typesense.sync.seniority.failed")

    try:
        await sync_technologies_typesense(local_conn, client)
    except Exception:
        log.exception("typesense.sync.technologies.failed")

    try:
        await sync_companies_typesense(supa_conn, local_conn, client)
    except Exception:
        log.exception("typesense.sync.companies.failed")

    try:
        await sync_watchlists_typesense(supa_conn, local_conn, client)
    except Exception:
        log.exception("typesense.sync.watchlists.failed")

    # Refresh posting counts
    if local_conn is not None:
        try:
            await refresh_typesense_counts(local_conn, client)
        except Exception:
            log.exception("typesense.sync.refresh_counts.failed")

    # Bust the web app's typeahead suggest caches so renamed / added /
    # removed taxonomy entries are reflected in autocomplete within
    # seconds, not the 1h TTL window. No-op + logs when the env vars
    # aren't set (e.g. local dev).
    try:
        from src.notify_invalidate import notify_invalidate_typeahead
        from src.shared.http import create_http_client

        async with create_http_client() as http:
            await notify_invalidate_typeahead(http)
    except Exception:
        log.exception("typesense.sync.invalidate_typeahead.failed")

    log.info("typesense.sync.complete")


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

    ts_client = get_typesense_client()

    supa_pool = await create_pool()
    local_pool = None
    if not dry_run:
        try:
            local_pool = await create_local_pool()
        except OSError:
            log.warning("sync.local_pool_unavailable", msg="Cannot reach local Postgres, skipping")
    try:
        # Snapshot taxonomy names before sync for rename detection
        name_maps_before: dict[str, dict[int, str]] | None = None
        if ts_client and not dry_run:
            try:
                async with supa_pool.acquire() as snap_conn:
                    name_maps_before = await _snapshot_name_maps(snap_conn)
            except Exception:
                log.exception("typesense.snapshot_before.failed")

        async with supa_pool.acquire() as supa_conn, supa_conn.transaction():
            await supa_conn.execute("SET lock_timeout = '30s'")

            # Lookup tables -> Supabase (web app queries these)
            await sync_occupation_domains(supa_conn, occupation_domains, dry_run)
            await sync_occupations(supa_conn, occupations, dry_run)
            await sync_seniority(supa_conn, seniority_df, dry_run)
            await sync_technologies(supa_conn, technologies, dry_run)
            await sync_industries(supa_conn, industries, dry_run)

            # Company data: local Postgres is source of truth.
            # 1. Bootstrap: align existing Supabase UUIDs into local
            #    (historical company_ids reference Supabase UUIDs)
            # 2. Apply CSV updates to local (new companies get local UUIDs)
            # 3. Mirror local -> Supabase (display layer)
            if local_pool is not None and not dry_run:
                async with local_pool.acquire() as lc:
                    await _mirror_companies_to_local(supa_conn, lc)
                    await sync_companies(lc, companies, dry_run)
                    await _mirror_companies_to_supabase(lc, supa_conn)
            else:
                # No local pool (dry_run or unreachable) — write to Supabase directly
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

        # --- Typesense sync (OUTSIDE the Supabase transaction) ---
        if ts_client and not dry_run:
            try:
                async with supa_pool.acquire() as supa_conn:
                    local_conn_for_ts = None
                    if local_pool is not None:
                        local_conn_for_ts = await local_pool.acquire()
                    try:
                        # Detect taxonomy renames
                        if name_maps_before is not None:
                            try:
                                name_maps_after = await _snapshot_name_maps(supa_conn)
                                if local_conn_for_ts is not None:
                                    await _apply_taxonomy_renames(
                                        name_maps_before,
                                        name_maps_after,
                                        local_conn_for_ts,
                                        ts_client,
                                    )
                            except Exception:
                                log.exception("typesense.rename_detection.failed")

                        # Full taxonomy + company + watchlist sync
                        await sync_typesense(supa_conn, local_conn_for_ts, ts_client)
                    finally:
                        if local_conn_for_ts is not None:
                            await local_pool.release(local_conn_for_ts)
            except Exception:
                log.exception("typesense.sync.failed")

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
