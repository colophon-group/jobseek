-- Running upgrade 0015 -> 0016

ALTER TABLE job_board
    ADD COLUMN IF NOT EXISTS gone_confirmation_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gone_first_confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS gone_last_confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_gone_error TEXT,
    ADD COLUMN IF NOT EXISTS last_gone_endpoint TEXT,
    ADD COLUMN IF NOT EXISTS last_gone_status SMALLINT,
    ADD COLUMN IF NOT EXISTS gone_transition_count BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gone_recovery_count BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_jb_gone_due
    ON job_board (next_check_at, id)
    WHERE is_enabled = true AND board_status IN ('gone_pending', 'gone');;

UPDATE job_board
SET is_enabled = true,
    board_status = 'gone_pending',
    gone_confirmation_count = GREATEST(gone_confirmation_count, 1),
    gone_first_confirmed_at = COALESCE(gone_first_confirmed_at, gone_at, updated_at, now()),
    gone_last_confirmed_at = COALESCE(gone_last_confirmed_at, gone_at, updated_at, now()),
    last_gone_error = COALESCE(
        last_gone_error,
        last_error,
        'Legacy one-shot BoardGoneError; endpoint and status unavailable'
    ),
    gone_transition_count = GREATEST(gone_transition_count, 1),
    next_check_at = now() + (
        abs(hashtextextended(id::text, 6156) % 900)::text || ' seconds'
    )::interval,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE board_status = 'gone';;

UPDATE alembic_version SET version_num='0016' WHERE alembic_version.version_num = '0015';
