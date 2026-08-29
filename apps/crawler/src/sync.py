"""CSV -> local crawler-state sync.

Reads data/companies.csv and data/boards.csv, upserts rows into the database.
The DB is derived state — CSVs are the source of truth.

Local Postgres is the only allocator and transactional authority. The normal
CLI publishes Redis board queues and Typesense collections after commit. The
legacy relational-mirror helpers remain library-only during rollback cleanup.

Usage:
    uv run python -m src.sync              # sync both CSVs
    uv run python -m src.sync --dry-run    # show what would change
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import polars as pl
import structlog
from typesense.exceptions import ObjectNotFound

if TYPE_CHECKING:
    import asyncpg
    import typesense

from src.config import settings
from src.core.monitors import api_monitor_types, monitor_needs_browser
from src.core.occupation_resolve import match_occupation, occupation_locale_columns
from src.core.scrapers import scraper_needs_browser
from src.db import close_all_pools, create_local_pool, create_pool, create_web_pool
from src.redis_queue import (
    MonitorSchedule,
    close_redis,
    encode_metadata_for_redis,
    enqueue_monitors,
    remove_monitors,
)
from src.shared.avature import avature_request_host
from src.shared.logging import setup_logging
from src.shared.taleo import taleo_request_host
from src.typesense_client import get_typesense_client

_API_MONITOR_TYPES = api_monitor_types()
_MONITOR_CONFIG_FINGERPRINT = "_monitor_config_fingerprint"
_RECOVERY_SCHEDULE_STATUSES = frozenset({"quarantined", "gone_pending", "gone"})

log = structlog.get_logger()

DATA_DIR = Path(__file__).parent.parent / "data"

# The web app filters every list/search/facet surface by
# `is_active:true && has_content:!=false` (POSTING_BASE_FILTER, see
# apps/web/src/lib/search/typesense-filters.ts). Precomputed taxonomy and
# company counts must use the same filter against the indexed source; otherwise
# the user clicks a facet labelled "N postings" and sees fewer postings than
# the label promised (issue #3009 / #3238).
_POSTING_BASE_FILTER = "is_active:true && has_content:!=false"
_POSTING_FLOW_FILTER = "has_content:!=false"


class CompanyTypesenseSyncError(RuntimeError):
    """Fail-closed company index error that must abort crawler sync."""


def _scraper_chain_needs_browser(
    scraper_type: str | None, scraper_config: dict[str, Any] | None
) -> bool:
    """Return whether any scraper in the configured fallback chain needs a browser."""

    current_type = scraper_type
    current_config = scraper_config
    while current_type:
        if scraper_needs_browser(current_type, current_config):
            return True

        fallback = current_config.get("fallback") if current_config else None
        if not isinstance(fallback, dict):
            return False

        fallback_type = fallback.get("type")
        if not isinstance(fallback_type, str) or not fallback_type:
            return False

        fallback_config = fallback.get("config")
        current_type = fallback_type
        current_config = fallback_config if isinstance(fallback_config, dict) else None

    return False


def _monitor_config_fingerprint(
    board_url: str,
    monitor_type: str,
    metadata: Mapping[str, object],
) -> str:
    """Hash the CSV-owned monitor contract, excluding scrape-only settings."""

    monitor_owned_config = {
        key: value
        for key, value in metadata.items()
        if key
        not in {
            "scraper_type",
            "scraper_config",
            "identity_migration",
            "_identity_migration_receipt",
            _MONITOR_CONFIG_FINGERPRINT,
        }
    }
    payload = json.dumps(
        {
            "board_url": board_url,
            "monitor_type": monitor_type,
            "monitor_config": monitor_owned_config,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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

_MIRROR_TECHNOLOGIES = """
INSERT INTO technology (id, slug, name, category)
SELECT * FROM unnest($1::int[], $2::text[], $3::text[], $4::text[])
ON CONFLICT (slug) DO UPDATE SET
  id = EXCLUDED.id,
  name = COALESCE(EXCLUDED.name, technology.name),
  category = COALESCE(EXCLUDED.category, technology.category)
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

# A slug-stable URL/company change is a replacement source. Realign the local
# authority before its board_url UPSERT, preserve the row id, and clear runtime
# state that belonged to the retired source (#5716).
_REALIGN_RENAMED_BOARD_URLS_LOCAL = """
UPDATE job_board jb
SET board_url = b.board_url,
    company_id = c.id,
    -- Identity-migration receipts are durable one-shot records, not source
    -- runtime state. Preserve them even when slug-stable URL realignment
    -- clears every other metadata key, so a renamed source cannot re-arm a
    -- completed migration.
    metadata = jsonb_strip_nulls(jsonb_build_object(
        '_identity_migration_receipt',
        jb.metadata -> '_identity_migration_receipt'
    )),
    is_enabled = true,
    board_status = 'active',
    consecutive_failures = 0,
    last_error = NULL,
    last_checked_at = NULL,
    last_success_at = NULL,
    next_check_at = now(),
    empty_check_count = 0,
    last_non_empty_at = NULL,
    gone_at = NULL,
    updated_at = now()
FROM unnest($1::text[], $2::text[], $3::text[])
  AS b(company_slug, board_slug, board_url)
JOIN company c ON c.slug = b.company_slug
WHERE jb.board_slug IS NOT NULL
  AND jb.board_slug = b.board_slug
  AND (jb.board_url IS DISTINCT FROM b.board_url
       OR jb.company_id IS DISTINCT FROM c.id)
"""

_UPSERT_BOARD_LOCAL = """
INSERT INTO job_board (company_id, board_slug, board_url,
                       crawler_type, metadata,
                       check_interval_minutes, scrape_interval_hours,
                       throttle_key, monitor_needs_browser, scraper_needs_browser,
                       is_enabled)
SELECT c.id, b.board_slug, b.board_url, b.crawler_type, b.metadata::jsonb,
       b.check_interval_minutes, b.scrape_interval_hours, b.throttle_key,
       b.monitor_needs_browser, b.scraper_needs_browser, b.is_enabled
FROM unnest(
    $1::text[], $2::text[], $3::text[], $4::text[], $5::text[],
    $6::int[], $7::int[], $8::text[], $9::boolean[], $10::boolean[],
    $11::boolean[]
) AS b(company_slug, board_slug, board_url, crawler_type, metadata,
       check_interval_minutes, scrape_interval_hours, throttle_key,
       monitor_needs_browser, scraper_needs_browser, is_enabled)
JOIN company c ON c.slug = b.company_slug
ON CONFLICT (board_url) DO UPDATE SET
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
    -- A URL or monitor-type change is a new source. Runtime discovery
    -- history, watermarks, and gone-detection streaks belong to the old
    -- source and would poison the replacement (issue #5716).
    metadata = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
        THEN COALESCE(EXCLUDED.metadata, '{}'::jsonb)
             || jsonb_strip_nulls(jsonb_build_object(
                 '_identity_migration_receipt',
                 job_board.metadata -> '_identity_migration_receipt'
             ))
        ELSE EXCLUDED.metadata || jsonb_strip_nulls(jsonb_build_object(
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
            -- Runtime receipt wins over CSV metadata. Once a one-shot
            -- identity migration completes, sync must never erase or re-arm
            -- it, even if a stale CSV copy contains a different value.
            '_identity_migration_receipt',
                job_board.metadata -> '_identity_migration_receipt',
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
        ))
    END,
    check_interval_minutes = EXCLUDED.check_interval_minutes,
    scrape_interval_hours = EXCLUDED.scrape_interval_hours,
    throttle_key = EXCLUDED.throttle_key,
    monitor_needs_browser = EXCLUDED.monitor_needs_browser,
    scraper_needs_browser = EXCLUDED.scraper_needs_browser,
    -- A URL/type/config change is a repair candidate. Keep it schedulable but
    -- quarantined until a real monitor run proves the repair (#5716/#6157).
    -- The fingerprint is added without resetting legacy rows on its first
    -- sync; subsequent CSV-owned monitor changes become immediately eligible.
    is_enabled = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN true
        WHEN job_board.board_status = 'disabled' THEN false
        ELSE EXCLUDED.is_enabled
    END,
    board_status = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN 'quarantined'
        ELSE job_board.board_status
    END,
    consecutive_failures = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN 0
        ELSE job_board.consecutive_failures
    END,
    last_error = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN NULL
        ELSE job_board.last_error
    END,
    last_checked_at = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
        THEN NULL
        ELSE job_board.last_checked_at
    END,
    last_success_at = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
        THEN NULL
        ELSE job_board.last_success_at
    END,
    next_check_at = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN now()
        ELSE job_board.next_check_at
    END,
    empty_check_count = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN 0
        ELSE job_board.empty_check_count
    END,
    last_non_empty_at = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
        THEN NULL
        ELSE job_board.last_non_empty_at
    END,
    gone_at = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN NULL
        ELSE job_board.gone_at
    END,
    gone_confirmation_count = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN 0
        ELSE job_board.gone_confirmation_count
    END,
    quarantined_at = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN now()
        ELSE job_board.quarantined_at
    END,
    last_quarantined_at = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN now()
        ELSE job_board.last_quarantined_at
    END,
    last_quarantine_error = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN job_board.last_error
        ELSE job_board.last_quarantine_error
    END,
    quarantine_probe_count = CASE
        WHEN job_board.board_url IS DISTINCT FROM EXCLUDED.board_url
          OR job_board.crawler_type IS DISTINCT FROM EXCLUDED.crawler_type
          OR (job_board.metadata ? '_monitor_config_fingerprint' AND
              job_board.metadata ->> '_monitor_config_fingerprint'
              IS DISTINCT FROM
              EXCLUDED.metadata ->> '_monitor_config_fingerprint')
        THEN 0
        ELSE job_board.quarantine_probe_count
    END,
    updated_at = now()
RETURNING id::text AS board_id, company_id::text AS company_id, board_url,
          metadata, next_check_at, board_status
"""

