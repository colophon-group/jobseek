-- Running upgrade 0014 -> 0015

ALTER TABLE job_board
    ADD COLUMN IF NOT EXISTS quarantined_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_quarantined_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_quarantine_error TEXT,
    ADD COLUMN IF NOT EXISTS quarantine_probe_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_recovered_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS recovery_count BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_jb_quarantine_due
    ON job_board (next_check_at, id)
    WHERE is_enabled = true AND board_status = 'quarantined';;

UPDATE job_board
SET is_enabled = true,
    board_status = 'quarantined',
    quarantined_at = COALESCE(quarantined_at, updated_at, now()),
    last_quarantined_at = COALESCE(last_quarantined_at, updated_at, now()),
    last_quarantine_error = COALESCE(last_quarantine_error, last_error),
    quarantine_probe_count = GREATEST(quarantine_probe_count, 1),
    next_check_at = now() + CASE
        WHEN crawler_type = 'ashby' THEN interval '0 seconds'
        ELSE (
            abs(hashtextextended(id::text, 6157) % 21600)::text || ' seconds'
        )::interval
    END,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE board_status = 'disabled'
  AND is_enabled = false;;

UPDATE alembic_version SET version_num='0015' WHERE alembic_version.version_num = '0014';
