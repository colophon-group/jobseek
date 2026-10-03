
UPDATE job_board
SET board_status = $2,
    gone_confirmation_count = $3,
    gone_first_confirmed_at = $4,
    gone_last_confirmed_at = $5,
    gone_at = $6,
    next_check_at = $7,
    last_error = $8,
    last_gone_error = $8,
    last_gone_endpoint = $9,
    last_gone_status = $10,
    gone_transition_count = gone_transition_count + CASE WHEN $11 THEN 1 ELSE 0 END,
    consecutive_failures = 0,
    is_enabled = true,
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE id = $1
RETURNING board_status, gone_confirmation_count, next_check_at
