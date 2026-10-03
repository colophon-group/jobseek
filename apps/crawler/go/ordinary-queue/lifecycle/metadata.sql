
UPDATE job_board
SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
    updated_at = now()
WHERE id = $1
