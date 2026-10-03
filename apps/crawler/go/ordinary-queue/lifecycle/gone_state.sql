
SELECT board_status,
       gone_confirmation_count,
       gone_first_confirmed_at,
       gone_last_confirmed_at,
       last_success_at,
       gone_at
FROM job_board
WHERE id = $1
FOR UPDATE
