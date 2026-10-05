WITH discovered AS MATERIALIZED (
  SELECT input.*, $4::uuid AS discovering_company_id
  FROM unnest($1::text[], $2::text[], $3::boolean[])
       AS input(source_identity, source_url, explicit_identity)
),
locked_existing AS MATERIALIZED (
  SELECT posting.id,
         posting.company_id,
         posting.source_identity,
         posting.source_url,
         posting.board_id,
         posting.is_active
  FROM job_posting posting
  JOIN discovered d
    ON d.source_identity = posting.source_identity
  ORDER BY posting.id
  FOR UPDATE OF posting
),
promoted_alias AS (
  DELETE FROM job_posting_source_alias alias
  USING locked_existing locked, discovered d
  WHERE locked.source_identity = d.source_identity
    AND alias.posting_id = locked.id
    AND alias.source_url = d.source_url
  RETURNING alias.source_url
),
archived_alias AS (
  INSERT INTO job_posting_source_alias (
      source_url, posting_id, first_observed_at, last_observed_at
  )
  SELECT locked.source_url, locked.id, now(), now()
  FROM locked_existing locked
  JOIN discovered d USING (source_identity)
  WHERE d.explicit_identity
    AND locked.source_url <> d.source_url
    AND (SELECT count(*) FROM promoted_alias) >= 0
  ON CONFLICT (source_url) DO UPDATE
  SET last_observed_at = EXCLUDED.last_observed_at
  WHERE job_posting_source_alias.posting_id = EXCLUDED.posting_id
  RETURNING source_url
),
touched AS (
  UPDATE job_posting
  SET source_url = d.source_url,
      last_seen_at = now(),
      missing_count = 0,
      next_scrape_at = CASE
          WHEN NOT $6::boolean
               AND job_posting.description_r2_hash IS NULL
               AND job_posting.next_scrape_at IS NULL
               AND job_posting.scrape_failures < 3
          THEN now()
          ELSE job_posting.next_scrape_at
      END
  FROM locked_existing locked
  JOIN discovered d USING (source_identity)
  WHERE job_posting.id = locked.id
    AND locked.board_id = $5
    AND locked.is_active = true
    AND (SELECT count(*) FROM archived_alias) >= 0
  RETURNING job_posting.id,
            job_posting.source_identity,
            job_posting.source_url,
            job_posting.description_r2_hash,
            (
              NOT $6::boolean
              AND job_posting.description_r2_hash IS NULL
              AND job_posting.next_scrape_at <= now()
              AND job_posting.scrape_failures < 3
            ) AS needs_scrape_enqueue
),
relisted AS (
  UPDATE job_posting
  SET source_url = d.source_url,
      is_active = true,
      missing_count = 0,
      scrape_failures = 0,
      last_seen_at = now(),
      updated_at = now(),
      next_scrape_at = CASE WHEN $6::boolean THEN NULL ELSE now() END
  FROM locked_existing locked
  JOIN discovered d USING (source_identity)
  WHERE job_posting.id = locked.id
    AND locked.board_id = $5
    AND locked.is_active = false
    AND (SELECT count(*) FROM archived_alias) >= 0
  RETURNING job_posting.id,
            job_posting.source_identity,
            job_posting.source_url,
            job_posting.description_r2_hash,
            false AS needs_scrape_enqueue
),
foreign_relisted AS (
  UPDATE job_posting
  SET source_url = d.source_url,
      is_active = true,
      missing_count = 0,
      scrape_failures = 0,
      last_seen_at = now(),
      updated_at = now(),
      next_scrape_at = CASE WHEN $6::boolean THEN NULL ELSE now() END
  FROM locked_existing locked
  JOIN discovered d USING (source_identity)
  WHERE job_posting.id = locked.id
    AND locked.board_id != $5
    AND locked.is_active = false
    AND (SELECT count(*) FROM archived_alias) >= 0
  RETURNING job_posting.id,
            job_posting.source_identity,
            job_posting.source_url,
            job_posting.description_r2_hash,
            false AS needs_scrape_enqueue
),
foreign_touched AS (
  UPDATE job_posting
  SET source_url = d.source_url,
      last_seen_at = now(),
      missing_count = 0
  FROM locked_existing locked
  JOIN discovered d USING (source_identity)
  WHERE job_posting.id = locked.id
    AND locked.board_id != $5
    AND locked.is_active = true
    AND (SELECT count(*) FROM archived_alias) >= 0
  RETURNING job_posting.source_identity, job_posting.source_url
),
new_sources AS (
  SELECT d.source_identity, d.source_url
  FROM discovered d
  WHERE NOT EXISTS (
    SELECT 1
    FROM locked_existing locked
    WHERE locked.source_identity = d.source_identity
  )
)
SELECT 'touched' AS action,
       id::text,
       source_identity,
       source_url AS url,
       description_r2_hash,
       needs_scrape_enqueue
FROM touched
UNION ALL
SELECT 'relisted', id::text, source_identity, source_url,
       description_r2_hash, needs_scrape_enqueue
FROM relisted
UNION ALL
SELECT 'foreign_relisted', id::text, source_identity, source_url,
       description_r2_hash, needs_scrape_enqueue
FROM foreign_relisted
UNION ALL
SELECT 'foreign', NULL::text, source_identity, source_url,
       NULL::bigint, false
FROM foreign_touched
UNION ALL
SELECT 'new', NULL::text, source_identity, source_url,
       NULL::bigint, false
FROM new_sources
