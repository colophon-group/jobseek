INSERT INTO job_posting
    (company_id, board_id,
     employment_type, source_url,
     first_seen_at, last_seen_at, next_scrape_at,
     is_active, titles, locales,
     location_ids, location_types,
     salary_min, salary_max, salary_currency, salary_period, salary_eur,
     experience_min, experience_max, technology_ids,
     occupation_id, seniority_id)
VALUES ($1, $2, $3, $4,
        now(), now(), now(),
        true, $5, $6,
        $7, $8,
        $9, $10, $11, $12, $13,
        $14, $15, $16,
        $17, $18)
ON CONFLICT (source_url) DO NOTHING
RETURNING id
