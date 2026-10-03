
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
