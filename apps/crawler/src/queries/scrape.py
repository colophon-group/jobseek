"""SQL queries for scrape operations."""

from __future__ import annotations

from src.workspace._compat import auto_skip_crawler_types


def _build_skip_no_scrape_predicate(board_alias: str = "jb") -> str:
    """Build the SQL predicate for 'board is rich-monitor, no scraping'.

    Mirrors ``_is_skip_no_scrape`` (``processing/scrape.py``). Returns a
    parenthesized boolean expression suitable for ``WHERE NOT ( … )``.

    Two cases match:
    1. ``metadata.scraper_type = 'skip'`` explicitly.
    2. ``metadata.scraper_type`` is unset AND ``crawler_type`` is in the
       auto-resolved rich-monitor set.

    Both cases additionally require no enrichment to be configured.
    ``COALESCE`` wraps the ``? 'enrich'`` check because the JSONB ``?``
    operator returns NULL when ``scraper_config`` itself is NULL, and
    ``NOT NULL`` is NULL (not TRUE).

    The rich crawler-type list is injected as a string literal so callers
    don't need to pass extra SQL parameters. Keep it in sync with
    ``workspace._compat._AUTO_SKIP_CRAWLER_TYPES``.
    """
    types = sorted(auto_skip_crawler_types())
    literal = ", ".join(f"'{t}'" for t in types)
    return f"""(
            ({board_alias}.metadata->>'scraper_type' = 'skip'
             OR (
                 {board_alias}.metadata->>'scraper_type' IS NULL
                 AND {board_alias}.crawler_type IN ({literal})
             )
            )
            AND NOT COALESCE({board_alias}.metadata->'scraper_config' ? 'enrich', false)
        )"""


_SKIP_NO_SCRAPE_PREDICATE = _build_skip_no_scrape_predicate("jb")


_FETCH_DUE_JOB_POSTINGS = f"""
WITH candidates AS (
    SELECT jp.id,
           split_part(split_part(jp.source_url, '://', 2), '/', 1) AS domain,
           jp.next_scrape_at,
           (jp.description_r2_hash IS NULL)::int AS needs_initial_scrape
    FROM job_posting jp
    JOIN job_board  jb ON jp.board_id = jb.id
    WHERE jp.is_active = true
      AND jp.next_scrape_at IS NOT NULL
      AND jp.next_scrape_at <= now()
      AND (jp.leased_until IS NULL OR jp.leased_until < now())
      -- Defense in depth: never pick up postings from rich-monitor boards.
      -- See issue #01-rich-monitor-scheduling.
      AND NOT {_SKIP_NO_SCRAPE_PREDICATE}
    FOR UPDATE OF jp SKIP LOCKED
),
ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY domain
               ORDER BY needs_initial_scrape DESC, next_scrape_at
           ) AS domain_rank,
           needs_initial_scrape,
           next_scrape_at
    FROM candidates
)
UPDATE job_posting
SET leased_until = now() + interval '10 minutes'
FROM (
    SELECT id AS rid
    FROM ranked
    ORDER BY domain_rank, needs_initial_scrape DESC, next_scrape_at
    LIMIT $1
) pick
WHERE job_posting.id = pick.rid
RETURNING job_posting.id, source_url, board_id,
          split_part(split_part(source_url, '://', 2), '/', 1) AS scrape_domain,
          description_r2_hash
"""

_UPDATE_JOB_CONTENT = """
UPDATE job_posting
SET employment_type = COALESCE($2, employment_type),
    titles = COALESCE($3, titles),
    locales = COALESCE($4, locales),
    location_ids = COALESCE($5, location_ids),
    location_types = COALESCE($6, location_types),
    technology_ids = COALESCE($7, technology_ids),
    salary_min = COALESCE($8, salary_min),
    salary_max = COALESCE($9, salary_max),
    salary_currency = COALESCE($10, salary_currency),
    salary_period = COALESCE($11, salary_period),
    salary_eur = COALESCE($12, salary_eur),
    experience_min = COALESCE($13, experience_min),
    experience_max = COALESCE($14, experience_max),
    occupation_id = COALESCE($15, occupation_id),
    seniority_id = COALESCE($16, seniority_id),
    to_be_enriched = true,
    updated_at = CASE
        WHEN employment_type IS DISTINCT FROM COALESCE($2, employment_type)
          OR titles IS DISTINCT FROM COALESCE($3, titles)
          OR locales IS DISTINCT FROM COALESCE($4, locales)
          OR location_ids IS DISTINCT FROM COALESCE($5, location_ids)
          OR location_types IS DISTINCT FROM COALESCE($6, location_types)
          OR technology_ids IS DISTINCT FROM COALESCE($7, technology_ids)
          OR salary_min IS DISTINCT FROM COALESCE($8, salary_min)
          OR salary_max IS DISTINCT FROM COALESCE($9, salary_max)
          OR salary_currency IS DISTINCT FROM COALESCE($10, salary_currency)
          OR salary_period IS DISTINCT FROM COALESCE($11, salary_period)
          OR salary_eur IS DISTINCT FROM COALESCE($12, salary_eur)
          OR experience_min IS DISTINCT FROM COALESCE($13, experience_min)
          OR experience_max IS DISTINCT FROM COALESCE($14, experience_max)
          OR occupation_id IS DISTINCT FROM COALESCE($15, occupation_id)
          OR seniority_id IS DISTINCT FROM COALESCE($16, seniority_id)
        THEN now()
        ELSE updated_at
    END
WHERE id = $1
"""

