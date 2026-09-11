-- Internship is a seniority level, not an employment type. Preserve any
-- explicit seniority selection; otherwise translate a legacy internship-only
-- employment filter to the Intern level. For a mixed legacy selection, retain
-- the remaining types without introducing a narrower cross-facet AND filter.
WITH candidates AS (
  SELECT
    id,
    filters,
    COALESCE(
      (
        SELECT jsonb_agg(entry.value ORDER BY entry.ordinality)
        FROM jsonb_array_elements(
          CASE
            WHEN jsonb_typeof(filters->'employmentType') = 'array'
              THEN filters->'employmentType'
            ELSE '[]'::jsonb
          END
        )
          WITH ORDINALITY AS entry(value, ordinality)
        WHERE jsonb_typeof(entry.value) <> 'string'
          OR lower(trim(entry.value #>> '{}')) <> 'internship'
      ),
      '[]'::jsonb
    ) AS remaining_employment_types,
    CASE
      WHEN jsonb_typeof(filters->'senioritySlugs') = 'array'
        THEN jsonb_array_length(filters->'senioritySlugs') > 0
      ELSE false
    END AS has_explicit_seniority
  FROM public.watchlist
  WHERE jsonb_typeof(filters->'employmentType') = 'array'
    AND EXISTS (
      SELECT 1
      FROM jsonb_array_elements(
        CASE
          WHEN jsonb_typeof(filters->'employmentType') = 'array'
            THEN filters->'employmentType'
          ELSE '[]'::jsonb
        END
      ) AS entry(value)
      WHERE jsonb_typeof(entry.value) = 'string'
        AND lower(trim(entry.value #>> '{}')) = 'internship'
    )
), without_internship_type AS (
  SELECT
    id,
    has_explicit_seniority,
    jsonb_array_length(remaining_employment_types) = 0 AS was_internship_only,
    CASE
      WHEN jsonb_array_length(remaining_employment_types) = 0
        THEN filters - 'employmentType'
      ELSE jsonb_set(
        filters,
        '{employmentType}',
        remaining_employment_types,
        true
      )
    END AS filters
  FROM candidates
)
UPDATE public.watchlist AS watchlist
SET filters = CASE
      WHEN migrated.has_explicit_seniority OR NOT migrated.was_internship_only
        THEN migrated.filters
      ELSE jsonb_set(
        migrated.filters,
        '{senioritySlugs}',
        '["intern"]'::jsonb,
        true
      )
    END,
    updated_at = now()
FROM without_internship_type AS migrated
WHERE watchlist.id = migrated.id;
