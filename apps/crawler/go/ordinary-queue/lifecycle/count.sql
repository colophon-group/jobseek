
SELECT
    COUNT(*) AS active,
    COUNT(*) FILTER (WHERE last_seen_at < $2) AS missing
FROM job_posting
WHERE board_id = $1
  AND is_active = true
