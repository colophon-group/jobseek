
SELECT id::text AS board_id,
       board_slug,
       board_url,
       is_enabled,
       board_status,
       metadata,
       crawler_type,
       scraper_needs_browser,
       scrape_interval_hours
FROM job_board
WHERE board_slug = ANY($1::text[])
ORDER BY board_slug
