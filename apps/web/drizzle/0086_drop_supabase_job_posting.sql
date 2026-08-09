-- Retire only the oversized Supabase job_posting mirror.
--
-- This migration is intentionally conservative: the smaller crawler taxonomy,
-- company, and board tables remain in place for the first Free-plan window.
-- Drizzle owns the outer transaction, so every check and the RESTRICT drop are
-- atomic. A stale reader, writer, dependency, ledger, or size estimate aborts
-- the whole migration.

-- The destructive migration is runnable only from an attested session. The
-- protected production workflow and the isolated restore drill create this
-- TEMP marker on the same physical connection before invoking Drizzle/psql.
-- An ordinary `pnpm db:migrate` session cannot accidentally reach the drop.
DO $attestation$
DECLARE
  marker_count integer;
  marker_mode text;
  marker_valid boolean;
  source_oid oid := to_regclass('public.job_posting');
BEGIN
  IF to_regclass('pg_temp.jobseek_retirement_attestation') IS NULL THEN
    RAISE EXCEPTION
      'Refusing job_posting retirement: session-local readiness attestation is absent';
  END IF;

  SELECT count(*), min(mode), bool_and(
    attested_at >= clock_timestamp() - interval '30 minutes'
    AND attested_at <= clock_timestamp() + interval '1 minute'
    AND web_deploy_sha ~ '^[0-9a-f]{40}$'
    AND readiness_digest ~ '^[0-9a-f]{64}$'
    AND (
      (
        mode = 'production-drop'
        AND confirmation = 'DROP-ONLY-JOB-POSTING-0086'
        AND backup_restore_run_id > 0
        AND crawler_deploy_run_id > 0
        AND typesense_backfill_run_id > 0
      )
      OR (
        mode = 'restore-drill'
        AND confirmation = 'RESTORE-ONLY-JOB-POSTING-0086'
        AND backup_restore_run_id = 0
        AND crawler_deploy_run_id = 0
        AND typesense_backfill_run_id = 0
      )
    )
  )
  INTO marker_count, marker_mode, marker_valid
  FROM pg_temp.jobseek_retirement_attestation;

  IF marker_count <> 1
     OR marker_valid IS DISTINCT FROM true
     OR marker_mode NOT IN ('production-drop', 'restore-drill')
  THEN
    RAISE EXCEPTION
      'Refusing job_posting retirement: session-local readiness attestation is invalid';
  END IF;

  IF marker_mode = 'production-drop' THEN
    IF source_oid IS NULL THEN
      RAISE EXCEPTION
        'Refusing production retirement: public.job_posting is already absent';
    END IF;
    EXECUTE 'LOCK TABLE public.job_posting IN ACCESS EXCLUSIVE MODE';
  ELSIF source_oid IS NOT NULL THEN
    RAISE EXCEPTION
      'Refusing restore-only convergence: public.job_posting unexpectedly exists';
  END IF;
END
$attestation$;--> statement-breakpoint

LOCK TABLE public.saved_job IN SHARE ROW EXCLUSIVE MODE;--> statement-breakpoint

DO $retirement$
DECLARE
  job_posting_oid oid := to_regclass('public.job_posting');
  attestation_mode text;
  ledger_count integer;
  latest_hash text;
  latest_created_at bigint;
  projected_database_bytes bigint;
  incomplete_snapshots bigint;
  snapshot_column_count integer;
  posting_fk_count integer;
  snapshot_check_count integer;
  saved_job_user_fk_count integer;
  saved_job_unique_index_count integer;
  interview_fk_count integer;
  inbound_fk_count integer;
  dependent_view_count integer;
  dependent_function_count integer;
  noninternal_trigger_count integer;
  publication_count integer;
  compatibility_trigger_count integer;
  compatibility_function_count integer;
  referencing_routine_count integer;
