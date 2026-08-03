-- Reconcile the known Supabase production baseline before the mirror cutover.
--
-- Migrations 0080-0082 were authored but never recorded by Drizzle. Production
-- contains only part of their effects: the interview CHECK and active-company
-- index exist, while watchlist.is_public still defaults to true and the two
-- experience columns remain integer. Replaying that sequence would rewrite the
-- 1.4 GB job_posting mirror immediately before its planned removal.
--
-- This replacement accepts only the verified 0079 ledger tip, keeps the mirror
-- columns integer, and converges the three small schema objects idempotently.
-- Drizzle supplies the outer transaction; do not add transaction control here.

DO $reconcile$
DECLARE
  latest_hash text;
  latest_created_at bigint;
  integer_experience_columns integer;
BEGIN
  SELECT hash, created_at
  INTO latest_hash, latest_created_at
  FROM drizzle.__drizzle_migrations
  ORDER BY created_at DESC, id DESC
  LIMIT 1;

  IF latest_created_at IS DISTINCT FROM 1779148800000
     OR latest_hash IS DISTINCT FROM 'a5bcf949b24c1a7f90cb458db9b52366e8cf5ce4ebd3338242502a1701e16c42'
  THEN
    RAISE EXCEPTION
      'Refusing Supabase reconciliation: expected 0079 ledger tip, got created_at=% hash=%',
      latest_created_at,
      latest_hash;
  END IF;

  SELECT count(*)
  INTO integer_experience_columns
  FROM pg_attribute
  WHERE attrelid = 'public.job_posting'::regclass
    AND attname IN ('experience_min', 'experience_max')
    AND atttypid = 'integer'::regtype
    AND NOT attisdropped;

  IF integer_experience_columns <> 2 THEN
    RAISE EXCEPTION
      'Refusing Supabase reconciliation: expected integer experience_min/experience_max, found %',
      integer_experience_columns;
  END IF;
END
$reconcile$;--> statement-breakpoint

ALTER TABLE public.application_interview
  DROP CONSTRAINT IF EXISTS application_interview_type_check;--> statement-breakpoint

ALTER TABLE public.application_interview
  ADD CONSTRAINT application_interview_type_check
  CHECK (
    type IN (
      'interview',
      'phone_screen',
      'video_call',
      'technical',
      'coding',
      'system_design',
      'behavioral',
      'onsite',
      'panel',
      'hiring_manager',
      'other'
    )
  );--> statement-breakpoint

-- Changing a column default does not rewrite or reclassify existing rows.
-- Hold writes through the outer Drizzle transaction and prove that the DDL
-- leaves every existing row's visibility unchanged.
LOCK TABLE public.watchlist IN SHARE MODE;--> statement-breakpoint

CREATE TEMPORARY TABLE jobseek_0083_watchlist_visibility
ON COMMIT DROP
AS
SELECT
  count(*) AS total_count,
  count(*) FILTER (WHERE is_public) AS public_count,
  count(*) FILTER (WHERE NOT is_public) AS private_count
FROM public.watchlist;--> statement-breakpoint

ALTER TABLE public.watchlist
  ALTER COLUMN is_public SET DEFAULT false;--> statement-breakpoint

DO $reconcile$
DECLARE
  active_company_index regclass;
  active_company_index_definition text;
  active_company_index_valid boolean;
  active_company_index_ready boolean;
BEGIN
  active_company_index := to_regclass('public.idx_jp_active_company');

  IF active_company_index IS NULL THEN
    EXECUTE 'CREATE INDEX idx_jp_active_company ON public.job_posting (company_id) WHERE is_active = true';
  ELSE
    SELECT pg_get_indexdef(indexrelid), indisvalid, indisready
    INTO active_company_index_definition, active_company_index_valid, active_company_index_ready
    FROM pg_index
    WHERE indexrelid = active_company_index;

    IF active_company_index_valid IS DISTINCT FROM true
       OR active_company_index_ready IS DISTINCT FROM true
       OR active_company_index_definition IS DISTINCT FROM
          'CREATE INDEX idx_jp_active_company ON public.job_posting USING btree (company_id) WHERE (is_active = true)'
    THEN
      RAISE EXCEPTION
        'Refusing Supabase reconciliation: idx_jp_active_company is not the verified valid partial index: %',
        active_company_index_definition;
    END IF;
  END IF;
END
$reconcile$;--> statement-breakpoint

DO $reconcile$
DECLARE
  watchlist_default text;
  interview_constraint text;
  visibility_unchanged boolean;
BEGIN
  SELECT pg_get_expr(adbin, adrelid)
  INTO watchlist_default
  FROM pg_attrdef
  WHERE adrelid = 'public.watchlist'::regclass
    AND adnum = (
      SELECT attnum
      FROM pg_attribute
      WHERE attrelid = 'public.watchlist'::regclass
        AND attname = 'is_public'
        AND NOT attisdropped
    );

  IF watchlist_default IS DISTINCT FROM 'false' THEN
    RAISE EXCEPTION
      'Supabase reconciliation failed: watchlist.is_public default is %',
      watchlist_default;
  END IF;

  SELECT pg_get_constraintdef(oid, true)
  INTO interview_constraint
  FROM pg_constraint
  WHERE conrelid = 'public.application_interview'::regclass
    AND conname = 'application_interview_type_check';

  IF interview_constraint IS NULL OR interview_constraint NOT LIKE '%''interview''::text%' THEN
    RAISE EXCEPTION
      'Supabase reconciliation failed: application_interview CHECK is %',
      interview_constraint;
  END IF;

  SELECT
    before_counts.total_count = after_counts.total_count
    AND before_counts.public_count = after_counts.public_count
    AND before_counts.private_count = after_counts.private_count
  INTO visibility_unchanged
  FROM jobseek_0083_watchlist_visibility AS before_counts
  CROSS JOIN (
    SELECT
      count(*) AS total_count,
      count(*) FILTER (WHERE is_public) AS public_count,
      count(*) FILTER (WHERE NOT is_public) AS private_count
    FROM public.watchlist
  ) AS after_counts;

  IF visibility_unchanged IS DISTINCT FROM true THEN
    RAISE EXCEPTION
      'Supabase reconciliation changed existing watchlist visibility';
  END IF;
END
$reconcile$;
