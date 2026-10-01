
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
