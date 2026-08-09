-- Contract phase for making saved_job independent of the crawler mirror.
--
-- Drizzle owns the outer transaction. The source-to-child lock order freezes
-- the final mirror catch-up and saved-job writes until every invariant has
-- been checked and the compatibility objects have been removed.

LOCK TABLE public.company IN SHARE MODE;--> statement-breakpoint
LOCK TABLE public.job_posting IN SHARE MODE;--> statement-breakpoint
LOCK TABLE public.saved_job IN SHARE ROW EXCLUSIVE MODE;--> statement-breakpoint

DO $contract$
DECLARE
  ledger_count integer;
  latest_hash text;
  latest_created_at bigint;
  snapshot_columns integer;
  posting_fk_count integer;
  required_check_count integer;
  compatibility_trigger_count integer;
  compatibility_function oid;
  saved_job_user_fk_count integer;
  saved_job_unique_index_count integer;
  interview_fk_count integer;
BEGIN
  SELECT count(*),
         (array_agg(hash ORDER BY created_at DESC, id DESC))[1],
         (array_agg(created_at ORDER BY created_at DESC, id DESC))[1]
  INTO ledger_count, latest_hash, latest_created_at
  FROM drizzle.__drizzle_migrations;

  IF ledger_count <> 74
     OR latest_created_at IS DISTINCT FROM 1785753600000
     OR latest_hash IS DISTINCT FROM
        'e42314d98708bdced560abcc1d8f6c3abd6c58b7f467cebcf5e0cb1decc567dc'
  THEN
    RAISE EXCEPTION
      'Refusing saved-job contract: expected exact 0084 ledger tip, got rows=% created_at=% hash=%',
      ledger_count,
      latest_created_at,
      latest_hash;
  END IF;

  WITH expected(name, data_type, required) AS (
    VALUES
      ('posting_title', 'text', true),
      ('posting_source_url', 'text', true),
      ('posting_first_seen_at', 'timestamp with time zone', true),
      ('posting_is_active', 'boolean', true),
      ('posting_salary_min', 'integer', false),
      ('posting_salary_max', 'integer', false),
      ('posting_salary_currency', 'text', false),
      ('posting_salary_period', 'text', false),
      ('company_id', 'uuid', true),
      ('company_name', 'text', true),
      ('company_slug', 'text', true),
      ('company_icon', 'text', false)
  )
  SELECT count(*)
  INTO snapshot_columns
  FROM expected
  JOIN pg_attribute AS attribute
    ON attribute.attrelid = 'public.saved_job'::regclass
   AND attribute.attname = expected.name
   AND NOT attribute.attisdropped
  LEFT JOIN pg_attrdef AS default_value
    ON default_value.adrelid = attribute.attrelid
   AND default_value.adnum = attribute.attnum
  WHERE format_type(attribute.atttypid, attribute.atttypmod) = expected.data_type
    AND attribute.attnotnull = false
    AND default_value.oid IS NULL;

  IF snapshot_columns <> 12 THEN
    RAISE EXCEPTION
      'Refusing saved-job contract: expected all 12 nullable, default-free expand columns, found %',
      snapshot_columns;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_attribute AS attribute
    LEFT JOIN pg_attrdef AS default_value
      ON default_value.adrelid = attribute.attrelid
     AND default_value.adnum = attribute.attnum
    WHERE attribute.attrelid = 'public.saved_job'::regclass
      AND attribute.attname = 'job_posting_id'
      AND attribute.atttypid = 'uuid'::regtype
      AND attribute.attnotnull
      AND NOT attribute.attisdropped
      AND default_value.oid IS NULL
  ) THEN
    RAISE EXCEPTION
      'Refusing saved-job contract: saved_job.job_posting_id must remain default-free uuid NOT NULL';
  END IF;

  SELECT count(*)
  INTO posting_fk_count
  FROM pg_constraint AS constraint_row
  WHERE constraint_row.conrelid = 'public.saved_job'::regclass
    AND constraint_row.confrelid = 'public.job_posting'::regclass
    AND constraint_row.conname = 'saved_job_job_posting_id_job_posting_id_fk'
    AND constraint_row.contype = 'f'
    AND constraint_row.convalidated
    AND constraint_row.confdeltype = 'r'
    AND constraint_row.confupdtype = 'a'
    AND constraint_row.conkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'job_posting_id'
          AND NOT attisdropped
      )
    ]::smallint[]
    AND constraint_row.confkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.job_posting'::regclass
          AND attname = 'id'
          AND NOT attisdropped
      )
    ]::smallint[];

  IF posting_fk_count <> 1 OR (
    SELECT count(*)
    FROM pg_constraint
    WHERE conrelid = 'public.saved_job'::regclass
      AND confrelid = 'public.job_posting'::regclass
      AND contype = 'f'
  ) <> 1 THEN
    RAISE EXCEPTION
      'Refusing saved-job contract: expected the exact validated restrictive posting FK';
  END IF;

  SELECT count(*)
  INTO required_check_count
  FROM pg_constraint
  WHERE conrelid = 'public.saved_job'::regclass
    AND conname = 'saved_job_required_snapshot_check'
    AND contype = 'c'
    AND convalidated
    AND pg_get_constraintdef(oid, true) LIKE '%btrim(posting_title)%'
    AND pg_get_constraintdef(oid, true) LIKE '%btrim(posting_source_url)%'
    AND pg_get_constraintdef(oid, true) LIKE '%posting_first_seen_at%'
    AND pg_get_constraintdef(oid, true) LIKE '%posting_is_active%'
    AND pg_get_constraintdef(oid, true) LIKE '%company_id%'
    AND pg_get_constraintdef(oid, true) LIKE '%btrim(company_name)%'
    AND pg_get_constraintdef(oid, true) LIKE '%btrim(company_slug)%';

  IF required_check_count <> 1 OR EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'public.saved_job'::regclass
      AND conname = 'saved_job_snapshot_text_nonblank_check'
  ) THEN
    RAISE EXCEPTION
      'Refusing saved-job contract: the exact temporary required-snapshot CHECK is absent or contract CHECK already exists';
  END IF;

  compatibility_function := to_regprocedure(
    'public.saved_job_snapshot_from_mirror()'
  );
  SELECT count(*)
  INTO compatibility_trigger_count
  FROM pg_trigger
  WHERE tgrelid = 'public.saved_job'::regclass
    AND tgname = 'saved_job_snapshot_from_mirror_before_insert'
    AND tgfoid = compatibility_function
    AND tgtype = 7
    AND tgenabled = 'O'
    AND NOT tgisinternal;

  IF compatibility_function IS NULL OR compatibility_trigger_count <> 1 THEN
    RAISE EXCEPTION
      'Refusing saved-job contract: the exact enabled compatibility trigger/function pair is absent';
  END IF;

  SELECT count(*)
  INTO saved_job_user_fk_count
  FROM pg_constraint AS constraint_row
  WHERE constraint_row.conrelid = 'public.saved_job'::regclass
    AND constraint_row.confrelid = 'public.user'::regclass
    AND constraint_row.conname = 'saved_job_user_id_user_id_fk'
    AND constraint_row.contype = 'f'
    AND constraint_row.convalidated
    AND constraint_row.confdeltype = 'c'
    AND constraint_row.confupdtype = 'a'
    AND constraint_row.conkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'user_id'
          AND NOT attisdropped
      )
    ]::smallint[]
    AND constraint_row.confkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.user'::regclass
          AND attname = 'id'
          AND NOT attisdropped
      )
    ]::smallint[];

  SELECT count(*)
  INTO saved_job_unique_index_count
  FROM pg_index AS index_row
  WHERE index_row.indexrelid = to_regclass('public.idx_sj_user_posting')
    AND index_row.indrelid = 'public.saved_job'::regclass
    AND index_row.indisunique
    AND index_row.indisvalid
    AND index_row.indisready
    AND index_row.indpred IS NULL
    AND index_row.indexprs IS NULL
    AND index_row.indnkeyatts = 2
    AND index_row.indkey::text = format(
      '%s %s',
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'user_id'
          AND NOT attisdropped
      ),
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'job_posting_id'
          AND NOT attisdropped
      )
    );

  SELECT count(*)
  INTO interview_fk_count
  FROM pg_constraint AS constraint_row
  WHERE constraint_row.conrelid = 'public.application_interview'::regclass
    AND constraint_row.confrelid = 'public.saved_job'::regclass
    AND constraint_row.conname =
        'application_interview_saved_job_id_fkey'
    AND constraint_row.contype = 'f'
    AND constraint_row.convalidated
    AND constraint_row.confdeltype = 'c'
    AND constraint_row.confupdtype = 'a'
    AND constraint_row.conkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.application_interview'::regclass
          AND attname = 'saved_job_id'
          AND NOT attisdropped
      )
    ]::smallint[]
    AND constraint_row.confkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'id'
          AND NOT attisdropped
      )
    ]::smallint[];

  IF saved_job_user_fk_count <> 1
     OR saved_job_unique_index_count <> 1
     OR interview_fk_count <> 1
  THEN
    RAISE EXCEPTION
      'Refusing saved-job contract: durable user/uniqueness/interview invariants differ (user_fk=%, unique_index=%, interview_fk=%)',
      saved_job_user_fk_count,
      saved_job_unique_index_count,
      interview_fk_count;
  END IF;
