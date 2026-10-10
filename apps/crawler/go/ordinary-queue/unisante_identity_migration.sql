
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
    AND slug = 'unisante'
), discovered_input AS MATERIALIZED (
  SELECT source_identity,
         detail_url,
         regexp_replace(
           source_identity,
           '^unisante:emploi:',
           ''
         ) AS provider_reference
  FROM unnest($3::text[], $4::text[]) AS input(source_identity, detail_url)
), discovered_state AS MATERIALIZED (
  SELECT COUNT(*) AS input_count,
         COUNT(DISTINCT source_identity) AS canonical_count,
         COUNT(DISTINCT detail_url) AS detail_count,
         COUNT(*) FILTER (
           WHERE source_identity ~ '^unisante:emploi:[1-9][0-9]{0,8}$'
             AND detail_url ~
             '^https://emploi[.]unisante[.]ch/index[.]php/offre/[a-z0-9]+(-[a-z0-9]+)*/?$'
         ) AS valid_count
  FROM discovered_input
), locked_rows AS MATERIALIZED (
  SELECT posting.id,
         posting.company_id,
         posting.board_id,
         posting.source_identity,
         posting.source_url,
         posting.is_active,
         posting.first_seen_at,
         CASE
           WHEN posting.source_identity IN (
             SELECT source_identity FROM discovered_input
           )
           THEN 'canonical'
           WHEN posting.source_identity = posting.source_url
            AND posting.source_url ~
             '^https://emploi[.]unisante[.]ch/(index[.]php/)?offre/[a-z0-9]+(-[a-z0-9]+)*/?$'
           THEN 'legacy'
           ELSE 'unknown'
         END AS source_kind
  FROM job_posting AS posting
  WHERE (posting.board_id = $1 AND posting.company_id = $2)
     OR posting.source_identity IN (SELECT source_identity FROM discovered_input)
     OR posting.source_url IN (SELECT detail_url FROM discovered_input)
  ORDER BY posting.id
  FOR UPDATE OF posting
), active_state AS MATERIALIZED (
  SELECT COUNT(*) AS active_count,
         COUNT(*) FILTER (WHERE source_kind = 'legacy') AS legacy_count,
         COUNT(*) FILTER (WHERE source_kind = 'canonical') AS canonical_count,
         COUNT(*) FILTER (WHERE source_kind = 'unknown') AS unknown_count
  FROM locked_rows
  WHERE board_id = $1
    AND company_id = $2
    AND is_active = true
), canonical_conflicts AS MATERIALIZED (
  SELECT COUNT(*) AS conflict_count
  FROM locked_rows
  WHERE (
        source_identity IN (SELECT source_identity FROM discovered_input)
        OR source_url IN (SELECT detail_url FROM discovered_input)
      )
    AND (board_id <> $1 OR company_id <> $2)
), canonical_existing AS MATERIALIZED (
  SELECT input.provider_reference,
         row.id,
         row.source_identity,
         row.source_url,
         row.is_active
  FROM discovered_input AS input
  JOIN locked_rows AS row ON row.source_identity = input.source_identity
  WHERE row.board_id = $1
    AND row.company_id = $2
), legacy_candidates AS MATERIALIZED (
  SELECT input.provider_reference,
         input.source_identity,
         input.detail_url,
         row.id,
         row.source_url,
         ROW_NUMBER() OVER (
           PARTITION BY input.provider_reference
           ORDER BY
             (rtrim(row.source_url, '/') = rtrim(input.detail_url, '/')) DESC,
             row.first_seen_at,
             row.id
         ) AS candidate_rank
  FROM discovered_input AS input
  JOIN locked_rows AS row
    ON row.board_id = $1
   AND row.company_id = $2
   AND row.is_active = true
   AND row.source_kind = 'legacy'
   AND (
        rtrim(row.source_url, '/') = rtrim(input.detail_url, '/')
        OR rtrim(row.source_url, '/') = rtrim(
             replace(input.detail_url, '/index.php/offre/', '/offre/'),
             '/'
           )
        OR substring(
             row.source_url
             FROM '/(?:index[.]php/)?offre/([1-9][0-9]*)-'
           ) = input.provider_reference
   )
), migration_state AS MATERIALIZED (
  SELECT active_state.*,
         discovered_state.input_count,
         discovered_state.canonical_count AS discovered_canonical_count,
         discovered_state.detail_count AS discovered_detail_count,
         discovered_state.valid_count,
         canonical_conflicts.conflict_count,
         EXISTS (SELECT 1 FROM owner)
           AND EXISTS (
             SELECT 1 FROM board_state WHERE existing_receipt IS NULL
           )
           AND discovered_state.input_count > 0
           AND discovered_state.input_count <= $5
           AND discovered_state.canonical_count = discovered_state.input_count
           AND discovered_state.detail_count = discovered_state.input_count
           AND discovered_state.valid_count = discovered_state.input_count
           AND active_state.active_count <= $5
           AND active_state.canonical_count = 0
           AND active_state.unknown_count = 0
           AND canonical_conflicts.conflict_count = 0 AS may_migrate
  FROM active_state, discovered_state, canonical_conflicts
), updated_in_place AS (
  UPDATE job_posting AS posting
  SET source_identity = candidate.source_identity,
      updated_at = now()
  FROM legacy_candidates AS candidate, migration_state
  WHERE posting.id = candidate.id
    AND candidate.candidate_rank = 1
    AND migration_state.may_migrate
    AND NOT EXISTS (
      SELECT 1
      FROM canonical_existing AS canonical
      WHERE canonical.provider_reference = candidate.provider_reference
  )
  RETURNING posting.id
), aliases_to_retire AS MATERIALIZED (
  SELECT candidate.id
  FROM legacy_candidates AS candidate
  UNION
  SELECT row.id
  FROM locked_rows AS row
  WHERE row.board_id = $1
    AND row.company_id = $2
    AND row.is_active = true
    AND row.source_kind = 'legacy'
    AND NOT EXISTS (
      SELECT 1 FROM legacy_candidates AS candidate WHERE candidate.id = row.id
    )
), retired_aliases AS (
  UPDATE job_posting AS posting
  SET is_active = false,
      missing_count = missing_count + 1,
      next_scrape_at = NULL,
      updated_at = now()
  FROM aliases_to_retire AS alias, migration_state
  WHERE posting.id = alias.id
    AND migration_state.may_migrate
    AND NOT EXISTS (
      SELECT 1 FROM updated_in_place AS updated WHERE updated.id = posting.id
    )
  RETURNING posting.id
), transition_state AS MATERIALIZED (
  SELECT migration_state.*,
         (SELECT COUNT(*) FROM legacy_candidates) AS candidate_count,
         (SELECT COUNT(*) FROM canonical_existing) AS existing_canonical_count,
         (SELECT COUNT(*) FROM updated_in_place) AS updated_count,
         (SELECT COUNT(*) FROM retired_aliases) AS retired_count
  FROM migration_state
), receipt AS (
  UPDATE job_board AS board
  SET metadata = COALESCE(board.metadata, '{}'::jsonb)
                 || jsonb_build_object(
                      '_identity_migration_receipt',
                      $6::jsonb || jsonb_build_object(
                        'completed_at', clock_timestamp(),
                        'updated_count', transition_state.updated_count,
                        'retired_count', transition_state.retired_count
                      )
                    ),
      updated_at = now()
  FROM transition_state
  WHERE board.id = $1
    AND board.company_id = $2
    AND transition_state.may_migrate
    AND transition_state.updated_count + transition_state.retired_count =
        transition_state.legacy_count
  RETURNING board.id
)
SELECT (SELECT existing_receipt FROM board_state) AS existing_receipt,
       transition_state.active_count AS active,
       transition_state.legacy_count AS legacy,
       transition_state.canonical_count AS canonical,
       transition_state.unknown_count AS unknown,
       transition_state.input_count AS discovered,
       transition_state.valid_count AS valid,
       transition_state.conflict_count AS conflicts,
       transition_state.candidate_count AS candidates,
       transition_state.existing_canonical_count AS existing_canonicals,
       transition_state.updated_count AS updated,
       transition_state.retired_count AS retired,
       transition_state.may_migrate AS may_migrate,
       EXISTS (SELECT 1 FROM receipt) AS receipt_written
FROM transition_state