_MIRROR_BOARDS_SUPA = """
INSERT INTO job_board (id, company_id, board_slug, board_url, crawler_type, metadata)
SELECT * FROM unnest(
    $1::uuid[], $2::uuid[], $3::text[], $4::text[], $5::text[], $6::jsonb[]
)
ON CONFLICT (id) DO UPDATE SET
    company_id = EXCLUDED.company_id,
    board_slug = COALESCE(EXCLUDED.board_slug, job_board.board_slug),
    board_url = EXCLUDED.board_url,
    crawler_type = EXCLUDED.crawler_type,
    metadata = EXCLUDED.metadata,
    is_enabled = true,
    updated_at = now()
"""

_DISABLE_REMOVED_BOARDS = """
UPDATE job_board
SET is_enabled = false,
    board_status = 'disabled',
    updated_at = now()
WHERE board_url NOT IN (SELECT unnest($1::text[]))
  AND is_enabled = true
"""

_DISABLE_REMOVED_BOARDS_LOCAL = """
UPDATE job_board
SET is_enabled = false,
    board_status = 'disabled',
    quarantined_at = NULL,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE board_url NOT IN (SELECT unnest($1::text[]))
  AND is_enabled = true
"""

_FETCH_BOARD_COMPANY_REHOMES_LOCAL = """
WITH incoming AS MATERIALIZED (
    SELECT c.id AS company_id, b.board_slug, b.board_url
    FROM unnest($1::text[], $2::text[], $3::text[])
      AS b(company_slug, board_slug, board_url)
    JOIN company c ON c.slug = b.company_slug
), rehomes AS (
    SELECT jb.id AS board_id, incoming.company_id
    FROM incoming
    JOIN job_board jb ON jb.board_url = incoming.board_url
    WHERE jb.company_id IS DISTINCT FROM incoming.company_id

    UNION

    SELECT jb.id AS board_id, incoming.company_id
    FROM incoming
    JOIN job_board jb
      ON incoming.board_slug IS NOT NULL
     AND jb.board_slug = incoming.board_slug
    WHERE jb.company_id IS DISTINCT FROM incoming.company_id
)
SELECT board_id::text AS board_id, company_id::text AS company_id
FROM rehomes
ORDER BY board_id
"""

_REALIGN_BOARD_POSTING_COMPANIES_SUPA = """
UPDATE job_posting jp
SET company_id = rehome.company_id
FROM unnest($1::uuid[], $2::uuid[]) AS rehome(board_id, company_id)
WHERE jp.board_id = rehome.board_id
  AND jp.company_id IS DISTINCT FROM rehome.company_id
"""

_REALIGN_BOARD_POSTING_COMPANIES_LOCAL = """
UPDATE job_posting jp
SET company_id = rehome.company_id,
    updated_at = now()
FROM unnest($1::uuid[], $2::uuid[]) AS rehome(board_id, company_id)
WHERE jp.board_id = rehome.board_id
  AND jp.company_id IS DISTINCT FROM rehome.company_id
"""

# Every row that should NOT be in Redis. A board appearing here while its
# board_id is still live in ``monitors_*:{domain}`` is why dead boards keep
# producing ``batch.monitor.error`` after being removed from ``boards.csv`` —
# Postgres gets disabled but the worker claims from Redis, not Postgres.
_FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP = """
SELECT id::text AS board_id, throttle_key
FROM job_board
WHERE is_enabled = false OR board_status = 'disabled'
"""


@dataclass(frozen=True)
class BoardSyncEffects:
    """External Redis work derived from one committed local board sync."""

    schedules: tuple[MonitorSchedule, ...] = ()
    orphan_monitors: tuple[tuple[str, str], ...] = ()
    posting_company_rehomes: tuple[tuple[str, str], ...] = ()


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


def _compute_throttle_key(
    monitor_type: str,
    board_url: str,
    metadata: Mapping[str, object] | None = None,
) -> str:
    """Compute rate-limit grouping key from monitor type and board URL."""
    if monitor_type == "darwinbox":
        from src.shared.darwinbox import darwinbox_board_from_metadata, darwinbox_board_from_url

        resolved = darwinbox_board_from_metadata(metadata or {}) or darwinbox_board_from_url(
            board_url
        )
        if resolved is not None:
            return resolved.host
    if monitor_type == "avature":
        resolved_host = avature_request_host(board_url, metadata or {})
        if resolved_host:
            return resolved_host
    if monitor_type == "pageup":
        return "careers.pageuppeople.com"
    if monitor_type in _API_MONITOR_TYPES:
        return monitor_type
    if monitor_type == "taleo":
        resolved_host = taleo_request_host(board_url, metadata or {})
        if resolved_host:
            return resolved_host
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

    locales = occupation_locale_columns(occupations.columns)
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