END
$contract$;--> statement-breakpoint

-- Fill only required NULLs. Values already captured by 0084 or the app are
-- immutable snapshots and must never be refreshed from the mutable mirror.
UPDATE public.saved_job AS saved
SET
  posting_title = COALESCE(saved.posting_title, posting.titles[1]),
  posting_source_url = COALESCE(saved.posting_source_url, posting.source_url),
  posting_first_seen_at = COALESCE(
    saved.posting_first_seen_at,
    posting.first_seen_at
  ),
  posting_is_active = COALESCE(saved.posting_is_active, posting.is_active),
  company_id = COALESCE(saved.company_id, source_company.id),
  company_name = COALESCE(saved.company_name, source_company.name),
  company_slug = COALESCE(saved.company_slug, source_company.slug)
FROM public.job_posting AS posting
JOIN public.company AS source_company ON source_company.id = posting.company_id
WHERE saved.job_posting_id = posting.id
  AND (
    saved.posting_title IS NULL
    OR saved.posting_source_url IS NULL
    OR saved.posting_first_seen_at IS NULL
    OR saved.posting_is_active IS NULL
    OR saved.company_id IS NULL
    OR saved.company_name IS NULL
    OR saved.company_slug IS NULL
  );--> statement-breakpoint

DO $contract$
DECLARE
  incomplete_required bigint;
