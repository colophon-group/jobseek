
SELECT jp.id::text AS posting_id,
       jp.board_id::text AS board_id,
       jp.source_url,
       jp.description_r2_hash,
       jp.is_active,
       jp.next_scrape_at,
       jp.leased_until,
       (jp.leased_until > now()) AS lease_active,
       jb.board_slug,
       jb.scraper_needs_browser,
       jb.scrape_interval_hours
FROM job_posting AS jp
JOIN job_board AS jb ON jb.id = jp.board_id
WHERE jb.board_slug = ANY($1::text[])
ORDER BY jp.id