async def _mirror_companies_to_supabase(
    local_conn: asyncpg.Connection,
    supa_conn: asyncpg.Connection,
) -> None:
    """Push all companies from local Postgres to Supabase.

    Identity alignment is preflighted before this function. It never deletes
    or renumbers a row: an unexpected conflict aborts the mirror transaction.
    """
    rows = await local_conn.fetch(
        "SELECT id, slug, name, website, logo, icon, logo_type, "
        "industry, employee_count_range, founded_year, extras "
        "FROM company"
    )
    if not rows:
        return

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
    local_conn: asyncpg.Connection,
    boards: pl.DataFrame,
    dry_run: bool,
) -> BoardSyncEffects:
    """Commit-ready local board writes and return deferred Redis effects.

    The caller owns the local transaction. No Redis write occurs here, so a
    rollback can never leave queues advertising board state that did not
    commit. Existing board ids survive both ordinary updates and slug-stable
    URL changes; only genuinely new boards use the local UUID default.
    """
    if len(boards) == 0:
        return BoardSyncEffects()

    company_slugs: list[str] = []
    board_slugs: list[str | None] = []
    board_urls: list[str] = []
    crawler_types: list[str] = []
    metadatas: list[str | None] = []
    throttle_keys: list[str] = []
    monitor_browser_flags: list[bool] = []
    scraper_browser_flags: list[bool] = []
    skipped = 0

    for row in boards.iter_rows(named=True):
        mon_type = row["monitor_type"]
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

        metadata_obj[_MONITOR_CONFIG_FINGERPRINT] = _monitor_config_fingerprint(
            row["board_url"], mon_type, metadata_obj
        )

        metadata: str | None = json.dumps(metadata_obj) if metadata_obj else None

        # Compute browser-need flags from crawler_type + config
        mon_browser = monitor_needs_browser(mon_type, metadata_obj)
        scr_type = metadata_obj.get("scraper_type")
        scr_cfg = metadata_obj.get("scraper_config")
        scr_browser = _scraper_chain_needs_browser(scr_type, scr_cfg)

        company_slugs.append(row["company_slug"])
        board_slugs.append(_or_none(row.get("board_slug")))
        board_urls.append(row["board_url"])
        crawler_types.append(mon_type)
        metadatas.append(metadata)
        throttle_keys.append(_compute_throttle_key(mon_type, row["board_url"], metadata_obj))
        monitor_browser_flags.append(mon_browser)
        scraper_browser_flags.append(scr_browser)

    if dry_run:
        log.info("sync.boards.dry_run", count=len(board_urls), skipped=skipped)
        return BoardSyncEffects()

    if not board_urls:
        log.info("sync.boards.all_skipped", skipped=skipped)
        return BoardSyncEffects()

    # Capture ownership changes before either the slug-stable realignment or
    # the board UPSERT overwrites the previous company id. The former query
    # checked every posting for every configured board on every sync, even
    # though a company rehome is rare; at production scale that repeatedly hit
    # the 30-second statement timeout and made the rollback sync fail too.
    rehome_rows = await local_conn.fetch(
        _FETCH_BOARD_COMPANY_REHOMES_LOCAL,
        company_slugs,
        board_slugs,
        board_urls,
    )
    rehomes_by_board: dict[str, str] = {}
    for row in rehome_rows:
        board_id = str(row["board_id"])
        company_id = str(row["company_id"])
        previous_company_id = rehomes_by_board.setdefault(board_id, company_id)
        if previous_company_id != company_id:
            raise RuntimeError(
                f"board sync resolved one existing board to multiple companies: {board_id}"
            )
    posting_company_rehomes = tuple(rehomes_by_board.items())

    # Realign a slug-stable source change before the board_url UPSERT. This
    # preserves the local id while explicitly resetting retired runtime state.
    await local_conn.execute(
        _REALIGN_RENAMED_BOARD_URLS_LOCAL,
        company_slugs,
        board_slugs,
        board_urls,
    )
    local_rows = await local_conn.fetch(
        _UPSERT_BOARD_LOCAL,
        company_slugs,
        board_slugs,
        board_urls,
        crawler_types,
        metadatas,
        [60 for _ in board_urls],
        [24 for _ in board_urls],
        throttle_keys,
        monitor_browser_flags,
        scraper_browser_flags,
        [True for _ in board_urls],
    )
    rows_by_url = {row["board_url"]: row for row in local_rows}
    missing_urls = [url for url in board_urls if url not in rows_by_url]
    if missing_urls:
        raise RuntimeError(
            "local board sync could not resolve every CSV company; "
            f"missing board URLs: {', '.join(missing_urls[:5])}"
        )

    # Derive Redis work from the rows actually returned by the local authority.
    # The caller applies it only after this transaction commits.
    schedules: list[MonitorSchedule] = []
    schedule_time = time.time()
    for i, board_url in enumerate(board_urls):
        local_row = rows_by_url[board_url]
        board_id_str = str(local_row["board_id"])
        board_status = local_row.get("board_status", "active")
        next_check_at = schedule_time
        if board_status in _RECOVERY_SCHEDULE_STATUSES and "next_check_at" in local_row:
            due = local_row["next_check_at"]
            if isinstance(due, datetime):
                next_check_at = max(schedule_time, due.timestamp())
        config = {
            "board_slug": board_slugs[i] or "",
            "board_url": board_url,
            "crawler_type": crawler_types[i],
            "company_id": str(local_row["company_id"]),
            "metadata": encode_metadata_for_redis(local_row["metadata"]),
            "check_interval_minutes": "60",
            "scrape_interval_hours": "24",
            "throttle_key": throttle_keys[i],
            "monitor_needs_browser": "1" if monitor_browser_flags[i] else "0",
            "scraper_needs_browser": "1" if scraper_browser_flags[i] else "0",
        }
        schedules.append(
            MonitorSchedule(
                domain=throttle_keys[i],
                board_id=board_id_str,
                next_check_at=next_check_at,
                config=config,
                browser=monitor_browser_flags[i],
                first_time=board_status not in _RECOVERY_SCHEDULE_STATUSES,
            )
        )

    if posting_company_rehomes:
        await local_conn.execute(
            _REALIGN_BOARD_POSTING_COMPANIES_LOCAL,
            [board_id for board_id, _company_id in posting_company_rehomes],
            [company_id for _board_id, company_id in posting_company_rehomes],
        )

    # Disable removed boards in local Postgres too
    await local_conn.execute(_DISABLE_REMOVED_BOARDS_LOCAL, board_urls)

    # Purge Redis monitor queue for any board that's no longer eligible to run
    # (just-disabled or previously disabled). Without this, the per-domain
    # ``monitors_{wtype}:{domain}`` key retains the stale board_id and the
    # worker keeps claiming it every cycle, producing ``batch.monitor.error``
    # 404s that no CSV update can silence.
    orphan_rows = await local_conn.fetch(_FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP)
    orphan_monitors: list[tuple[str, str]] = []
    for row in orphan_rows:
        domain = row["throttle_key"] or ""
        if not domain:
            continue
        orphan_monitors.append((domain, row["board_id"]))
    log.info(
        "sync.boards.local_staged",
        local_upserted=len(local_rows),
        posting_company_rehomes=len(posting_company_rehomes),
        redis_to_enqueue=len(schedules),
        redis_orphans_to_remove=len(orphan_monitors),
    )
    return BoardSyncEffects(
        schedules=tuple(schedules),
        orphan_monitors=tuple(orphan_monitors),
        posting_company_rehomes=posting_company_rehomes,
    )


async def apply_board_redis_effects(effects: BoardSyncEffects) -> None:
    """Publish board state derived from a successfully committed local sync."""
    await enqueue_monitors(list(effects.schedules))
    if effects.orphan_monitors:
        await remove_monitors(list(effects.orphan_monitors))
    log.info(
        "sync.boards.redis_applied",
        redis_enqueued=len(effects.schedules),
        redis_orphans_removed=len(effects.orphan_monitors),
    )


async def _mirror_table(
    target_conn: asyncpg.Connection,
    table: str,
    mirror_sql: str,
    ids: list[int],
    slugs: list[str],
) -> None:
    """Copy authoritative IDs into a preflighted legacy mirror table."""
    if table not in {"occupation_domain", "occupation", "seniority"}:
        raise ValueError(f"unsupported legacy mirror table: {table}")
    await target_conn.execute(mirror_sql, ids, slugs)
    # Advance to the highest identity that actually exists in the mirror. A
    # stale mirror-only row may legitimately be above every local id; setting
    # the sequence to max(ids) would move it backwards and create a future
    # primary-key collision.
    await target_conn.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"(SELECT MAX(id) FROM {table}), true)"
    )


async def sync_lookup_tables_local(
    local_conn: asyncpg.Connection,
    occupation_domains: pl.DataFrame,
    occupations: pl.DataFrame,
    seniority_df: pl.DataFrame,
    technologies: pl.DataFrame,
    industries: pl.DataFrame,
    dry_run: bool,
) -> None:
    """Upsert CSV lookups locally without replacing any existing identity.

    Natural-key conflicts update content only. Existing integer ids remain
    untouched; new ids come from local sequences. This is the core allocator
    boundary for the Supabase-free sync path.
    """
    if len(occupation_domains) > 0:
        await sync_occupation_domains(local_conn, occupation_domains, dry_run)
    if len(occupations) > 0:
        await sync_occupations(local_conn, occupations, dry_run)
    if len(seniority_df) > 0:
        await sync_seniority(local_conn, seniority_df, dry_run)

    await sync_technologies(local_conn, technologies, dry_run)
    await sync_industries(local_conn, industries, dry_run)
    log.info("sync.lookup_tables_local.complete")


_LEGACY_SLUG_IDENTITY_TABLES = (
    "company",
    "occupation_domain",
    "occupation",
    "seniority",
    "technology",
)


def _identity_conflicts(
    local_rows,
    mirror_rows,
    key: str,
    *,
    check_id_key: bool = True,
) -> list[str]:
    """Return bounded natural-key/id conflicts without attempting repairs."""
    local_by_key = {str(row[key]): str(row["id"]) for row in local_rows if row[key] is not None}
    mirror_by_key = {str(row[key]): str(row["id"]) for row in mirror_rows if row[key] is not None}
    local_by_id = {str(row["id"]): str(row[key]) for row in local_rows if row[key] is not None}
    mirror_by_id = {str(row["id"]): str(row[key]) for row in mirror_rows if row[key] is not None}
    conflicts = [
        f"{key}={value}: local={local_by_key[value]} mirror={mirror_by_key[value]}"
        for value in local_by_key.keys() & mirror_by_key.keys()
        if local_by_key[value] != mirror_by_key[value]
    ]
    if check_id_key:
        conflicts.extend(
            f"id={row_id}: local-{key}={local_by_id[row_id]} mirror-{key}={mirror_by_id[row_id]}"
            for row_id in local_by_id.keys() & mirror_by_id.keys()
            if local_by_id[row_id] != mirror_by_id[row_id]
        )
    return conflicts[:5]


async def _assert_legacy_identity_alignment(
    local_conn: asyncpg.Connection,
    mirror_conn: asyncpg.Connection,
) -> None:
    """Fail closed before any mirror write if existing identities diverge."""
    conflicts: list[str] = []
    for table in _LEGACY_SLUG_IDENTITY_TABLES:
        local_rows = await local_conn.fetch(f"SELECT id, slug FROM {table}")
        mirror_rows = await mirror_conn.fetch(f"SELECT id, slug FROM {table}")
        conflicts.extend(
            f"{table}: {item}" for item in _identity_conflicts(local_rows, mirror_rows, "slug")
        )

    local_boards = await local_conn.fetch("SELECT id, board_slug, board_url FROM job_board")
    mirror_boards = await mirror_conn.fetch("SELECT id, board_slug, board_url FROM job_board")
    conflicts.extend(
        f"job_board: {item}"
        for item in _identity_conflicts(local_boards, mirror_boards, "board_slug")
    )
    conflicts.extend(
        f"job_board: {item}"
        for item in _identity_conflicts(
            local_boards,
            mirror_boards,
            "board_url",
            check_id_key=False,
        )
    )
    if conflicts:
        raise RuntimeError(
            "legacy mirror identity drift; refusing to renumber or delete rows: "
            + "; ".join(conflicts[:5])
        )