BEGIN
  SELECT count(*)
  INTO incomplete_required
  FROM public.saved_job
  WHERE NULLIF(btrim(posting_title), '') IS NULL
     OR NULLIF(btrim(posting_source_url), '') IS NULL
     OR posting_first_seen_at IS NULL
     OR posting_is_active IS NULL
     OR company_id IS NULL
     OR NULLIF(btrim(company_name), '') IS NULL
     OR NULLIF(btrim(company_slug), '') IS NULL;

  IF incomplete_required <> 0 THEN
    RAISE EXCEPTION
      'Saved-job contract catch-up left % incomplete required snapshots',
      incomplete_required;
  END IF;
END
$contract$;--> statement-breakpoint

-- NOT NULL carries completeness; this permanent CHECK retains the stronger
-- invariant that the four required text values also contain non-whitespace.
ALTER TABLE public.saved_job
  ADD CONSTRAINT saved_job_snapshot_text_nonblank_check
  CHECK (
    NULLIF(btrim(posting_title), '') IS NOT NULL
    AND NULLIF(btrim(posting_source_url), '') IS NOT NULL
    AND NULLIF(btrim(company_name), '') IS NOT NULL
    AND NULLIF(btrim(company_slug), '') IS NOT NULL
  ) NOT VALID;--> statement-breakpoint
ALTER TABLE public.saved_job
  VALIDATE CONSTRAINT saved_job_snapshot_text_nonblank_check;--> statement-breakpoint

ALTER TABLE public.saved_job
  ALTER COLUMN posting_title SET NOT NULL,
  ALTER COLUMN posting_source_url SET NOT NULL,
  ALTER COLUMN posting_first_seen_at SET NOT NULL,
  ALTER COLUMN posting_is_active SET NOT NULL,
  ALTER COLUMN company_id SET NOT NULL,
  ALTER COLUMN company_name SET NOT NULL,
  ALTER COLUMN company_slug SET NOT NULL;--> statement-breakpoint

-- Remove compatibility objects in dependency order. The posting FK is the
-- final removal so every durable invariant is already enforced before the
-- crawler mirror ceases to own saved-job lifetime.
ALTER TABLE public.saved_job
  DROP CONSTRAINT saved_job_required_snapshot_check;--> statement-breakpoint
DROP TRIGGER saved_job_snapshot_from_mirror_before_insert
  ON public.saved_job;--> statement-breakpoint
DROP FUNCTION public.saved_job_snapshot_from_mirror();--> statement-breakpoint
ALTER TABLE public.saved_job
  DROP CONSTRAINT saved_job_job_posting_id_job_posting_id_fk;--> statement-breakpoint

DO $contract$
DECLARE
  required_not_null integer;
  optional_nullable integer;
  permanent_check_count integer;
  permanent_check_definition text;
  saved_job_user_fk_count integer;
  saved_job_unique_index_count integer;
  interview_fk_count integer;
