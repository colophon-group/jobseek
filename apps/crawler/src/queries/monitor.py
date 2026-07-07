"""SQL queries for monitor (board) operations."""

from __future__ import annotations

__all__ = [
    "_BATCH_UPDATE_RICH_CONTENT",
    "_BLAST_RADIUS_FLOOR_DEFAULT",
    "_COUNT_BOARD_ACTIVE_AND_MISSING",
    "_CREATE_RICH_UPDATES_TEMP",
    "_DELIST_BOARD_POSTINGS",
    "_DELIST_THRESHOLD_AUTHORITATIVE",
    "_DELIST_THRESHOLD_FRAGILE",
    "_DIFF_BATCH",
    "_DROP_GUARD_HISTORY_WINDOW",
    "_DROP_GUARD_MIN_HISTORY",
    "_DROP_GUARD_THRESHOLD_DEFAULT",
    "_EXTEND_BOARD_LEASE",
    "_FETCH_DUE_BOARDS",
    "_INSERT_RICH_JOB",
    "_INSERT_RICH_JOB_ENRICH",
    "_INSERT_URL_ONLY_JOBS",
    "_MARK_GONE",
    "_MARK_GONE_BY_TIMESTAMP",
    "_RECORD_BOARD_GONE",
    "_RECORD_EMPTY_CHECK",
    "_RECORD_FAILURE",
    "_RECORD_SUCCESS_NONEMPTY",
    "_RELEASE_BOARD_LEASE",
    "_RELEASE_BOARD_LEASES",
    "_RELEASE_POSTING_LEASES",
    "_UPDATE_METADATA",
    "_UPSERT_LOCATION_MISSES",
]

_FETCH_DUE_BOARDS = """
WITH ranked AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY throttle_key
           ORDER BY next_check_at, id
         ) AS domain_rank,
         next_check_at
  FROM job_board
  WHERE is_enabled = true
    AND board_status IN ('active', 'suspect')
    AND next_check_at <= now()
    AND (leased_until IS NULL OR leased_until < now())
),
picked AS (
  SELECT id
  FROM ranked
  ORDER BY domain_rank, next_check_at, id
  LIMIT $1
  FOR UPDATE SKIP LOCKED
)
UPDATE job_board b
SET lease_owner   = $2,
    leased_until  = now() + interval '10 minutes',
    last_checked_at = now(),
    next_check_at = now() + (check_interval_minutes || ' minutes')::interval
FROM picked
WHERE b.id = picked.id
RETURNING b.*
"""

_RELEASE_BOARD_LEASE = """
UPDATE job_board
SET lease_owner = NULL, leased_until = NULL
WHERE id = $1
"""

_RELEASE_BOARD_LEASES = """
UPDATE job_board
SET lease_owner = NULL, leased_until = NULL
WHERE id = ANY($1::uuid[])
"""

_RELEASE_POSTING_LEASES = """
UPDATE job_posting
SET leased_until = NULL
WHERE id = ANY($1::uuid[])
"""

# Delist threshold: API monitors are authoritative (1 miss = delist),
# URL-only monitors are fragile (#2725: 4 misses before delist; was 2 until
# the 2026-04-26 NHS spike showed 2 was too tight against transient
# pagination flaps). Per-board override is read from
# ``metadata.delist_threshold`` in ``processing/board.py``.
_DELIST_THRESHOLD_AUTHORITATIVE = 1
_DELIST_THRESHOLD_FRAGILE = 4

# Drop guardrail (#2723). Skip _MARK_GONE_BY_TIMESTAMP when the monitor's
# discovered count drops more than DROP_THRESHOLD below the rolling median
# of the last HISTORY_WINDOW successful runs. Catches paginating monitors
# that silently truncate on transient errors (#2722). MIN_HISTORY avoids
# firing on freshly-onboarded boards before a baseline exists — the
# blast-radius guard below covers that case.
_DROP_GUARD_THRESHOLD_DEFAULT = 0.30
_DROP_GUARD_HISTORY_WINDOW = 5
_DROP_GUARD_MIN_HISTORY = 3

# Blast-radius cap (#2724). Last-line defense: if the fraction of a board's
# active postings about to be marked missing in a single cycle exceeds
# BLAST_RADIUS_FLOOR, skip _MARK_GONE_BY_TIMESTAMP. Independent of (b);
# fires even with empty discovered-count history.
_BLAST_RADIUS_FLOOR_DEFAULT = 0.50

_COUNT_BOARD_ACTIVE_AND_MISSING = """
SELECT
    COUNT(*) FILTER (WHERE is_active) AS active,
    COUNT(*) FILTER (WHERE is_active AND last_seen_at < $2) AS missing
FROM job_posting
WHERE board_id = $1
"""

