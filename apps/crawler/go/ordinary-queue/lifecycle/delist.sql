
UPDATE job_posting
SET is_active = false, next_scrape_at = NULL, updated_at = now()
WHERE board_id = $1 AND is_active = true
RETURNING id
