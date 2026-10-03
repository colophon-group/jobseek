
WITH previous AS MATERIALIZED (
    SELECT id, board_status
    FROM job_board
    WHERE id = $1
      AND metadata ->> '_monitor_config_fingerprint' IS NOT DISTINCT FROM $2::text
    FOR UPDATE
), updated AS (
    UPDATE job_board jb
    SET consecutive_failures = 0,
        last_error = NULL,
        last_success_at = now(),
        next_check_at = now() + (check_interval_minutes || ' minutes')::interval,
        empty_check_count = 0,
        board_status = CASE
            WHEN COALESCE((jb.metadata ->> 'suspect_streak')::int, 0) >= 3
            THEN 'suspect'
            ELSE 'active'
        END,
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