_DELIST_BOARD_POSTINGS = """
UPDATE job_posting
SET is_active = false, next_scrape_at = NULL, updated_at = now()
WHERE board_id = $1 AND is_active = true
RETURNING id
"""

_RECORD_BOARD_GONE = """
UPDATE job_board
SET board_status = 'gone', gone_at = now(),
    is_enabled = false,
    lease_owner = NULL, leased_until = NULL,
    updated_at = now()
WHERE id = $1
"""

_RECORD_SUCCESS_NONEMPTY = """
UPDATE job_board
SET consecutive_failures = 0,
    last_error = NULL,
    last_success_at = now(),
    next_check_at = now() + (check_interval_minutes || ' minutes')::interval,
    empty_check_count = 0,
    board_status = 'active',
    last_non_empty_at = now(),
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE id = $1
"""

_RECORD_EMPTY_CHECK = """
UPDATE job_board
SET consecutive_failures = 0,
    last_error = NULL,
    last_success_at = now(),
    next_check_at = now() + (check_interval_minutes || ' minutes')::interval,
    empty_check_count = empty_check_count + 1,
    board_status = CASE
        WHEN last_non_empty_at IS NOT NULL AND empty_check_count + 1 >= 6
        THEN 'gone'
        WHEN last_non_empty_at IS NOT NULL AND empty_check_count + 1 >= 3
        THEN 'suspect'
        ELSE board_status
    END,
    gone_at = CASE
        WHEN last_non_empty_at IS NOT NULL AND empty_check_count + 1 >= 6
        THEN now()
        ELSE gone_at
    END,
    is_enabled = CASE
        WHEN last_non_empty_at IS NOT NULL AND empty_check_count + 1 >= 6
        THEN false
        ELSE is_enabled
    END,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE id = $1
RETURNING board_status
"""

# RETURNING ``last_success_at`` + the post-update ``is_enabled`` lets
# the Python caller detect the single transition from enabled →
# disabled and delist postings when the board is truly stale. A
# recent-success board stays enabled as ``suspect`` after strike #5 so
# the scheduler retries it after the backoff instead of freezing active
# postings forever after a short provider outage.
_RECORD_FAILURE = """
UPDATE job_board
SET consecutive_failures = consecutive_failures + 1,
    last_error = $2,
    next_check_at = now() + LEAST(
        (5 * pow(2, consecutive_failures)) || ' minutes',
        '1440 minutes'
    )::interval,
    is_enabled = CASE
        WHEN consecutive_failures + 1 >= 5
         AND (last_success_at IS NULL OR last_success_at < now() - interval '24 hours')
        THEN false
        ELSE is_enabled
    END,
    board_status = CASE
        WHEN consecutive_failures + 1 >= 5
         AND (last_success_at IS NULL OR last_success_at < now() - interval '24 hours')
        THEN 'disabled'
        WHEN consecutive_failures + 1 >= 5
        THEN 'suspect'
        ELSE board_status
    END,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE id = $1
RETURNING is_enabled, last_success_at
"""

