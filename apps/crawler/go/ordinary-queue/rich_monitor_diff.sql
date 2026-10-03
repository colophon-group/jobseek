
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
--
-- scrape_failures >= 3 is the transient retry tombstone written by
-- _RECORD_SCRAPE_TRANSIENT. A monitor touch is liveness evidence for the
-- posting, not evidence that the detail scraper recovered, so it must not
-- reset that terminal state. Recovery remains explicit through
-- ``crawler retry-stalled-scrapes`` (or a true relist, which resets the
-- failure budget below).
touched AS (
  UPDATE job_posting
  SET last_seen_at = now(),
      missing_count = 0,
      next_scrape_at = CASE
          WHEN NOT $3::boolean
               AND job_posting.description_r2_hash IS NULL
               AND job_posting.next_scrape_at IS NULL
               AND job_posting.scrape_failures < 3
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
              AND job_posting.scrape_failures < 3
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
