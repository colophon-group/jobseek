"""SQL queries for monitor (board) operations."""

from __future__ import annotations

__all__ = [
    "_BATCH_UPDATE_RICH_CONTENT",
    "_BLAST_RADIUS_FLOOR_DEFAULT",
    "_RETIRE_CANONICALIZED_PROVIDER_IDENTITIES",
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
    "_FETCH_BOARD_GONE_STATE",
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
    AND board_status IN ('active', 'suspect', 'quarantined', 'gone_pending', 'gone')
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
    COUNT(*) AS active,
    COUNT(*) FILTER (WHERE last_seen_at < $2) AS missing
FROM job_posting
WHERE board_id = $1
  AND is_active = true
"""

# Narrow, receipt-backed identity-migration lane. The caller supplies only
# code-owned URL contracts after validating the exact board configuration.
# Every active row owned by the board is locked and classified before any
# write. The exact canonical URL set discovered by this cycle is independently
# checked against active, same-company rows touched since the cycle began.
# Unknown board-owned sources, an invalid/incomplete discovery set, or a legacy
# count over the hard cap makes both UPDATEs affect zero rows. Every strict
# legacy row is then retired, including stale rows for jobs no longer present in
# the current discovery. Retirement and the durable job_board receipt commit
# atomically in the surrounding normal board-success transaction.
_RETIRE_CANONICALIZED_PROVIDER_IDENTITIES = """
WITH board_state AS MATERIALIZED (
  SELECT metadata -> '_identity_migration_receipt' AS existing_receipt
  FROM job_board
  WHERE id = $1
    AND company_id = $2
  FOR UPDATE
), owner AS MATERIALIZED (
  SELECT id
  FROM company
  WHERE id = $2
    AND slug = 'merck'
), discovered_input AS MATERIALIZED (
  SELECT source_url
  FROM unnest($5::text[]) AS input(source_url)
), discovered_input_state AS MATERIALIZED (
  SELECT COUNT(*) AS input_count,
         COUNT(DISTINCT source_url) AS unique_count,
         COUNT(*) FILTER (WHERE source_url ~ $7) AS canonical_count
  FROM discovered_input
), validated_discoveries AS MATERIALIZED (
  SELECT posting.id,
         posting.source_url
  FROM discovered_input AS input
  JOIN job_posting AS posting
    ON posting.source_url = input.source_url
  JOIN owner ON owner.id = posting.company_id
  WHERE posting.company_id = $2
    AND posting.is_active = true
    AND posting.last_seen_at >= $3
    AND posting.source_url ~ $7
), active_sources AS MATERIALIZED (
  SELECT posting.id,
         posting.company_id,
         posting.source_url,
         posting.last_seen_at,
         CASE
           WHEN posting.source_url ~ $6 THEN 'legacy'
           WHEN posting.source_url ~ $7 THEN 'canonical'
           ELSE 'unknown'
         END AS source_kind
  FROM job_posting AS posting
  JOIN owner ON owner.id = posting.company_id
  JOIN board_state ON board_state.existing_receipt IS NULL
  WHERE posting.board_id = $1
    AND posting.company_id = $2
    AND posting.is_active = true
  FOR UPDATE OF posting
), classified AS MATERIALIZED (
  SELECT COUNT(*) AS active_count,
         COUNT(*) FILTER (WHERE source_kind = 'legacy') AS legacy_count,
         COUNT(*) FILTER (WHERE source_kind = 'canonical') AS canonical_count,
         COUNT(*) FILTER (WHERE source_kind = 'unknown') AS unknown_count
  FROM active_sources
), migration_state AS MATERIALIZED (
  SELECT classified.*,
         discovered_input_state.input_count AS discovered_count,
         COUNT(validated_discoveries.id) AS validated_count,
         EXISTS (SELECT 1 FROM owner)
           AND EXISTS (
             SELECT 1 FROM board_state WHERE existing_receipt IS NULL
           )
           AND discovered_input_state.input_count > 0
           AND discovered_input_state.input_count <= $4
           AND discovered_input_state.unique_count = discovered_input_state.input_count
           AND discovered_input_state.canonical_count = discovered_input_state.input_count
           AND COUNT(validated_discoveries.id) = discovered_input_state.input_count
           AND classified.unknown_count = 0
           AND classified.legacy_count <= $4 AS may_migrate
  FROM classified, discovered_input_state
  LEFT JOIN validated_discoveries ON true
  GROUP BY classified.active_count,
           classified.legacy_count,
           classified.canonical_count,
           classified.unknown_count,
           discovered_input_state.input_count,
           discovered_input_state.unique_count,
           discovered_input_state.canonical_count
), retired AS (
  UPDATE job_posting AS posting
  SET is_active = false,
      missing_count = missing_count + 1,
      next_scrape_at = NULL,
      updated_at = now()
  FROM active_sources, migration_state
  WHERE posting.id = active_sources.id
    AND active_sources.source_kind = 'legacy'
    AND migration_state.may_migrate
  RETURNING posting.id
), retired_state AS MATERIALIZED (
  SELECT migration_state.*,
         COUNT(retired.id) AS retired_count
  FROM migration_state
  LEFT JOIN retired ON true
  GROUP BY migration_state.active_count,
           migration_state.legacy_count,
           migration_state.canonical_count,
           migration_state.unknown_count,
           migration_state.discovered_count,
           migration_state.validated_count,
           migration_state.may_migrate
), receipt AS (
  UPDATE job_board AS board
  SET metadata = COALESCE(board.metadata, '{}'::jsonb)
                 || jsonb_build_object(
                      '_identity_migration_receipt',
                      $8::jsonb || jsonb_build_object(
                        'completed_at', clock_timestamp(),
                        'retired_count', retired_state.retired_count
                      )
                    ),
      updated_at = now()
  FROM retired_state
  WHERE board.id = $1
    AND board.company_id = $2
    AND EXISTS (
      SELECT 1 FROM board_state WHERE existing_receipt IS NULL
    )
    AND retired_state.may_migrate
    AND retired_state.retired_count = retired_state.legacy_count
  RETURNING board.id
)
SELECT retired_state.active_count AS active,
       retired_state.legacy_count AS legacy,
       retired_state.canonical_count AS canonical,
       retired_state.unknown_count AS unknown,
       retired_state.discovered_count AS discovered,
       retired_state.validated_count AS validated,
       retired_state.retired_count AS retired,
       EXISTS (SELECT 1 FROM receipt) AS receipt_written,
       (SELECT existing_receipt FROM board_state) AS existing_receipt
FROM retired_state
"""

_DELIST_BOARD_POSTINGS = """
UPDATE job_posting
SET is_active = false, next_scrape_at = NULL, updated_at = now()
WHERE board_id = $1 AND is_active = true
RETURNING id
"""

_FETCH_BOARD_GONE_STATE = """
SELECT board_status,
       gone_confirmation_count,
       gone_first_confirmed_at,
       gone_last_confirmed_at,
       last_success_at,
       gone_at
FROM job_board
WHERE id = $1
FOR UPDATE
"""

_RECORD_BOARD_GONE = """
UPDATE job_board
SET board_status = $2,
    gone_confirmation_count = $3,
    gone_first_confirmed_at = $4,
    gone_last_confirmed_at = $5,
    gone_at = $6,
    next_check_at = $7,
    last_error = $8,
    last_gone_error = $8,
    last_gone_endpoint = $9,
    last_gone_status = $10,
    gone_transition_count = gone_transition_count + CASE WHEN $11 THEN 1 ELSE 0 END,
    consecutive_failures = 0,
    is_enabled = true,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE id = $1
RETURNING board_status, gone_confirmation_count, next_check_at
"""

_RECORD_SUCCESS_NONEMPTY = """
WITH previous AS MATERIALIZED (
    SELECT id, board_status
    FROM job_board
    WHERE id = $1
    FOR UPDATE
), updated AS (
    UPDATE job_board jb
    SET consecutive_failures = 0,
        last_error = NULL,
        last_success_at = now(),
        next_check_at = now() + (check_interval_minutes || ' minutes')::interval,
        empty_check_count = 0,
        board_status = 'active',
        is_enabled = true,
        last_non_empty_at = now(),
        last_recovered_at = CASE
            WHEN previous.board_status IN ('quarantined', 'gone_pending', 'gone') THEN now()
            ELSE jb.last_recovered_at
        END,
        recovery_count = jb.recovery_count + CASE
            WHEN previous.board_status IN ('quarantined', 'gone_pending', 'gone') THEN 1
            ELSE 0
        END,
        gone_recovery_count = jb.gone_recovery_count + CASE
            WHEN previous.board_status IN ('gone_pending', 'gone') THEN 1
            ELSE 0
        END,
        gone_confirmation_count = 0,
        gone_at = NULL,
        quarantined_at = NULL,
        quarantine_probe_count = 0,
        lease_owner = NULL,
        leased_until = NULL,
        updated_at = now()
    FROM previous
    WHERE jb.id = previous.id
    RETURNING CASE
        WHEN previous.board_status IN ('gone_pending', 'gone') THEN 'provider_gone'
        WHEN previous.board_status = 'quarantined' THEN 'quarantined'
        ELSE NULL
    END AS recovered_from
)
SELECT recovered_from FROM updated
"""

_RECORD_EMPTY_CHECK = """
WITH previous AS MATERIALIZED (
    SELECT id, board_status
    FROM job_board
    WHERE id = $1
    FOR UPDATE
), updated AS (
    UPDATE job_board jb
    SET consecutive_failures = 0,
        last_error = NULL,
        last_success_at = now(),
        next_check_at = now() + (check_interval_minutes || ' minutes')::interval,
        empty_check_count = jb.empty_check_count + 1,
        board_status = CASE
            WHEN jb.last_non_empty_at IS NOT NULL AND jb.empty_check_count + 1 >= 3
            THEN 'suspect'
            WHEN previous.board_status IN ('quarantined', 'gone_pending', 'gone') THEN 'active'
            ELSE jb.board_status
        END,
        is_enabled = true,
        last_recovered_at = CASE
            WHEN previous.board_status IN ('quarantined', 'gone_pending', 'gone') THEN now()
            ELSE jb.last_recovered_at
        END,
        recovery_count = jb.recovery_count + CASE
            WHEN previous.board_status IN ('quarantined', 'gone_pending', 'gone') THEN 1
            ELSE 0
        END,
        gone_recovery_count = jb.gone_recovery_count + CASE
            WHEN previous.board_status IN ('gone_pending', 'gone') THEN 1
            ELSE 0
        END,
        gone_confirmation_count = 0,
        gone_at = NULL,
        quarantined_at = NULL,
        quarantine_probe_count = 0,
        lease_owner = NULL,
        leased_until = NULL,
        updated_at = now()
    FROM previous
    WHERE jb.id = previous.id
    RETURNING
        jb.board_status,
        jb.empty_check_count >= 6 AS should_delist,
        CASE
            WHEN previous.board_status IN ('gone_pending', 'gone') THEN 'provider_gone'
            WHEN previous.board_status = 'quarantined' THEN 'quarantined'
            ELSE NULL
        END AS recovered_from
)
SELECT board_status, should_delist, recovered_from FROM updated
"""

# Ordinary integration and upstream failures enter a recoverable quarantine
# after strike five.  The board remains enabled and the same exponential
# schedule reaches a daily ceiling, so a code/config/provider fix can recover
# without SQL while Redis domain throttles continue to bound pressure.
# Keep both the exponent and the interval input numeric: comparing minute
# strings makes LEAST lexical and can select an interval too large to persist.
_RECORD_FAILURE = """
WITH previous AS MATERIALIZED (
    SELECT
        id,
        board_status,
        consecutive_failures,
        LEAST(consecutive_failures::bigint + 1, 2147483647)::integer
            AS next_failure_count
    FROM job_board
    WHERE id = $1
    FOR UPDATE
), updated AS (
    UPDATE job_board jb
    SET consecutive_failures = previous.next_failure_count,
        last_error = $2,
        next_check_at = now() + LEAST(
            5 * pow(2, LEAST(jb.consecutive_failures, 9)),
            1440
        ) * interval '1 minute',
        is_enabled = true,
        board_status = CASE
            WHEN previous.next_failure_count >= 5 THEN 'quarantined'
            ELSE jb.board_status
        END,
        quarantined_at = CASE
            WHEN previous.next_failure_count >= 5
            THEN COALESCE(jb.quarantined_at, now())
            ELSE jb.quarantined_at
        END,
        last_quarantined_at = CASE
            WHEN previous.next_failure_count >= 5
             AND previous.board_status IS DISTINCT FROM 'quarantined'
            THEN now()
            ELSE jb.last_quarantined_at
        END,
        last_quarantine_error = CASE
            WHEN previous.next_failure_count >= 5 THEN $2
            ELSE jb.last_quarantine_error
        END,
        quarantine_probe_count = CASE
            WHEN previous.next_failure_count >= 5
            THEN CASE
                WHEN previous.board_status = 'quarantined' THEN jb.quarantine_probe_count + 1
                ELSE 1
            END
            ELSE jb.quarantine_probe_count
        END,
        lease_owner = NULL,
        leased_until = NULL,
        updated_at = now()
    FROM previous
    WHERE jb.id = previous.id
    RETURNING
        jb.is_enabled,
        jb.last_success_at,
        jb.board_status,
        jb.quarantined_at,
        previous.board_status IS DISTINCT FROM 'quarantined'
            AND jb.board_status = 'quarantined' AS entered_quarantine
)
SELECT * FROM updated
"""

_DIFF_BATCH = """
WITH discovered AS (
  SELECT unnest($1::text[]) AS url
),
-- A batch can contain both rows owned by this board and cross-board
-- duplicates owned by another board.  The old data-modifying CTEs locked
-- those groups independently (own rows in touched/relisted, foreign rows in
-- foreign_touched).  Concurrent boards that discovered each other's URLs
-- could therefore lock A -> B and B -> A and deadlock (#5103).
--
-- Lock every existing match exactly once, in a global order, before any CTE
-- updates it.  MATERIALIZED is intentional: every write below depends on the
-- completed lock set rather than letting the planner inline/reorder the scan.
locked_existing AS MATERIALIZED (
  SELECT jp.id, jp.source_url, jp.board_id, jp.is_active
  FROM job_posting jp
  JOIN discovered d ON d.url = jp.source_url
  ORDER BY jp.id
  FOR UPDATE OF jp
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
  FROM locked_existing locked
  WHERE job_posting.id = locked.id
    AND locked.board_id = $2
    AND locked.is_active = true
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
      -- Relisting changes a user-visible CDC field.  The exporter reads by
      -- (updated_at, id), so failing to advance updated_at leaves Supabase
      -- and Typesense permanently behind once their cursors have passed the
      -- posting's old timestamp.
      updated_at = now(),
      next_scrape_at = CASE WHEN $3::boolean THEN NULL ELSE now() END
  FROM locked_existing locked
  WHERE job_posting.id = locked.id
    AND locked.board_id = $2
    AND locked.is_active = false
  RETURNING job_posting.id,
            job_posting.source_url,
            job_posting.description_r2_hash,
            false AS needs_scrape_enqueue
),
-- A foreign-board discovery is liveness evidence for the globally canonical
-- posting. Preserve the first owner's company_id/board_id deterministically:
-- a shared ATS URL is not enough evidence to transfer company attribution.
-- But an inactive canonical row must be recoverable when a sibling or shared
-- tenant still lists it (#6159). Reset the same state as the owning-board
-- relisted path, advance the exported timestamp through the CDC trigger, and
-- return the canonical id so the discovering board can refresh its content.
foreign_relisted AS (
  UPDATE job_posting
  SET is_active = true,
      missing_count = 0,
      scrape_failures = 0,
      last_seen_at = now(),
      updated_at = now(),
      next_scrape_at = CASE WHEN $3::boolean THEN NULL ELSE now() END
  FROM locked_existing locked
  WHERE job_posting.id = locked.id
    AND locked.board_id != $2
    AND locked.is_active = false
  RETURNING job_posting.id,
            job_posting.source_url,
            job_posting.description_r2_hash,
            false AS needs_scrape_enqueue
),
-- Active foreign matches remain owned by their canonical board. Refreshing
-- last_seen_at prevents a concurrent owner cycle from treating the URL as
-- unseen, while clearing missing_count requires a full new confirmation
-- window before a later owner-only absence can tombstone it.
foreign_touched AS (
  UPDATE job_posting
  SET last_seen_at = now(),
      missing_count = 0
  FROM locked_existing locked
  WHERE job_posting.id = locked.id
    AND locked.board_id != $2
    AND locked.is_active = true
  RETURNING job_posting.source_url
),
new_urls AS (
  SELECT d.url
  FROM discovered d
  WHERE NOT EXISTS (
    SELECT 1 FROM locked_existing locked
    WHERE locked.source_url = d.url
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
SELECT 'foreign_relisted' AS action,
       id::text,
       source_url AS url,
       description_r2_hash,
       needs_scrape_enqueue
FROM foreign_relisted
UNION ALL
-- Active foreign rows need no content refresh, so only their count is
-- returned to the caller. Inactive foreign rows above return the canonical id
-- because relisting does require the discovering board's refresh path.
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