_DIFF_BATCH = """
WITH discovered AS (
  SELECT unnest($1::text[]) AS url
),
-- Self-heal touched rows (#2996, #4952): when a previously-stuck
-- rich-monitor posting (description_r2_hash IS NULL AND next_scrape_at
-- IS NULL) is re-scanned by a board that NOW has enrich
-- (is_rich_no_scrape = $3 = false), reset next_scrape_at = now() and
-- mark it for Redis enqueue so the scrape worker picks the row up.
-- Also mark already-due missing-content rows for enqueue: production
-- workers claim from Redis, not Postgres next_scrape_at, so a DB-due row
-- with a lost scrape ZSET entry otherwise stays active but unscraped
-- forever while monitor cycles only refresh last_seen_at.
-- Without this branch, scraper-config fixes shipped via PR
-- (e.g. #2947, #2953, #2954, #2961, #2962, #2964, #2967, #2968, #2970,
-- #2971, #2972) only affect FUTURE rows inserted via
-- ``_INSERT_RICH_JOB_ENRICH``; existing rows inserted via the no-enrich
-- ``_INSERT_RICH_JOB`` path stay stuck forever. Healthy rows
-- (description_r2_hash already set, OR next_scrape_at already
-- scheduled) are untouched. is_rich_no_scrape=true boards (rich
-- monitor without enrich) intentionally keep next_scrape_at = NULL —
-- the board delivers everything.
touched AS (
  UPDATE job_posting
  SET last_seen_at = now(),
      missing_count = 0,
      next_scrape_at = CASE
          WHEN NOT $3::boolean
               AND job_posting.description_r2_hash IS NULL
               AND job_posting.next_scrape_at IS NULL
          THEN now()
          ELSE job_posting.next_scrape_at
      END
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.is_active = true
    AND job_posting.source_url = d.url
  RETURNING job_posting.id,
            job_posting.source_url,
            job_posting.description_r2_hash,
            (
              NOT $3::boolean
              AND job_posting.description_r2_hash IS NULL
              AND job_posting.next_scrape_at <= now()
            ) AS needs_scrape_enqueue
),
relisted AS (
  UPDATE job_posting
  SET is_active = true, missing_count = 0,
      -- Reset scrape_failures so a previously scrape-tombstoned URL
      -- (queries/scrape.py: _RECORD_SCRAPE_FAILURE budget tombstone
      -- or _RECORD_SCRAPE_TRANSIENT budget exhaustion) gets a fresh
      -- budget on its next try. Without this, a relisted posting
      -- comes back with scrape_failures=3 and the next single
      -- failure re-tombstones it — a flap loop on chronically slow
      -- upstreams.
      scrape_failures = 0,
      last_seen_at = now(),
      next_scrape_at = CASE WHEN $3::boolean THEN NULL ELSE now() END
  FROM discovered d
  WHERE job_posting.board_id = $2
    AND job_posting.is_active = false
    AND job_posting.source_url = d.url
  RETURNING job_posting.id,
            job_posting.source_url,
            job_posting.description_r2_hash,
            false AS needs_scrape_enqueue
),
-- Cross-tenant URLs: the same source_url exists under another board
-- (e.g. ByteDance/TikTok share jobs.bytedance.com, Glencore reaches
-- GCAA's Workday tenant). Refresh the owning row's last_seen_at so
-- _MARK_GONE_BY_TIMESTAMP on the OWNING board doesn't tombstone jobs
-- that are still live via a secondary board. Excluded from new_urls
-- below so we don't chase an impossible INSERT every cycle.
--
-- We deliberately DO NOT gate this on is_active=true: refreshing
-- last_seen_at on an inactive foreign row is harmless (mark_gone only
-- operates on active rows) and prevents the URL from falling into an
-- invisible bucket where it appears in neither new_urls, touched,
-- relisted, nor foreign_touched.
foreign_touched AS (
  UPDATE job_posting
  SET last_seen_at = now()
  FROM discovered d
  WHERE job_posting.source_url = d.url
    AND job_posting.board_id != $2
  RETURNING job_posting.source_url
),
new_urls AS (
  SELECT d.url
  FROM discovered d
  WHERE NOT EXISTS (
    SELECT 1 FROM job_posting jp
    WHERE jp.source_url = d.url
  )
)
SELECT 'touched' AS action,
       id::text,
       source_url AS url,
       description_r2_hash,
       needs_scrape_enqueue
FROM touched
UNION ALL
SELECT 'relisted' AS action,
       id::text,
       source_url AS url,
       description_r2_hash,
       needs_scrape_enqueue
FROM relisted
UNION ALL
-- id is NULL for foreign rows: the owning board's id has no meaning
-- for the calling board, and the Python layer only counts this action.
SELECT 'foreign' AS action,
       NULL::text,
       source_url AS url,
       NULL::bigint,
       false AS needs_scrape_enqueue
FROM foreign_touched
UNION ALL
SELECT 'new',
       NULL,
       url,
       NULL::bigint,
       false AS needs_scrape_enqueue
FROM new_urls
"""

_MARK_GONE = """
WITH discovered AS (
  SELECT unnest($1::text[]) AS url
)
UPDATE job_posting
SET missing_count = missing_count + 1,
    is_active = CASE
        WHEN missing_count + 1 >= $3 THEN false
        ELSE is_active
    END,
    next_scrape_at = CASE
        WHEN missing_count + 1 >= $3 THEN NULL
        ELSE next_scrape_at
    END,
    updated_at = CASE
        WHEN missing_count + 1 >= $3 THEN now()
        ELSE updated_at
    END
WHERE job_posting.board_id = $2
  AND job_posting.is_active = true
  AND job_posting.source_url NOT IN (SELECT url FROM discovered)
RETURNING job_posting.id, job_posting.source_url
"""

