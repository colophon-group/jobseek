WITH discovered AS (
  SELECT *
  FROM unnest($1::text[], $2::text[], $3::boolean[])
       AS input(source_identity, source_url, explicit_identity)
), violations AS (
  SELECT 'duplicate_identity_in_batch'::text AS reason,
         min(source_identity) AS source_identity,
         min(source_url) AS source_url
  FROM discovered
  GROUP BY source_identity
  HAVING count(*) <> 1

  UNION ALL

  SELECT 'duplicate_outbound_url_in_batch',
         min(source_identity),
         source_url
  FROM discovered
  GROUP BY source_url
  HAVING count(*) <> 1

  UNION ALL

  SELECT 'malformed_explicit_identity', d.source_identity, d.source_url
  FROM discovered d
  WHERE d.explicit_identity
    AND d.source_identity !~
        '^[a-z][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9._-]{0,63}:[A-Za-z0-9][A-Za-z0-9._~:/-]{0,255}[A-Za-z0-9._~:/-]{0,128}$'

  UNION ALL

  SELECT 'cross_owner_identity', d.source_identity, d.source_url
  FROM discovered d
  JOIN job_posting posting
    ON posting.source_identity = d.source_identity
  WHERE d.explicit_identity
    AND posting.company_id <> $4::uuid

  UNION ALL

  SELECT 'outbound_url_owned_by_other_identity', d.source_identity, d.source_url
  FROM discovered d
  JOIN job_posting posting
    ON posting.source_url = d.source_url
   AND posting.source_identity <> d.source_identity

  UNION ALL

  SELECT 'outbound_alias_owned_by_other_identity', d.source_identity, d.source_url
  FROM discovered d
  JOIN job_posting_source_alias alias
    ON alias.source_url = d.source_url
  JOIN job_posting posting
    ON posting.id = alias.posting_id
   AND posting.source_identity <> d.source_identity
)
SELECT reason, source_identity, source_url
FROM violations
ORDER BY reason, source_identity, source_url
LIMIT 1
