-- Exact original Python provider retirement statement; claim-fenced caller only.
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
    AND slug = $9
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
  WHERE posting.company_id = $2
    AND ($10::boolean OR posting.board_id = $1)
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