# Monitor-side delisting authority — primary half of the dual-authority
# delisting model. See docs/03-crawler-architecture.md "Delisting model
# — when is a posting 'gone'?" for the full design and the relationship
# with the scrape-side fallback (queries/scrape.py: _RECORD_SCRAPE_FAILURE
# for tombstoning failures, _RECORD_SCRAPE_TRANSIENT for non-tombstoning
# failures — naming preserved for git-blame continuity, but both record
# failures; the difference is whether they touch is_active).
_MARK_GONE_BY_TIMESTAMP = """
UPDATE job_posting
SET missing_count = missing_count + 1,
    is_active = CASE
        WHEN missing_count + 1 >= $3 THEN false
        ELSE is_active
    END,
    next_scrape_at = CASE
        WHEN missing_count + 1 >= $3 THEN NULL
        ELSE next_scrape_at
    END,
    updated_at = CASE
        WHEN missing_count + 1 >= $3 THEN now()
        ELSE updated_at
    END
WHERE board_id = $1
  AND is_active = true
  AND last_seen_at < $2
RETURNING id, source_url
"""

_EXTEND_BOARD_LEASE = """
UPDATE job_board
SET leased_until = now() + interval '10 minutes'
WHERE id = $1
"""

_INSERT_RICH_JOB = """
INSERT INTO job_posting
    (company_id, board_id,
     employment_type, source_url,
     first_seen_at, last_seen_at,
     is_active, titles, locales,
     location_ids, location_types,
     salary_min, salary_max, salary_currency, salary_period, salary_eur,
     experience_min, experience_max, technology_ids,
     occupation_id, seniority_id)
VALUES ($1, $2, $3, $4,
        now(), now(),
        true, $5, $6,
        $7, $8,
        $9, $10, $11, $12, $13,
        $14, $15, $16,
        $17, $18)
ON CONFLICT (source_url) DO NOTHING
RETURNING id
"""

_INSERT_RICH_JOB_ENRICH = """
INSERT INTO job_posting
    (company_id, board_id,
     employment_type, source_url,
     first_seen_at, last_seen_at, next_scrape_at,
     is_active, titles, locales,
     location_ids, location_types,
     salary_min, salary_max, salary_currency, salary_period, salary_eur,
     experience_min, experience_max, technology_ids,
     occupation_id, seniority_id)
VALUES ($1, $2, $3, $4,
        now(), now(), now(),
        true, $5, $6,
        $7, $8,
        $9, $10, $11, $12, $13,
        $14, $15, $16,
        $17, $18)
ON CONFLICT (source_url) DO NOTHING
RETURNING id
"""

_CREATE_RICH_UPDATES_TEMP = """
CREATE TEMP TABLE _rich_updates (
    id uuid,
    employment_type text,
    titles text[], locales text[],
    location_ids integer[], location_types text[],
    salary_min integer, salary_max integer,
    salary_currency text, salary_period text, salary_eur integer,
    experience_min numeric(3,1), experience_max numeric(3,1),
    technology_ids integer[],
    occupation_id integer, seniority_id integer
) ON COMMIT DROP
"""

_BATCH_UPDATE_RICH_CONTENT = """
UPDATE job_posting AS jp
SET employment_type = u.employment_type,
    titles = u.titles, locales = u.locales,
    location_ids = u.location_ids, location_types = u.location_types,
    salary_min = u.salary_min, salary_max = u.salary_max,
    salary_currency = u.salary_currency, salary_period = u.salary_period,
    salary_eur = u.salary_eur,
    experience_min = CASE
        WHEN u.experience_min IS NULL AND u.experience_max IS NULL
        THEN jp.experience_min
        ELSE u.experience_min
    END,
    experience_max = CASE
        WHEN u.experience_min IS NULL AND u.experience_max IS NULL
        THEN jp.experience_max
        ELSE u.experience_max
    END,
    technology_ids = COALESCE(u.technology_ids, jp.technology_ids),
    occupation_id = COALESCE(u.occupation_id, jp.occupation_id),
    seniority_id = COALESCE(u.seniority_id, jp.seniority_id)
FROM _rich_updates u
WHERE jp.id = u.id
"""

_INSERT_URL_ONLY_JOBS = """
INSERT INTO job_posting (company_id, board_id, source_url,
                         first_seen_at, last_seen_at, next_scrape_at,
                         is_active, titles, locales)
SELECT $1, $2, u.url, now(), now(),
       CASE WHEN $4::boolean THEN NULL ELSE now() END,
       true, '{}', '{}'
FROM unnest($3::text[]) AS u(url)
ON CONFLICT (source_url) DO NOTHING
RETURNING id, source_url
"""

_UPDATE_METADATA = """
UPDATE job_board
SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
    updated_at = now()
WHERE id = $1
"""

_UPSERT_LOCATION_MISSES = """
INSERT INTO taxonomy_miss (taxonomy, raw_value, sample_value)
SELECT 'location', * FROM unnest($1::text[], $2::text[])
ON CONFLICT (taxonomy, raw_value) DO UPDATE SET
    hit_count = taxonomy_miss.hit_count + 1,
    last_seen_at = now()
WHERE taxonomy_miss.status = 'pending'
"""
