package executor

const fetchPostingForEnrichSQL = `
SELECT titles, locales, location_ids, location_types, employment_type
FROM job_posting
WHERE id = $1
`

// Frozen existing Python SQL. Query parity and real PostgreSQL effects must
// remain verified before the native executor becomes a production owner.

const updateContentSQL = `
UPDATE job_posting
SET employment_type = COALESCE($2, employment_type),
    titles = COALESCE($3, titles),
    locales = COALESCE($4, locales),
    location_ids = COALESCE($5, location_ids),
    location_types = COALESCE($6, location_types),
    technology_ids = COALESCE($7, technology_ids),
    salary_min = COALESCE($8, salary_min),
    salary_max = COALESCE($9, salary_max),
    salary_currency = COALESCE($10, salary_currency),
    salary_period = COALESCE($11, salary_period),
    salary_eur = COALESCE($12, salary_eur),
    experience_min = COALESCE($13, experience_min),
    experience_max = COALESCE($14, experience_max),
    occupation_id = COALESCE($15, occupation_id),
    seniority_id = COALESCE($16, seniority_id),
    to_be_enriched = true,
    updated_at = CASE
        WHEN employment_type IS DISTINCT FROM COALESCE($2, employment_type)
          OR titles IS DISTINCT FROM COALESCE($3, titles)
          OR locales IS DISTINCT FROM COALESCE($4, locales)
          OR location_ids IS DISTINCT FROM COALESCE($5, location_ids)
          OR location_types IS DISTINCT FROM COALESCE($6, location_types)
          OR technology_ids IS DISTINCT FROM COALESCE($7, technology_ids)
          OR salary_min IS DISTINCT FROM COALESCE($8, salary_min)
          OR salary_max IS DISTINCT FROM COALESCE($9, salary_max)
          OR salary_currency IS DISTINCT FROM COALESCE($10, salary_currency)
          OR salary_period IS DISTINCT FROM COALESCE($11, salary_period)
          OR salary_eur IS DISTINCT FROM COALESCE($12, salary_eur)
          OR experience_min IS DISTINCT FROM COALESCE($13, experience_min)
          OR experience_max IS DISTINCT FROM COALESCE($14, experience_max)
          OR occupation_id IS DISTINCT FROM COALESCE($15, occupation_id)
          OR seniority_id IS DISTINCT FROM COALESCE($16, seniority_id)
        THEN now()
        ELSE updated_at
    END
WHERE id = $1
`

const recordSuccessSQL = `
UPDATE job_posting jp
SET scrape_failures  = 0,
    last_scraped_at  = now(),
    next_scrape_at   = CASE
        WHEN NOT jp.is_active THEN NULL
        WHEN (SELECT metadata->>'rescrape_policy' FROM job_board WHERE id = jp.board_id) = 'never'
            THEN NULL
        ELSE now() + (COALESCE(
            (SELECT scrape_interval_hours FROM job_board WHERE id = jp.board_id),
            24
        ) || ' hours')::interval
    END,
    leased_until     = NULL
WHERE jp.id = $1
`

const recordFailureSQL = `
UPDATE job_posting
SET scrape_failures   = scrape_failures + 1,
    last_scraped_at   = now(),
    next_scrape_at    = CASE
        WHEN $2::boolean OR scrape_failures + 1 >= 3 THEN NULL
        ELSE now() + (30 * pow(2, scrape_failures)) * interval '1 minute'
    END,
    is_active         = CASE
        WHEN $2::boolean OR scrape_failures + 1 >= 3 THEN false
        ELSE is_active
    END,
    updated_at        = CASE
        -- Only bump on the tombstone branch — that's a state change
        -- the exporter cursor needs to push to Supabase + Typesense
        -- (cursor key is ` + "`" + `` + "`" + `(updated_at, id)` + "`" + `` + "`" + `). For pure backoff
        -- updates we leave updated_at alone so we don't reflow
        -- unchanged docs.
        WHEN $2::boolean OR scrape_failures + 1 >= 3 THEN now()
        ELSE updated_at
    END,
    leased_until = NULL
WHERE id = $1
`

const recordTransientSQL = `
UPDATE job_posting
SET scrape_failures   = scrape_failures + 1,
    last_scraped_at   = now(),
    next_scrape_at    = CASE
        WHEN scrape_failures + 1 >= 3 THEN NULL
        ELSE now() + (30 * pow(2, scrape_failures)) * interval '1 minute'
    END,
    leased_until = NULL
WHERE id = $1
`

const upsertDescriptionSQL = `
WITH prior AS MATERIALIZED (
    SELECT html, hash, r2_uploaded
    FROM descriptions
    WHERE posting_id = $1
      AND locale = $2
    FOR UPDATE
),
upserted AS (
    INSERT INTO descriptions (posting_id, locale, html, hash, r2_uploaded)
    SELECT $1, $2, $3, COALESCE($4::bigint, $5::bigint), false
    FROM (SELECT count(*) FROM prior) AS locked_prior
    WHERE true
    ON CONFLICT (posting_id, locale) DO UPDATE
    SET html = EXCLUDED.html,
        hash = EXCLUDED.hash,
        r2_uploaded = false,
        r2_upload_failures = 0,
        r2_next_attempt_at = '-infinity'::timestamptz,
        updated_at = now()
    WHERE convert_to(descriptions.html, 'UTF8')
        IS DISTINCT FROM convert_to(EXCLUDED.html, 'UTF8')
    RETURNING r2_uploaded
)
SELECT sample.keep AS diagnostic_sampled,
       EXISTS (SELECT 1 FROM prior) AS row_existed,
       CASE WHEN sample.keep THEN (SELECT md5(html) FROM prior) END
           AS old_html_checksum,
       CASE WHEN sample.keep THEN md5($3) END AS new_html_checksum,
       EXISTS (SELECT 1 FROM upserted) AS upload_scheduled,
       EXISTS (SELECT 1 FROM upserted)
           AND (SELECT r2_uploaded FROM prior) IS DISTINCT FROM false
           AS upload_state_changed
FROM (
    SELECT get_byte(uuid_send($1::uuid), 15) = 0 AS keep
) AS sample
`

const currentDetailSQL = `
SELECT jp.board_id::text AS board_id,
       jp.source_url,
       jp.description_r2_hash,
       jp.is_active,
       jp.next_scrape_at,
       jb.board_slug,
       jb.is_enabled,
       jb.board_status,
       jb.metadata,
       jb.crawler_type,
       jb.scraper_needs_browser,
       jb.scrape_interval_hours
FROM job_posting AS jp
JOIN job_board AS jb ON jb.id = jp.board_id
WHERE jp.id = $1::uuid
`