_UPDATE_ENRICH_CONTENT = """
UPDATE job_posting
SET employment_type = COALESCE($2, employment_type),
    titles = COALESCE($3, titles),
    locales = COALESCE($4, locales),
    location_ids = COALESCE($5, location_ids),
    location_types = COALESCE($6, location_types),
    technology_ids = COALESCE($7, technology_ids),
    salary_min = COALESCE($8, salary_min),
    salary_max = COALESCE($9, salary_max),
    salary_currency = COALESCE($10, salary_currency),
    salary_period = COALESCE($11, salary_period),
    salary_eur = COALESCE($12, salary_eur),
    experience_min = COALESCE($13, experience_min),
    experience_max = COALESCE($14, experience_max),
    occupation_id = COALESCE($15, occupation_id),
    seniority_id = COALESCE($16, seniority_id),
    to_be_enriched = true,
    updated_at = CASE
        WHEN employment_type IS DISTINCT FROM COALESCE($2, employment_type)
          OR titles IS DISTINCT FROM COALESCE($3, titles)
          OR locales IS DISTINCT FROM COALESCE($4, locales)
          OR location_ids IS DISTINCT FROM COALESCE($5, location_ids)
          OR location_types IS DISTINCT FROM COALESCE($6, location_types)
          OR technology_ids IS DISTINCT FROM COALESCE($7, technology_ids)
          OR salary_min IS DISTINCT FROM COALESCE($8, salary_min)
          OR salary_max IS DISTINCT FROM COALESCE($9, salary_max)
          OR salary_currency IS DISTINCT FROM COALESCE($10, salary_currency)
          OR salary_period IS DISTINCT FROM COALESCE($11, salary_period)
          OR salary_eur IS DISTINCT FROM COALESCE($12, salary_eur)
          OR experience_min IS DISTINCT FROM COALESCE($13, experience_min)
          OR experience_max IS DISTINCT FROM COALESCE($14, experience_max)
          OR occupation_id IS DISTINCT FROM COALESCE($15, occupation_id)
          OR seniority_id IS DISTINCT FROM COALESCE($16, seniority_id)
        THEN now()
        ELSE updated_at
    END
WHERE id = $1
"""

_RECORD_SCRAPE_SUCCESS = """
UPDATE job_posting jp
SET scrape_failures  = 0,
    last_scraped_at  = now(),
    next_scrape_at   = CASE
        WHEN jp.is_active
        THEN now() + (COALESCE(
            (SELECT scrape_interval_hours FROM job_board WHERE id = jp.board_id),
            24
        ) || ' hours')::interval
        ELSE NULL
    END,
    leased_until     = NULL
WHERE jp.id = $1
"""

_RECORD_SCRAPE_FAILURE = """
UPDATE job_posting
SET scrape_failures   = scrape_failures + 1,
    last_scraped_at   = now(),
    next_scrape_at    = CASE
        WHEN scrape_failures + 1 >= 3 THEN NULL
        ELSE now() + (30 * pow(2, scrape_failures)) * interval '1 minute'
    END,
    leased_until = NULL
WHERE id = $1
"""

_CLEAR_SCRAPE_FOR_RICH = f"""
UPDATE job_posting jp
SET next_scrape_at = NULL, leased_until = NULL
FROM job_board jb
WHERE jp.id = ANY($1::uuid[])
  AND jb.id = jp.board_id
  -- Scope the clear to boards that are STILL rich-no-scrape. Without this
  -- predicate the UPDATE races with legitimate scrape writers when a
  -- board was just reclassified (config drift, enrich added). See issue
  -- #01-rich-monitor-scheduling.
  AND {_SKIP_NO_SCRAPE_PREDICATE}
"""

_FETCH_POSTING_FOR_ENRICH = """
SELECT titles, locales, location_ids, location_types, employment_type
FROM job_posting
WHERE id = $1
"""

_FETCH_BOARD_SCRAPERS = """
SELECT id::text AS id, metadata, crawler_type
FROM job_board
WHERE id::text = ANY($1::text[])
"""

_EXTEND_SCRAPE_LEASE = """
UPDATE job_posting
SET leased_until = now() + interval '10 minutes'
WHERE id = $1
"""

_FETCH_BOARD_BY_SLUG = """
SELECT * FROM job_board WHERE board_slug = $1
"""

_FETCH_BOARD_SCRAPE_ITEMS = """
SELECT id, source_url, board_id,
       split_part(split_part(source_url, '://', 2), '/', 1) AS scrape_domain,
       description_r2_hash
FROM job_posting
WHERE board_id = $1
  AND is_active = true
  AND next_scrape_at IS NOT NULL
  AND next_scrape_at <= now()
"""

_FETCH_BOARD_ALL_ACTIVE = """
SELECT id, source_url, board_id,
       split_part(split_part(source_url, '://', 2), '/', 1) AS scrape_domain,
       description_r2_hash
FROM job_posting
WHERE board_id = $1
  AND is_active = true
"""