BEGIN
  SELECT count(*)
  INTO required_not_null
  FROM pg_attribute
  WHERE attrelid = 'public.saved_job'::regclass
    AND attname IN (
      'posting_title',
      'posting_source_url',
      'posting_first_seen_at',
      'posting_is_active',
      'company_id',
      'company_name',
      'company_slug'
    )
    AND attnotnull
    AND NOT attisdropped;

  SELECT count(*)
  INTO optional_nullable
  FROM pg_attribute
  WHERE attrelid = 'public.saved_job'::regclass
    AND attname IN (
      'posting_salary_min',
      'posting_salary_max',
      'posting_salary_currency',
      'posting_salary_period',
      'company_icon'
    )
    AND NOT attnotnull
    AND NOT attisdropped;

  SELECT
    count(*),
    min(pg_get_constraintdef(oid, true))
  INTO permanent_check_count, permanent_check_definition
  FROM pg_constraint
  WHERE conrelid = 'public.saved_job'::regclass
    AND conname = 'saved_job_snapshot_text_nonblank_check'
    AND contype = 'c'
    AND convalidated;

  SELECT count(*)
  INTO saved_job_user_fk_count
  FROM pg_constraint AS constraint_row
  WHERE constraint_row.conrelid = 'public.saved_job'::regclass
    AND constraint_row.confrelid = 'public.user'::regclass
    AND constraint_row.conname = 'saved_job_user_id_user_id_fk'
    AND constraint_row.contype = 'f'
    AND constraint_row.convalidated
    AND constraint_row.confdeltype = 'c'
    AND constraint_row.confupdtype = 'a'
    AND constraint_row.conkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'user_id'
          AND NOT attisdropped
      )
    ]::smallint[]
    AND constraint_row.confkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.user'::regclass
          AND attname = 'id'
          AND NOT attisdropped
      )
    ]::smallint[];

  SELECT count(*)
  INTO saved_job_unique_index_count
  FROM pg_index
  WHERE indexrelid = to_regclass('public.idx_sj_user_posting')
    AND indrelid = 'public.saved_job'::regclass
    AND indisunique
    AND indisvalid
    AND indisready
    AND indpred IS NULL
    AND indexprs IS NULL
    AND indnkeyatts = 2
    AND indkey::text = format(
      '%s %s',
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'user_id'
          AND NOT attisdropped
      ),
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'job_posting_id'
          AND NOT attisdropped
      )
    );

  SELECT count(*)
  INTO interview_fk_count
  FROM pg_constraint AS constraint_row
  WHERE constraint_row.conrelid = 'public.application_interview'::regclass
    AND constraint_row.confrelid = 'public.saved_job'::regclass
    AND constraint_row.conname =
        'application_interview_saved_job_id_fkey'
    AND constraint_row.contype = 'f'
    AND constraint_row.convalidated
    AND constraint_row.confdeltype = 'c'
    AND constraint_row.confupdtype = 'a'
    AND constraint_row.conkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.application_interview'::regclass
          AND attname = 'saved_job_id'
          AND NOT attisdropped
      )
    ]::smallint[]
    AND constraint_row.confkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'id'
          AND NOT attisdropped
      )
    ]::smallint[];

  IF required_not_null <> 7
     OR optional_nullable <> 5
     OR permanent_check_count <> 1
     OR permanent_check_definition IS DISTINCT FROM
        $definition$CHECK (NULLIF(btrim(posting_title), ''::text) IS NOT NULL AND NULLIF(btrim(posting_source_url), ''::text) IS NOT NULL AND NULLIF(btrim(company_name), ''::text) IS NOT NULL AND NULLIF(btrim(company_slug), ''::text) IS NOT NULL)$definition$
     OR NOT EXISTS (
       SELECT 1
       FROM pg_attribute AS attribute
       LEFT JOIN pg_attrdef AS default_value
         ON default_value.adrelid = attribute.attrelid
        AND default_value.adnum = attribute.attnum
       WHERE attribute.attrelid = 'public.saved_job'::regclass
         AND attribute.attname = 'job_posting_id'
         AND attribute.atttypid = 'uuid'::regtype
         AND attribute.attnotnull
         AND NOT attribute.attisdropped
         AND default_value.oid IS NULL
     )
     OR EXISTS (
       SELECT 1
       FROM public.saved_job
       WHERE NULLIF(btrim(posting_title), '') IS NULL
          OR NULLIF(btrim(posting_source_url), '') IS NULL
          OR posting_first_seen_at IS NULL
          OR posting_is_active IS NULL
          OR company_id IS NULL
          OR NULLIF(btrim(company_name), '') IS NULL
          OR NULLIF(btrim(company_slug), '') IS NULL
     )
     OR EXISTS (
       SELECT 1
       FROM pg_constraint
       WHERE conrelid = 'public.saved_job'::regclass
         AND (
           conname = 'saved_job_required_snapshot_check'
           OR confrelid = 'public.job_posting'::regclass
         )
     )
     OR EXISTS (
       SELECT 1
       FROM pg_trigger
       WHERE tgrelid = 'public.saved_job'::regclass
         AND tgname = 'saved_job_snapshot_from_mirror_before_insert'
         AND NOT tgisinternal
     )
     OR to_regprocedure('public.saved_job_snapshot_from_mirror()') IS NOT NULL
     OR saved_job_user_fk_count <> 1
     OR saved_job_unique_index_count <> 1
     OR interview_fk_count <> 1
  THEN
    RAISE EXCEPTION
      'Saved-job contract final catalog verification failed';
  END IF;
END
$contract$;
