
SELECT jp.id,
       jp.source_url,
       jp.board_id,
       jp.description_r2_hash,
       jp.next_scrape_at,
       jb.scraper_needs_browser
FROM job_posting jp
JOIN job_board jb ON jb.id = jp.board_id
WHERE jp.is_active
  AND jp.next_scrape_at IS NOT NULL
  AND ($1::uuid IS NULL OR jp.id > $1::uuid)
  AND NOT (
            (jb.metadata->>'scraper_type' = 'skip'
             OR (
                 jb.metadata->>'scraper_type' IS NULL
                 AND (
                    jb.crawler_type IN ('accenture', 'almacareer', 'amazon', 'ashby', 'beehire', 'brassring', 'bytedance', 'cnstaff', 'comeet', 'cornerstone', 'curately', 'cvwarehouse', 'darwinbox', 'dayforce', 'deel', 'dvinci', 'earcu', 'fenbi', 'gem', 'greenhouse', 'hibob', 'hirehive', 'hireology', 'inline', 'jarvi', 'job51', 'jobbank104', 'jobylon', 'keka', 'kipt', 'lever', 'manatal', 'nowhiring', 'paynet', 'pinpoint', 'prospective', 'recruitee', 'recruiter_co_kr', 'seamlesshiring', 'talentreef', 'traffit', 'turbohire', 'unifr', 'unisante', 'universia', 'wecruit', 'welcometothejungle', 'woowa')
                    OR (
                        jb.crawler_type = 'rss'
                        AND COALESCE(jb.metadata->>'variant', '') <> 'legacy'
                    )
                    OR (
                         jb.crawler_type IN ('api_sniffer', 'nextdata')
                         AND jb.metadata ? 'fields'
                    )
                    OR (
                         jb.crawler_type = 'smartrecruiters'
                         AND jsonb_typeof(
                             jb.metadata->'canonical_job_id_url_template'
                         ) = 'string'
                         AND COALESCE(
                             jb.metadata->>'canonical_job_id_url_template',
                             ''
                         ) <> ''
                     )
                 )
             )
            )
            AND NOT COALESCE(jb.metadata->'scraper_config' ? 'enrich', false)
        )
ORDER BY jp.id
LIMIT $2
