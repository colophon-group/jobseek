-- Running upgrade 0010 -> 0011

UPDATE job_board
SET is_enabled = true,
    board_status = 'suspect',
    gone_at = NULL,
    next_check_at = now(),
    lease_owner = NULL,
    leased_until = NULL,
    updated_at = now()
WHERE board_status = 'gone'
  AND is_enabled = false
  AND consecutive_failures = 0
  AND last_error IS NULL
  AND last_non_empty_at IS NOT NULL
  AND empty_check_count >= 6;

UPDATE alembic_version SET version_num='0011' WHERE alembic_version.version_num = '0010';
