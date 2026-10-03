
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
