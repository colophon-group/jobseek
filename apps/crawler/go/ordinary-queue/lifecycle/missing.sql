
UPDATE job_posting
SET missing_count = missing_count + 1,
    is_active = CASE
        WHEN missing_count + 1 >= $3 THEN false
        ELSE is_active
    END,
    next_scrape_at = CASE
        WHEN missing_count + 1 >= $3 THEN NULL
        ELSE next_scrape_at
    END,
    updated_at = CASE
        WHEN missing_count + 1 >= $3 THEN now()
        ELSE updated_at
    END
WHERE board_id = $1
  AND is_active = true
  AND last_seen_at < $2
RETURNING id, source_url