async def _mirror_lookup_tables_to_supabase(
    local_conn: asyncpg.Connection,
    supa_conn: asyncpg.Connection,
    occupation_domains: pl.DataFrame,
    occupations: pl.DataFrame,
    seniority_df: pl.DataFrame,
    technologies: pl.DataFrame,
    industries: pl.DataFrame,
) -> None:
    """Copy locally allocated lookup identities into the transitional mirror."""
    for table, sql in (
        ("occupation_domain", _MIRROR_OCCUPATION_DOMAINS),
        ("occupation", _MIRROR_OCCUPATIONS),
        ("seniority", _MIRROR_SENIORITY),
    ):
        rows = await local_conn.fetch(f"SELECT id, slug FROM {table}")
        if rows:
            await _mirror_table(
                supa_conn,
                table,
                sql,
                [row["id"] for row in rows],
                [row["slug"] for row in rows],
            )

    tech_rows = await local_conn.fetch("SELECT id, slug, name, category FROM technology")
    if tech_rows:
        await supa_conn.execute(
            _MIRROR_TECHNOLOGIES,
            [row["id"] for row in tech_rows],
            [row["slug"] for row in tech_rows],
            [row["name"] for row in tech_rows],
            [row["category"] for row in tech_rows],
        )
        await supa_conn.execute(
            "SELECT setval(pg_get_serial_sequence('technology', 'id'), "
            "(SELECT MAX(id) FROM technology), true)"
        )

    # Populate names and relationships only after every exact identity exists.
    await sync_occupation_domains(supa_conn, occupation_domains, False)
    await sync_occupations(supa_conn, occupations, False)
    await sync_seniority(supa_conn, seniority_df, False)
    await sync_industries(supa_conn, industries, False)


async def _mirror_boards_to_supabase(
    local_conn: asyncpg.Connection,
    supa_conn: asyncpg.Connection,
    board_urls: list[str],
    posting_company_rehomes: tuple[tuple[str, str], ...] = (),
) -> None:
    rows = await local_conn.fetch(
        "SELECT id, company_id, board_slug, board_url, crawler_type, metadata "
        "FROM job_board WHERE board_url = ANY($1::text[])",
        board_urls,
    )
    if len(rows) != len(board_urls):
        raise RuntimeError("legacy mirror could not load every committed local board")
    await supa_conn.execute(
        _MIRROR_BOARDS_SUPA,
        [row["id"] for row in rows],
        [row["company_id"] for row in rows],
        [row["board_slug"] for row in rows],
        [row["board_url"] for row in rows],
        [row["crawler_type"] for row in rows],
        [row["metadata"] for row in rows],
    )
    if posting_company_rehomes:
        await supa_conn.execute(
            _REALIGN_BOARD_POSTING_COMPANIES_SUPA,
            [board_id for board_id, _company_id in posting_company_rehomes],
            [company_id for _board_id, company_id in posting_company_rehomes],
        )
    await supa_conn.execute(_DISABLE_REMOVED_BOARDS, board_urls)


async def sync_legacy_mirror(
    local_conn: asyncpg.Connection,
    supa_conn: asyncpg.Connection,
    *,
    occupation_domains: pl.DataFrame,
    occupations: pl.DataFrame,
    seniority_df: pl.DataFrame,
    technologies: pl.DataFrame,
    industries: pl.DataFrame,
    company_descs: pl.DataFrame,
    boards: pl.DataFrame,
    posting_company_rehomes: tuple[tuple[str, str], ...] = (),
) -> None:
    """Mirror a committed local snapshot, aborting atomically on any drift."""
    async with supa_conn.transaction():
        await supa_conn.execute("SET LOCAL lock_timeout = '30s'")
        await _assert_legacy_identity_alignment(local_conn, supa_conn)
        await _mirror_lookup_tables_to_supabase(
            local_conn,
            supa_conn,
            occupation_domains,
            occupations,
            seniority_df,
            technologies,
            industries,
        )
        await _mirror_companies_to_supabase(local_conn, supa_conn)
        await sync_company_descriptions(supa_conn, company_descs, False)
        await _mirror_boards_to_supabase(
            local_conn,
            supa_conn,
            boards["board_url"].to_list(),
            posting_company_rehomes,
        )
        await resolve_pending_misses(supa_conn)
    log.info("sync.legacy_mirror.complete")


# ---------------------------------------------------------------------------
# Typesense helpers
# ---------------------------------------------------------------------------

_TYPESENSE_BATCH_SIZE = 1000