BEGIN
  SELECT mode
  INTO attestation_mode
  FROM pg_temp.jobseek_retirement_attestation;

  SELECT count(*),
         (array_agg(hash ORDER BY created_at DESC, id DESC))[1],
         (array_agg(created_at ORDER BY created_at DESC, id DESC))[1]
  INTO ledger_count, latest_hash, latest_created_at
  FROM drizzle.__drizzle_migrations;

  IF ledger_count <> 75
     OR latest_created_at IS DISTINCT FROM 1785757200000
     OR latest_hash IS DISTINCT FROM
        'eec5962093a1eb8a7058f9bf031877d148718e2531eaa981b86c5c6bc51165ab'
  THEN
    RAISE EXCEPTION
      'Refusing job_posting retirement: expected exact 0085 ledger tip, got rows=% created_at=% hash=%',
      ledger_count,
      latest_created_at,
      latest_hash;
  END IF;

  IF attestation_mode = 'production-drop' THEN
    IF job_posting_oid IS NULL OR NOT EXISTS (
      SELECT 1
      FROM pg_class AS relation
      JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
      WHERE relation.oid = job_posting_oid
        AND namespace.nspname = 'public'
        AND relation.relname = 'job_posting'
        AND relation.relkind = 'r'
        AND relation.relpersistence = 'p'
    ) THEN
      RAISE EXCEPTION
        'Refusing job_posting retirement: public.job_posting is not the expected permanent ordinary table';
    END IF;
  ELSIF attestation_mode <> 'restore-drill' OR job_posting_oid IS NOT NULL THEN
    RAISE EXCEPTION
      'Refusing job_posting retirement: restore-only source state differs';
  END IF;

  WITH expected(name, data_type, required) AS (
    VALUES
      ('job_posting_id', 'uuid', true),
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
  INTO snapshot_column_count
  FROM expected
  JOIN pg_attribute AS attribute
    ON attribute.attrelid = 'public.saved_job'::regclass
   AND attribute.attname = expected.name
   AND NOT attribute.attisdropped
  LEFT JOIN pg_attrdef AS default_value
    ON default_value.adrelid = attribute.attrelid
   AND default_value.adnum = attribute.attnum
  WHERE format_type(attribute.atttypid, attribute.atttypmod) = expected.data_type
    AND attribute.attnotnull = expected.required
    AND default_value.oid IS NULL;

  SELECT count(*)
  INTO incomplete_snapshots
  FROM public.saved_job
  WHERE NULLIF(btrim(posting_title), '') IS NULL
     OR NULLIF(btrim(posting_source_url), '') IS NULL
     OR posting_first_seen_at IS NULL
     OR posting_is_active IS NULL
     OR company_id IS NULL
     OR NULLIF(btrim(company_name), '') IS NULL
     OR NULLIF(btrim(company_slug), '') IS NULL;

  SELECT count(*)
  INTO posting_fk_count
  FROM pg_constraint
  WHERE conrelid = 'public.saved_job'::regclass
    AND contype = 'f'
    AND conkey = ARRAY[
      (
        SELECT attnum
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'job_posting_id'
          AND NOT attisdropped
      )
    ]::smallint[];

  SELECT count(*)
  INTO snapshot_check_count
  FROM pg_constraint
  WHERE conrelid = 'public.saved_job'::regclass
    AND conname = 'saved_job_snapshot_text_nonblank_check'
    AND contype = 'c'
    AND convalidated
    AND pg_get_constraintdef(oid, true) =
      $definition$CHECK (NULLIF(btrim(posting_title), ''::text) IS NOT NULL AND NULLIF(btrim(posting_source_url), ''::text) IS NOT NULL AND NULLIF(btrim(company_name), ''::text) IS NOT NULL AND NULLIF(btrim(company_slug), ''::text) IS NOT NULL)$definition$;

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
        SELECT attnum FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'user_id' AND NOT attisdropped
      )
    ]::smallint[]
    AND constraint_row.confkey = ARRAY[
      (
        SELECT attnum FROM pg_attribute
        WHERE attrelid = 'public.user'::regclass
          AND attname = 'id' AND NOT attisdropped
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
    AND constraint_row.conname = 'application_interview_saved_job_id_fkey'
    AND constraint_row.contype = 'f'
    AND constraint_row.convalidated
    AND constraint_row.confdeltype = 'c'
    AND constraint_row.confupdtype = 'a'
    AND constraint_row.conkey = ARRAY[
      (
        SELECT attnum FROM pg_attribute
        WHERE attrelid = 'public.application_interview'::regclass
          AND attname = 'saved_job_id' AND NOT attisdropped
      )
    ]::smallint[]
    AND constraint_row.confkey = ARRAY[
      (
        SELECT attnum FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname = 'id' AND NOT attisdropped
      )
    ]::smallint[];

  SELECT count(*)
  INTO compatibility_trigger_count
  FROM pg_trigger
  WHERE tgrelid = 'public.saved_job'::regclass
    AND tgname = 'saved_job_snapshot_from_mirror_before_insert'
    AND NOT tgisinternal;

  compatibility_function_count := CASE
    WHEN to_regprocedure('public.saved_job_snapshot_from_mirror()') IS NULL THEN 0
    ELSE 1
  END;

  SELECT count(*)
  INTO referencing_routine_count
  FROM pg_proc AS routine
  JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
  WHERE namespace.nspname <> 'information_schema'
    AND namespace.nspname !~ '^pg_'
    AND routine.prokind IN ('f', 'p')
    AND pg_get_functiondef(routine.oid) ~* '\mjob_posting\M';

  IF snapshot_column_count <> 13
     OR incomplete_snapshots <> 0
     OR posting_fk_count <> 0
     OR snapshot_check_count <> 1
     OR saved_job_user_fk_count <> 1
     OR saved_job_unique_index_count <> 1
     OR interview_fk_count <> 1
     OR compatibility_trigger_count <> 0
     OR compatibility_function_count <> 0
     OR referencing_routine_count <> 0
  THEN
    RAISE EXCEPTION
      'Refusing job_posting retirement: durable saved-job invariants differ (columns=%, incomplete=%, posting_fk=%, check=%, user_fk=%, unique_index=%, interview_fk=%, compatibility_trigger=%, compatibility_function=%, referencing_routines=%)',
      snapshot_column_count,
      incomplete_snapshots,
      posting_fk_count,
      snapshot_check_count,
      saved_job_user_fk_count,
      saved_job_unique_index_count,
      interview_fk_count,
      compatibility_trigger_count,
      compatibility_function_count,
      referencing_routine_count;
  END IF;

  IF attestation_mode = 'production-drop' THEN
    SELECT count(*)
    INTO inbound_fk_count
    FROM pg_constraint
    WHERE confrelid = job_posting_oid
      AND conrelid <> job_posting_oid;

    SELECT count(DISTINCT dependent_view.oid)
    INTO dependent_view_count
    FROM pg_depend AS dependency
    JOIN pg_rewrite AS rewrite_rule
      ON dependency.classid = 'pg_rewrite'::regclass
     AND dependency.objid = rewrite_rule.oid
    JOIN pg_class AS dependent_view ON dependent_view.oid = rewrite_rule.ev_class
    WHERE dependency.refclassid = 'pg_class'::regclass
      AND dependency.refobjid = job_posting_oid
      AND dependent_view.oid <> job_posting_oid;

    SELECT count(DISTINCT dependency.objid)
    INTO dependent_function_count
    FROM pg_depend AS dependency
    WHERE dependency.refclassid = 'pg_class'::regclass
      AND dependency.refobjid = job_posting_oid
      AND dependency.classid = 'pg_proc'::regclass;

    SELECT count(*)
    INTO noninternal_trigger_count
    FROM pg_trigger
    WHERE tgrelid = job_posting_oid
      AND NOT tgisinternal;

    SELECT count(*)
    INTO publication_count
    FROM pg_publication_rel
    WHERE prrelid = job_posting_oid;

    IF inbound_fk_count <> 0
       OR dependent_view_count <> 0
       OR dependent_function_count <> 0
       OR noninternal_trigger_count <> 0
       OR publication_count <> 0
    THEN
      RAISE EXCEPTION
        'Refusing job_posting retirement: external dependencies remain (inbound_fk=%, views=%, functions=%, triggers=%, publications=%)',
        inbound_fk_count,
        dependent_view_count,
        dependent_function_count,
        noninternal_trigger_count,
        publication_count;
    END IF;

    projected_database_bytes :=
      pg_database_size(current_database())
      - pg_total_relation_size(job_posting_oid);

    IF projected_database_bytes >= 400 * 1024 * 1024 THEN
      RAISE EXCEPTION
        'Refusing job_posting retirement: projected database size % bytes is not below the 400 MiB safety ceiling',
        projected_database_bytes;
    END IF;
  END IF;
END
$retirement$;--> statement-breakpoint

-- RESTRICT is the final catalog guard. PostgreSQL aborts instead of silently
-- cascading to any dependency missed by the explicit evidence queries above.
DROP TABLE IF EXISTS public.job_posting RESTRICT;--> statement-breakpoint

DO $retirement$
BEGIN
  IF to_regclass('public.job_posting') IS NOT NULL THEN
    RAISE EXCEPTION
      'job_posting retirement postcondition failed: relation still exists';
  END IF;
END
$retirement$;