def _ts_bulk_upsert(
    client: typesense.Client,
    collection: str,
    docs: list[dict],
    action: str = "upsert",
    *,
    fail_on_error: bool = False,
) -> None:
    """Bulk write documents to a Typesense collection.

    ``action`` is a Typesense import action:
    - ``"upsert"`` (default): replaces each doc; requires all non-optional fields
    - ``"update"``: partial merge into an existing doc; 404s if the doc doesn't exist
    - ``"emplace"``: partial merge if the doc exists, otherwise creates it

    Splits into batches of ``_TYPESENSE_BATCH_SIZE``. Existing callers keep
    the historical best-effort behavior. Exact-sync callers can set
    ``fail_on_error`` so a rejected import blocks any subsequent pruning.
    """
    if not docs:
        return
    for i in range(0, len(docs), _TYPESENSE_BATCH_SIZE):
        batch = docs[i : i + _TYPESENSE_BATCH_SIZE]
        documents = cast(Any, client.collections[collection].documents)
        results = documents.import_(batch, {"action": action})
        if fail_on_error:
            acknowledged_count = len(results) if isinstance(results, list) else 0
            successful_count = (
                sum(
                    1
                    for result in results
                    if isinstance(result, dict) and result.get("success") is True
                )
                if isinstance(results, list)
                else 0
            )
            if (
                not isinstance(results, list)
                or len(results) != len(batch)
                or any(
                    not isinstance(result, dict) or result.get("success") is not True
                    for result in results
                )
            ):
                log.error(
                    "typesense.bulk_upsert.invalid_acknowledgement",
                    collection=collection,
                    action=action,
                    expected_count=len(batch),
                    acknowledged_count=acknowledged_count,
                    successful_count=successful_count,
                )
                raise RuntimeError(
                    "Typesense bulk import acknowledgement was invalid "
                    f"(collection={collection}, action={action}, "
                    f"expected_count={len(batch)}, "
                    f"acknowledged_count={acknowledged_count}, "
                    f"successful_count={successful_count})"
                ) from None
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
    local_conn: asyncpg.Connection,
    client: typesense.Client,
) -> None:
    """Sync locations to the Typesense ``location`` collection.

    All crawler-owned location data comes from local Postgres.
    """
    rows = await local_conn.fetch(
        """
        SELECT l.id, l.type, l.lat, l.lng, l.slug, l.population, l.parent_id,
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
    missing_slug_ids = [r["id"] for r in rows if not (r["slug"] or "").strip()]
    if missing_slug_ids:
        raise RuntimeError(
            "local location data is not cutover-ready: blank slug for "
            f"{len(missing_slug_ids)} rows (sample ids: {missing_slug_ids[:5]})"
        )

    name_rows = await local_conn.fetch(
        "SELECT location_id, locale, name, is_display FROM location_name "
        "WHERE locale IN ('en', 'de', 'fr', 'it')"
    )
    names_by_id: dict[int, dict[str, str]] = {}
    aliases_by_id: dict[int, set[str]] = {}
    for nr in name_rows:
        if nr["is_display"]:
            names_by_id.setdefault(nr["location_id"], {})[nr["locale"]] = nr["name"]
        else:
            aliases_by_id.setdefault(nr["location_id"], set()).add(nr["name"])

    # Publish the complete hierarchy contract with each location document so
    # the web app can resolve parent paths, expand descendants and render macro
    # membership without reading the crawler mirror. ``ancestor_ids`` contains
    # self + geographic parents + every macro attached to the country in that
    # path, matching exporter.py's job-posting expansion semantics.
    macro_rows = await local_conn.fetch("SELECT macro_id, country_id FROM location_macro_member")
    parent_by_id = {r["id"]: r["parent_id"] for r in rows}
    type_by_id = {r["id"]: r["type"] for r in rows}
    macros_by_country: dict[int, set[int]] = {}
    members_by_macro: dict[int, set[int]] = {}
    for mr in macro_rows:
        macros_by_country.setdefault(mr["country_id"], set()).add(mr["macro_id"])
        members_by_macro.setdefault(mr["macro_id"], set()).add(mr["country_id"])

    ancestors_by_id: dict[int, list[int]] = {}
    for location_id in parent_by_id:
        ancestors: set[int] = set()
        current: int | None = location_id
        while current is not None and current not in ancestors:
            ancestors.add(current)
            if type_by_id.get(current) == "country":
                ancestors.update(macros_by_country.get(current, set()))
            current = parent_by_id.get(current)
        ancestors_by_id[location_id] = [location_id, *sorted(ancestors - {location_id})]

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
        facet_counts = await loop.run_in_executor(None, _fetch_facet_counts, client, "location_ids")
        counts = {int(k): v for k, v in facet_counts.items()}
    except Exception as exc:
        # First-time bootstrap: job_posting collection / index may not
        # exist yet. Fall back to leaf-only Postgres counts so locations
        # still get *some* count rather than zeros.
        log.warning("typesense.locations.facet_unavailable", error=str(exc))
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
            "ancestor_ids": ancestors_by_id[loc_id],
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
        if r["parent_id"] is not None:
            doc["parent_id"] = r["parent_id"]
        member_country_ids = members_by_macro.get(loc_id)
        if member_country_ids:
            doc["member_country_ids"] = sorted(member_country_ids)
        if r["population"] is not None:
            doc["population"] = r["population"]
        # Macro-region aliases (#2939): natural-language synonyms so
        # "Europe" / "European Union" / "DACH" / "Asia Pacific" / etc.
        # match the macro row whose ``name_en`` is just the abbreviation.
        aliases = set(aliases_by_id.get(loc_id, set()))
        if r["type"] == "macro" and r["slug"]:
            aliases.update(_LOCATION_MACRO_ALIASES.get(r["slug"], []))
        if aliases:
            doc["aliases"] = sorted(aliases)

        docs.append(doc)

    await loop.run_in_executor(None, _ts_bulk_upsert, client, "location", docs)
    log.info("typesense.locations.synced", count=len(docs))


async def sync_occupations_typesense(
    local_conn: asyncpg.Connection,
    client: typesense.Client,
) -> None:
    """Sync occupations to the Typesense ``occupation`` collection.

    One document per (occupation, locale) pair.
    """
    rows = await local_conn.fetch(
        """
        SELECT o.id, o.slug, o.parent_id, o.domain_id,
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
    domain_name_rows = await local_conn.fetch(
        "SELECT domain_id, locale, name FROM occupation_domain_name WHERE is_display"
    )
    domain_names: dict[int, dict[str, str]] = {}
    for dr in domain_name_rows:
        domain_names.setdefault(dr["domain_id"], {})[dr["locale"]] = dr["name"]

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
            None, _fetch_facet_counts, client, "occupation_ids"
        )
        counts = {int(k): v for k, v in facet_counts.items()}
    except Exception as exc:
        log.warning("typesense.occupations.facet_unavailable", error=str(exc))
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
            domain_id = r["domain_id"]
            domain_name_map = domain_names.get(domain_id, {}) if domain_id else {}
            occ_data[key] = {
                "occ_id": occ_id,
                "slug": r["slug"],
                "parent_id": r["parent_id"],
                "domain_id": domain_id,
                "domain_slug": r["domain_slug"],
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
        if data["parent_id"] is not None:
            doc["parent_id"] = data["parent_id"]
        if data["domain_id"] is not None:
            doc["domain_id"] = data["domain_id"]
        if data["domain_slug"]:
            doc["domain_slug"] = data["domain_slug"]
        if data["domain_name"]:
            doc["domain_name"] = data["domain_name"]

        docs.append(doc)

    await loop.run_in_executor(None, _ts_bulk_upsert, client, "occupation", docs)
    log.info("typesense.occupations.synced", count=len(docs))


async def sync_seniority_typesense(
    local_conn: asyncpg.Connection,
    client: typesense.Client,
) -> None:
    """Sync seniorities to the Typesense ``seniority`` collection.

    One document per (seniority, locale) pair.
    """
    rows = await local_conn.fetch(
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


_TYPESENSE_COMPANY_ID_PAGE_SIZE = 250
_TYPESENSE_COMPANY_PRUNE_MAX_DOCUMENTS = 50
_TYPESENSE_COMPANY_PRUNE_MAX_BASIS_POINTS = 100  # 1% of the observed remote set.


def _company_prune_within_safety_budget(*, remote_count: int, delete_count: int) -> bool:
    """Bound exact-sync pruning against accidental authority collapse."""

    if remote_count <= 0 or delete_count < 0:
        return False
    return (
        delete_count <= _TYPESENSE_COMPANY_PRUNE_MAX_DOCUMENTS
        and delete_count * 10_000 <= remote_count * _TYPESENSE_COMPANY_PRUNE_MAX_BASIS_POINTS
    )


def _fetch_typesense_company_ids(client: typesense.Client) -> set[str]:
    """Read every company id while enforcing exact pagination invariants."""

    collection = client.collections["company"]
    metadata = collection.retrieve()
    if not isinstance(metadata, dict):
        raise RuntimeError("Typesense company collection returned invalid metadata")
    metadata_count = metadata.get("num_documents")
    if (
        not isinstance(metadata_count, int)
        or isinstance(metadata_count, bool)
        or metadata_count < 0
    ):
        raise RuntimeError("Typesense company collection returned an invalid count")

    found: int | None = None
    returned_count = 0
    seen_ids: set[str] = set()
    page = 1
    while True:
        response = collection.documents.search(
            {
                "q": "*",
                "query_by": "name",
                "include_fields": "id",
                "enable_overrides": False,
                "page": page,
                "per_page": _TYPESENSE_COMPANY_ID_PAGE_SIZE,
            }
        )
        if not isinstance(response, dict):
            raise RuntimeError("Typesense company search returned an invalid response")
        response_found = response.get("found")
        hits = response.get("hits")
        if (
            not isinstance(response_found, int)
            or isinstance(response_found, bool)
            or response_found < 0
            or not isinstance(hits, list)
        ):
            raise RuntimeError("Typesense company search returned invalid pagination")
        if found is None:
            found = response_found
        elif response_found != found:
            raise RuntimeError("Typesense company count changed during pagination")
        if response_found != metadata_count:
            raise RuntimeError("Typesense company metadata and search counts differ")

        remaining = response_found - returned_count
        if remaining < 0:
            raise RuntimeError("Typesense company pagination exceeded its count")
        expected_hits = min(_TYPESENSE_COMPANY_ID_PAGE_SIZE, remaining)
        if len(hits) != expected_hits:
            raise RuntimeError("Typesense company pagination returned an invalid page size")

        for hit in hits:
            if not isinstance(hit, dict) or not isinstance(hit.get("document"), dict):
                raise RuntimeError("Typesense company search returned an invalid hit")
            document_id = hit["document"].get("id")
            if not isinstance(document_id, str) or not document_id:
                raise RuntimeError("Typesense company search returned an invalid id")
            if document_id in seen_ids:
                raise RuntimeError("Typesense company pagination returned a duplicate id")
            seen_ids.add(document_id)

        returned_count += len(hits)
        if returned_count == response_found:
            break
        page += 1

    if len(seen_ids) != metadata_count:
        raise RuntimeError("Typesense company pagination count did not converge")
    return seen_ids


async def sync_companies_typesense(
    local_conn: asyncpg.Connection,
    client: typesense.Client,
) -> None:
    """Sync companies to the Typesense ``company`` collection.

    Populates the per-locale description / industry_name variants used by the
    company detail page reader (``getCompanyBySlug``) so that page can serve
    from Typesense without a Supabase round-trip.
    """
    rows = await local_conn.fetch(
        """
        SELECT c.id, c.name, c.slug, c.icon, c.logo, c.website,
               c.industry,
               c.employee_count_range, c.founded_year,
               i.name AS industry_name
        FROM company c
        LEFT JOIN industry i ON i.id = c.industry
        """
    )
    if not rows:
        log.error("typesense.companies.empty_authority", expected_count=0)
        raise RuntimeError("Local company authority is empty")

    desc_rows = await local_conn.fetch(
        "SELECT company_id, locale, description FROM company_description"
    )
    descs_by_locale: dict[str, dict] = {}
    for r in desc_rows:
        descs_by_locale.setdefault(r["locale"], {})[r["company_id"]] = r["description"]

    ind_name_rows = await local_conn.fetch(
        "SELECT industry_id, locale, name FROM industry_name WHERE is_display"
    )
    ind_names_by_locale: dict[str, dict] = {}
    for r in ind_name_rows:
        ind_names_by_locale.setdefault(r["locale"], {})[r["industry_id"]] = r["name"]

    # Posting-derived company counts come from the same indexed source and
    # filters as the web. The previous local Postgres GROUP BY scans were the
    # last statement-timeout source in sync/refresh-typesense (issue #5752).
    loop = asyncio.get_event_loop()
    active_counts, year_counts = await loop.run_in_executor(
        None, _fetch_company_posting_counts, client
    )

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

        en_desc = descs_by_locale.get("en", {}).get(r["id"])
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

    expected_ids = {str(doc["id"]) for doc in docs}
    if len(expected_ids) != len(docs):
        raise RuntimeError("Local company authority returned duplicate ids")

    await loop.run_in_executor(
        None,
        partial(
            _ts_bulk_upsert,
            client,
            "company",
            docs,
            fail_on_error=True,
        ),
    )

    remote_ids = await loop.run_in_executor(None, _fetch_typesense_company_ids, client)
    missing_count = len(expected_ids - remote_ids)
    if missing_count:
        log.error(
            "typesense.companies.prune.blocked",
            expected_count=len(expected_ids),
            remote_count=len(remote_ids),
            missing_count=missing_count,
        )
        raise RuntimeError("Typesense company index is missing expected documents")

    stale_ids = remote_ids - expected_ids
    if stale_ids and not _company_prune_within_safety_budget(
        remote_count=len(remote_ids),
        delete_count=len(stale_ids),
    ):
        log.error(
            "typesense.companies.prune.budget_blocked",
            expected_count=len(expected_ids),
            remote_count=len(remote_ids),
            delete_count=len(stale_ids),
            max_delete_count=_TYPESENSE_COMPANY_PRUNE_MAX_DOCUMENTS,
            max_delete_basis_points=_TYPESENSE_COMPANY_PRUNE_MAX_BASIS_POINTS,
        )
        raise RuntimeError("Typesense company prune exceeds the safety budget")
    log.info(
        "typesense.companies.prune.planned",
        expected_count=len(expected_ids),
        remote_count=len(remote_ids),
        delete_count=len(stale_ids),
    )
    deleted_count = 0
    for document_id in sorted(stale_ids):
        try:
            await loop.run_in_executor(
                None,
                client.collections["company"].documents[document_id].delete,
            )
        except Exception:
            log.error(
                "typesense.companies.prune.delete_failed",
                expected_count=len(expected_ids),
                remote_count=len(remote_ids),
                planned_delete_count=len(stale_ids),
                completed_delete_count=deleted_count,
            )
            raise RuntimeError("Typesense company prune deletion failed") from None
        deleted_count += 1

    converged_ids = await loop.run_in_executor(None, _fetch_typesense_company_ids, client)
    if converged_ids != expected_ids:
        log.error(
            "typesense.companies.convergence_failed",
            expected_count=len(expected_ids),
            remote_count=len(converged_ids),
            missing_count=len(expected_ids - converged_ids),
            unexpected_count=len(converged_ids - expected_ids),
            deleted_count=deleted_count,
        )
        raise RuntimeError("Typesense company index did not converge")

    log.info(
        "typesense.companies.synced",
        expected_count=len(expected_ids),
        remote_count=len(converged_ids),
        deleted_count=deleted_count,
    )


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
        or f.get("workMode")
        or f.get("employmentType")
        or f.get("salaryMin") is not None
        or f.get("salaryMax") is not None
        or f.get("experienceMin") is not None
        or f.get("experienceMax") is not None
    )


def _parse_watchlist_filters(raw_filters) -> dict | None:
    if isinstance(raw_filters, str):
        try:
            parsed = json.loads(raw_filters)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return raw_filters if isinstance(raw_filters, dict) else None


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _number_value(value) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _resolved_ids_for_slugs(slugs: list[str], id_by_slug: dict[str, int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for slug in slugs:
        resolved_id = id_by_slug.get(slug)
        if resolved_id is None or resolved_id in seen:
            continue
        seen.add(resolved_id)
        result.append(resolved_id)
    return result


def _watchlist_filters_json(
    filters: dict | None,
    resolved_ids: dict[str, list[int]],
) -> str | None:
    """Build the public, self-contained filter payload for Typesense.

    Public Discover cards use this payload to compute live any-company
    counts without hydrating ``watchlist.filters`` from Supabase/Postgres.
    Keep the shape in sync with ``apps/web/src/lib/actions/watchlists.ts``.
    """
    f = filters or {}
    payload: dict = {}

    if f.get("anyCompany") is True:
        payload["anyCompany"] = True

    for key in (
        "keywords",
        "locationSlugs",
        "occupationSlugs",
        "senioritySlugs",
        "technologySlugs",
        "workMode",
        "employmentType",
    ):
        values = _string_list(f.get(key))
        if values:
            payload[key] = values

    salary_currency = f.get("salaryCurrency")
    if isinstance(salary_currency, str) and salary_currency:
        payload["salaryCurrency"] = salary_currency

    for key in ("salaryMin", "salaryMax", "experienceMin", "experienceMax"):
        value = _number_value(f.get(key))
        if value is not None:
            payload[key] = value

    for key, values in resolved_ids.items():
        if values:
            payload[key] = values

    if not payload:
        return None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


async def _resolve_watchlist_filter_ids(
    conn: asyncpg.Connection,
    filters_by_watchlist: dict[str, dict | None],
    *,
    fallback_conn: asyncpg.Connection | None = None,
) -> dict[str, dict[str, list[int]]]:
    """Resolve watchlist filter slugs to numeric taxonomy IDs in batches."""
    location_slugs: set[str] = set()
    occupation_slugs: set[str] = set()
    seniority_slugs: set[str] = set()
    technology_slugs: set[str] = set()

    for filters in filters_by_watchlist.values():
        f = filters or {}
        location_slugs.update(_string_list(f.get("locationSlugs")))
        occupation_slugs.update(_string_list(f.get("occupationSlugs")))
        seniority_slugs.update(_string_list(f.get("senioritySlugs")))
        technology_slugs.update(_string_list(f.get("technologySlugs")))

    async def _fetch_id_map(label: str, sql: str, slugs: set[str]) -> dict[str, int]:
        if not slugs:
            return {}
        use_fallback = fallback_conn is not None and fallback_conn is not conn
        try:
            rows = await conn.fetch(sql, sorted(slugs))
            found = {r["slug"]: int(r["id"]) for r in rows}
        except Exception:
            if not use_fallback:
                raise
            log.warning("typesense.watchlists.filter_ids_primary_failed", taxonomy=label)
            found = {}

        missing = slugs - set(found)
        if missing and use_fallback:
            assert fallback_conn is not None
            try:
                rows = await fallback_conn.fetch(sql, sorted(missing))
                found.update({r["slug"]: int(r["id"]) for r in rows})
            except Exception:
                if not found:
                    raise
                log.warning(
                    "typesense.watchlists.filter_ids_fallback_failed",
                    taxonomy=label,
                    missing=len(missing),
                )
        return found

    location_ids = await _fetch_id_map(
        "location",
        "SELECT slug, id FROM location WHERE slug = ANY($1::text[])",
        location_slugs,
    )
    occupation_ids = await _fetch_id_map(
        "occupation",
        "SELECT slug, id FROM occupation WHERE slug = ANY($1::text[])",
        occupation_slugs,
    )
    seniority_ids = await _fetch_id_map(
        "seniority",
        "SELECT slug, id FROM seniority WHERE slug = ANY($1::text[])",
        seniority_slugs,
    )
    technology_ids = await _fetch_id_map(
        "technology",
        "SELECT slug, id FROM technology WHERE slug = ANY($1::text[])",
        technology_slugs,
    )

    result: dict[str, dict[str, list[int]]] = {}
    for wid, filters in filters_by_watchlist.items():
        f = filters or {}
        result[wid] = {
            "locationIds": _resolved_ids_for_slugs(
                _string_list(f.get("locationSlugs")),
                location_ids,
            ),
            "occupationIds": _resolved_ids_for_slugs(
                _string_list(f.get("occupationSlugs")),
                occupation_ids,
            ),
            "seniorityIds": _resolved_ids_for_slugs(
                _string_list(f.get("senioritySlugs")),
                seniority_ids,
            ),
            "technologyIds": _resolved_ids_for_slugs(
                _string_list(f.get("technologySlugs")),
                technology_ids,
            ),
        }
    return result


async def sync_watchlists_typesense(
    web_conn: asyncpg.Connection,
    local_conn: asyncpg.Connection | None,
    client: typesense.Client,
) -> None:
    """Sync public watchlists to the Typesense ``watchlist`` collection.

    Watchlists are web-owned, so metadata and ``watchlist_company`` pairs come
    from ``web_conn``. The active-posting count per company is computed
    against local Postgres (the job_posting source of truth) and aggregated
    per watchlist in Python. Shared company UUIDs make the cross-database
    aggregation exact. This avoids a costly web-database join against its
    transitional posting mirror. A missing local connection fails closed so
    no caller can silently restore that retired read path.

    Trivial watchlists (no companies, no meaningful filters) are deleted
    from Typesense rather than upserted. Import acknowledgements are validated
    before those dependent deletes, so a partial scheduled refresh cannot
    prune against an incompletely updated watchlist collection.
    """
    if local_conn is None:
        raise RuntimeError("watchlist sync requires a local Postgres connection")

    rows = await web_conn.fetch(
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
    parsed_filters_by_id = {str(r["id"]): _parse_watchlist_filters(r["filters"]) for r in rows}
    try:
        resolved_filter_ids_by_id = await _resolve_watchlist_filter_ids(
            local_conn,
            parsed_filters_by_id,
            fallback_conn=web_conn,
        )
    except Exception:
        log.exception("typesense.watchlists.filter_ids_failed")
        resolved_filter_ids_by_id = {}

    wc_pairs = await web_conn.fetch(
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
    job_counts: dict[str, int] = defaultdict(int)
    for r in wc_pairs:
        job_counts[str(r["watchlist_id"])] += per_company.get(r["company_id"], 0)

    # Mirror counts
    mirror_count_rows = await web_conn.fetch(
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

        filters = parsed_filters_by_id.get(wid)

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
        filters_json = _watchlist_filters_json(
            filters,
            resolved_filter_ids_by_id.get(wid, {}),
        )
        if filters_json:
            doc["filters_json"] = filters_json
        docs.append(doc)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        partial(
            _ts_bulk_upsert,
            client,
            "watchlist",
            docs,
            fail_on_error=True,
        ),
    )
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

# This query enumerates only the small, retained taxonomy/company authorities;
# it never scans ``job_posting``.  The document id is kept separate from the
# facet value because occupation and seniority publish one document per locale.
_COUNT_REFRESH_TARGETS_SQL = """
SELECT 'location' AS collection, l.id::text AS document_id, l.id::text AS facet_value
FROM location l
UNION ALL
SELECT DISTINCT
       'occupation' AS collection,
       o.id::text || '-' || n.locale AS document_id,
       o.id::text AS facet_value
FROM occupation o
JOIN occupation_name n ON n.occupation_id = o.id
WHERE n.is_display AND n.locale <> '*'
UNION ALL
SELECT DISTINCT
       'seniority' AS collection,
       s.id::text || '-' || n.locale AS document_id,
       s.id::text AS facet_value
FROM seniority s
JOIN seniority_name n ON n.seniority_id = s.id
WHERE n.is_display AND n.locale <> '*'
UNION ALL
SELECT 'technology' AS collection, t.id::text AS document_id, t.id::text AS facet_value
FROM technology t
UNION ALL
SELECT 'company' AS collection, c.id::text AS document_id, c.id::text AS facet_value
FROM company c
"""

_COUNT_REFRESH_COLLECTIONS = ("location", "occupation", "seniority", "technology", "company")


async def _fetch_count_refresh_targets(
    local_conn: asyncpg.Connection,
) -> dict[str, dict[str, str]]:
    """Return retained Typesense document ids keyed by their facet value.

    Local Postgres is authoritative for which taxonomy/company documents are
    retained.  Requiring every bounded authority to be non-empty prevents a
    missing or accidentally truncated local source from being mistaken for a
    legitimate all-zero posting facet.
    """
    rows = await local_conn.fetch(_COUNT_REFRESH_TARGETS_SQL)
    targets: dict[str, dict[str, str]] = {
        collection: {} for collection in _COUNT_REFRESH_COLLECTIONS
    }
    for row in rows:
        collection = row["collection"]
        if collection not in targets:
            raise RuntimeError(f"Unknown count-refresh collection: {collection}")
        document_id = row["document_id"]
        facet_value = row["facet_value"]
        if not isinstance(document_id, str) or not document_id:
            raise RuntimeError(f"Invalid {collection} count-refresh document id")
        if not isinstance(facet_value, str) or not facet_value:
            raise RuntimeError(f"Invalid {collection} count-refresh facet value")
        previous = targets[collection].setdefault(document_id, facet_value)
        if previous != facet_value:
            raise RuntimeError(f"Conflicting {collection} count-refresh target")

    empty_authorities = [collection for collection, values in targets.items() if not values]
    if empty_authorities:
        joined = ", ".join(empty_authorities)
        raise RuntimeError(f"Count-refresh authority is empty for: {joined}")
    return targets


def _fetch_facet_counts(
    client: typesense.Client,
    field: str,
    filter_by: str = _POSTING_BASE_FILTER,
) -> dict[str, int]:
    """Read filtered facet counts from the Typesense ``job_posting`` collection.

    Returns ``{facet_value: count}`` for every distinct value of ``field``
    matching ``filter_by``. ``field`` is the Typesense facet field name.
    ``location_ids`` and ``occupation_ids`` are *post* ancestor expansion
    in the indexer (``exporter._build_typesense_docs``), so the resulting
    counts include city -> country -> macro fan-in. This is the count the
    user sees when clicking the facet in the UI; counting from local
    Postgres ``unnest(location_ids)`` is leaf-only and silently diverges
    from filter results (issue #2978). Other indexed facet arrays such as
    ``technology_ids`` use the same web-visible base filter without forcing
    Postgres to rescan and unnest active postings during refresh.

    The ``filter_by`` clause matches the web's ``POSTING_BASE_FILTER``
    (``is_active:true && has_content:!=false``) so the count an operator
    sees on a location / occupation / company card equals the count
    they get when they filter the job-posting collection by that doc
    (issue #3238).

    Synchronous — designed to be called from
    ``loop.run_in_executor(None, _fetch_facet_counts, ...)``.
    """
    resp = client.collections["job_posting"].documents.search(
        {
            "q": "*",
            "query_by": "title",
            "filter_by": filter_by,
            "facet_by": field,
            "max_facet_values": _TS_FACET_REFRESH_MAX,
            "facet_strategy": "exhaustive",
            "per_page": 0,
        }
    )
    if not isinstance(resp, dict) or "facet_counts" not in resp:
        raise RuntimeError(f"Typesense facet response is missing facet_counts for {field}")
    facets = resp["facet_counts"]
    if not isinstance(facets, list):
        raise RuntimeError(f"Typesense facet response has invalid facet_counts for {field}")
    if not facets:
        return {}
    matching_facets = [
        facet for facet in facets if isinstance(facet, dict) and facet.get("field_name") == field
    ]
    if len(matching_facets) != 1:
        raise RuntimeError(f"Typesense facet response did not contain exactly one {field} facet")
    facet = matching_facets[0]
    if "counts" not in facet or not isinstance(facet["counts"], list):
        raise RuntimeError(f"Typesense facet response has invalid counts for {field}")

    result: dict[str, int] = {}
    for entry in facet["counts"]:
        if not isinstance(entry, dict):
            raise RuntimeError(f"Typesense facet response has invalid count entry for {field}")
        value = entry.get("value")
        count = entry.get("count")
        if (
            not isinstance(value, str)
            or not value
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise RuntimeError(f"Typesense facet response has invalid count entry for {field}")
        if value in result:
            raise RuntimeError(f"Typesense facet response has duplicate value for {field}")
        result[value] = count
    return result


def _one_year_ago_epoch(now: datetime | None = None) -> int:
    """Return the calendar-year cutoff used by the web's posting-flow count."""
    current = now or datetime.now(UTC)
    try:
        cutoff = current.replace(year=current.year - 1)
    except ValueError:
        # PostgreSQL's ``interval '1 year'`` maps leap day to February 28.
        cutoff = current.replace(year=current.year - 1, day=28)
    return int(cutoff.timestamp())


def _fetch_company_posting_counts(
    client: typesense.Client,
    now: datetime | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return active and one-year company counts from indexed facets."""
    active = _fetch_facet_counts(client, "company_id", _POSTING_BASE_FILTER)
    year = _fetch_facet_counts(
        client,
        "company_id",
        f"{_POSTING_FLOW_FILTER} && first_seen_at:>{_one_year_ago_epoch(now)}",
    )
    return active, year


async def refresh_typesense_counts(
    local_conn: asyncpg.Connection,
    client: typesense.Client,
) -> None:
    """Refresh active_posting_count on all taxonomy and company collections.

    Idempotent — can be called after each sync run or on a timer.
    Counts are approximate.

    Every submitted update requires an exact, explicit-success Typesense
    acknowledgement. The scheduled CLI therefore exits non-zero (and records
    a failed cron run) rather than reporting partially refreshed counts as
    successful.

    Location, occupation, seniority, technology, and company counts are read
    from the Typesense ``job_posting`` facets so displayed counts match the
    indexed filters they represent. Reading from local Postgres either silently
    diverges from filter results (issue #2978) or can exceed the crawler's
    statement timeout on the posting set (issues #4947, #4961, and #5752).
    """
    loop = asyncio.get_event_loop()
    targets = await _fetch_count_refresh_targets(local_conn)

    # Validate every upstream facet before the first write. A missing or
    # malformed response must never be reinterpreted as an empty facet and
    # fan out destructive zeroes to an otherwise healthy collection.
    loc_facet = await loop.run_in_executor(None, _fetch_facet_counts, client, "location_ids")
    occ_facet = await loop.run_in_executor(None, _fetch_facet_counts, client, "occupation_ids")
    sen_facet = await loop.run_in_executor(None, _fetch_facet_counts, client, "seniority_id")
    tech_facet = await loop.run_in_executor(None, _fetch_facet_counts, client, "technology_ids")
    active_map, year_map = await loop.run_in_executor(None, _fetch_company_posting_counts, client)

    # Count refresh uses action="update" (partial merge). The taxonomy and
    # company docs are fully written by sync_*_typesense; here we only touch
    # the *_posting_count fields, so we must not require the schema's other
    # non-optional fields like `name`. See issue #2622.

    # --- Locations (read from Typesense facet — see #2978) ---
    loc_docs = []
    for document_id, facet_value in targets["location"].items():
        count = loc_facet.get(facet_value, 0)
        loc_docs.append(
            {
                "id": document_id,
                "active_posting_count": count,
                "has_active_postings": count > 0,
            }
        )
    await loop.run_in_executor(
        None,
        partial(
            _ts_bulk_upsert,
            client,
            "location",
            loc_docs,
            "update",
            fail_on_error=True,
        ),
    )

    # --- Occupations (read from Typesense facet on `occupation_ids` —
    # which carries the leaf occupation + its ancestors in
    # exporter._build_typesense_docs) ---
    occ_docs: list[dict] = []
    for document_id, facet_value in targets["occupation"].items():
        count = occ_facet.get(facet_value, 0)
        occ_docs.append(
            {
                "id": document_id,
                "active_posting_count": count,
                "has_active_postings": count > 0,
            }
        )
    await loop.run_in_executor(
        None,
        partial(
            _ts_bulk_upsert,
            client,
            "occupation",
            occ_docs,
            "update",
            fail_on_error=True,
        ),
    )

    # --- Seniorities (read from Typesense facet to avoid the production
    # Postgres aggregate exceeding the statement timeout even with its
    # dedicated partial index; #4947) ---
    sen_docs: list[dict] = []
    for document_id, facet_value in targets["seniority"].items():
        count = sen_facet.get(facet_value, 0)
        sen_docs.append(
            {
                "id": document_id,
                "active_posting_count": count,
                "has_active_postings": count > 0,
            }
        )
    await loop.run_in_executor(
        None,
        partial(
            _ts_bulk_upsert,
            client,
            "seniority",
            sen_docs,
            "update",
            fail_on_error=True,
        ),
    )

    # --- Technologies (read from Typesense facet to avoid the production
    # Postgres unnest aggregate timing out on the active posting set; #4961) ---
    tech_docs = []
    for document_id, facet_value in targets["technology"].items():
        count = tech_facet.get(facet_value, 0)
        tech_docs.append(
            {
                "id": document_id,
                "active_posting_count": count,
                "has_active_postings": count > 0,
            }
        )
    await loop.run_in_executor(
        None,
        partial(
            _ts_bulk_upsert,
            client,
            "technology",
            tech_docs,
            "update",
            fail_on_error=True,
        ),
    )

    # --- Companies (indexed facets; issue #5752) ---
    company_docs = [
        {
            "id": document_id,
            "active_posting_count": active_map.get(facet_value, 0),
            "year_posting_count": year_map.get(facet_value, 0),
        }
        for document_id, facet_value in targets["company"].items()
    ]
    await loop.run_in_executor(
        None,
        partial(
            _ts_bulk_upsert,
            client,
            "company",
            company_docs,
            "update",
            fail_on_error=True,
        ),
    )

    log.info("typesense.refresh_counts.done")


# ---------------------------------------------------------------------------
# Taxonomy rename detection
# ---------------------------------------------------------------------------


async def _snapshot_name_maps(
    local_conn: asyncpg.Connection,
) -> dict[str, dict[int, str]]:
    """Snapshot current display names for rename detection.

    Returns a dict keyed by taxonomy type with {id: display_name_en} maps.
    """
    occ_rows = await local_conn.fetch(
        """
        SELECT o.id, on2.name
        FROM occupation o
        JOIN occupation_name on2 ON on2.occupation_id = o.id
        WHERE on2.is_display AND on2.locale = 'en'
        """
    )
    sen_rows = await local_conn.fetch(
        """
        SELECT s.id, sn.name
        FROM seniority s
        JOIN seniority_name sn ON sn.seniority_id = s.id
        WHERE sn.is_display AND sn.locale = 'en'
        """
    )
    tech_rows = await local_conn.fetch("SELECT id, name FROM technology")

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
    local_conn: asyncpg.Connection,
    web_conn: asyncpg.Connection,
    client: typesense.Client,
) -> None:
    """Sync all taxonomy, company, and watchlist data to Typesense.

    Called only after the local transaction commits. Crawler-owned collections
    read local Postgres; only user-owned watchlists read ``web_conn``.
    """
    try:
        await sync_locations_typesense(local_conn, client)
    except Exception:
        log.exception("typesense.sync.locations.failed")

    try:
        await sync_occupations_typesense(local_conn, client)
    except Exception:
        log.exception("typesense.sync.occupations.failed")

    try:
        await sync_seniority_typesense(local_conn, client)
    except Exception:
        log.exception("typesense.sync.seniority.failed")

    try:
        await sync_technologies_typesense(local_conn, client)
    except Exception:
        log.exception("typesense.sync.technologies.failed")

    try:
        await sync_companies_typesense(local_conn, client)
    except Exception:
        # Exact-sync stages emit count-only diagnostics before raising. Do not
        # attach a client exception that may contain a document identifier.
        log.error("typesense.sync.companies.failed")
        raise CompanyTypesenseSyncError("Typesense company exact sync failed") from None

    try:
        await sync_watchlists_typesense(web_conn, local_conn, client)
    except Exception:
        log.exception("typesense.sync.watchlists.failed")

    # Refresh posting counts
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


async def run_sync(dry_run: bool = False, *, legacy_mirror: bool = False) -> None:
    """Sync CSV state with local Postgres as the transaction authority.

    ``legacy_mirror`` is an explicit transition mode. It is never inferred
    from the mere presence of ``DATABASE_URL`` and refuses to start when that
    credential is absent.
    """
    setup_logging(settings.log_level)

    if legacy_mirror and not settings.database_url:
        raise RuntimeError(
            "--legacy-mirror requires DATABASE_URL; refusing a partial local-only sync"
        )

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

    local_pool = await create_local_pool()
    board_effects = BoardSyncEffects()
    name_maps_before: dict[str, dict[int, str]] | None = None
    try:
        async with local_pool.acquire() as local_conn:
            local_connection = cast("asyncpg.Connection", local_conn)
            if ts_client and not dry_run:
                name_maps_before = await _snapshot_name_maps(local_connection)

            async with local_conn.transaction():
                await local_conn.execute("SET LOCAL lock_timeout = '30s'")
                await sync_lookup_tables_local(
                    local_connection,
                    occupation_domains,
                    occupations,
                    seniority_df,
                    technologies,
                    industries,
                    dry_run,
                )
                await sync_companies(local_connection, companies, dry_run)
                await sync_company_descriptions(local_connection, company_descs, dry_run)
                board_effects = await sync_boards(local_connection, boards, dry_run)
                if not dry_run:
                    await resolve_pending_misses(local_connection)

        # The local transaction is committed before every downstream write.
        if legacy_mirror and not dry_run:
            supa_pool = await create_pool()
            async with local_pool.acquire() as local_conn, supa_pool.acquire() as supa_conn:
                await sync_legacy_mirror(
                    cast("asyncpg.Connection", local_conn),
                    cast("asyncpg.Connection", supa_conn),
                    occupation_domains=occupation_domains,
                    occupations=occupations,
                    seniority_df=seniority_df,
                    technologies=technologies,
                    industries=industries,
                    company_descs=company_descs,
                    boards=boards,
                    posting_company_rehomes=board_effects.posting_company_rehomes,
                )

        web_pool = None
        if ts_client and not dry_run:
            # Establish the provider-neutral user-data boundary before Redis
            # is changed; a missing WEB_DATABASE_URL therefore fails closed.
            web_pool = await create_web_pool()

        if not dry_run:
            await apply_board_redis_effects(board_effects)

        # Reconcile visibility after board hashes/schedules and retired-board
        # cleanup are complete. This deliberately classifies and reports only:
        # active poison descriptors remain parked until an explicit operator
        # retry, and retired descriptors remain until an explicit prune.
        if local_pool is not None and not dry_run:
            from src.deadletters import classify_deadletters, lifecycle_counts

            deadletters = await classify_deadletters(local_pool)
            log.info(
                "sync.deadletters.reconciled",
                total=len(deadletters),
                counts=lifecycle_counts(deadletters),
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
            legacy_mirror=legacy_mirror,
        )

        # Typesense is a post-commit derived target. Crawler-owned collections
        # read local Postgres; watchlists use only the provider-neutral web DB.
        if ts_client and not dry_run:
            assert web_pool is not None
            try:
                async with local_pool.acquire() as local_conn, web_pool.acquire() as web_conn:
                    local_connection = cast("asyncpg.Connection", local_conn)
                    web_connection = cast("asyncpg.Connection", web_conn)
                    if name_maps_before is not None:
                        try:
                            name_maps_after = await _snapshot_name_maps(local_connection)
                            await _apply_taxonomy_renames(
                                name_maps_before,
                                name_maps_after,
                                local_connection,
                                ts_client,
                            )
                        except Exception:
                            log.exception("typesense.rename_detection.failed")
                    await sync_typesense(local_connection, web_connection, ts_client)
            except CompanyTypesenseSyncError:
                log.exception("typesense.sync.failed")
                raise
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
