-- Expand phase for removing saved_job's dependency on the crawler mirror.
--
-- The compatibility trigger is installed before the backfill so old web
-- instances that know only job_posting_id cannot create a snapshot-less row.
-- The outbound FK deliberately remains until the dual-write/read application
-- release is proven and the separate 0085 contract migration is approved.

ALTER TABLE public.saved_job ADD COLUMN posting_title text;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN posting_source_url text;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN posting_first_seen_at timestamp with time zone;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN posting_is_active boolean;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN posting_salary_min integer;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN posting_salary_max integer;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN posting_salary_currency text;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN posting_salary_period text;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN company_id uuid;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN company_name text;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN company_slug text;--> statement-breakpoint
ALTER TABLE public.saved_job ADD COLUMN company_icon text;--> statement-breakpoint

CREATE FUNCTION public.saved_job_snapshot_from_mirror()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $snapshot$
DECLARE
  source_posting record;
BEGIN
  SELECT
    jp.titles[1] AS title,
    jp.source_url,
    jp.first_seen_at,
    jp.is_active,
    jp.salary_min,
    jp.salary_max,
    jp.salary_currency,
    jp.salary_period,
    c.id AS company_id,
    c.name AS company_name,
    c.slug AS company_slug,
    c.icon AS company_icon
  INTO source_posting
  FROM public.job_posting AS jp
  JOIN public.company AS c ON c.id = jp.company_id
  WHERE jp.id = NEW.job_posting_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION
      'Cannot snapshot saved job: posting % is absent from the Supabase mirror',
      NEW.job_posting_id;
  END IF;

  NEW.posting_title := COALESCE(
    NULLIF(btrim(NEW.posting_title), ''),
    source_posting.title
  );
  NEW.posting_source_url := COALESCE(
    NULLIF(btrim(NEW.posting_source_url), ''),
    source_posting.source_url
  );
  NEW.posting_first_seen_at := COALESCE(
    NEW.posting_first_seen_at,
    source_posting.first_seen_at
  );
  NEW.posting_is_active := COALESCE(
    NEW.posting_is_active,
    source_posting.is_active
  );
  NEW.posting_salary_min := COALESCE(
    NEW.posting_salary_min,
    source_posting.salary_min
  );
  NEW.posting_salary_max := COALESCE(
    NEW.posting_salary_max,
    source_posting.salary_max
  );
  NEW.posting_salary_currency := COALESCE(
    NEW.posting_salary_currency,
    source_posting.salary_currency
  );
  NEW.posting_salary_period := COALESCE(
    NEW.posting_salary_period,
    source_posting.salary_period
  );
  NEW.company_id := COALESCE(NEW.company_id, source_posting.company_id);
  NEW.company_name := COALESCE(
    NULLIF(btrim(NEW.company_name), ''),
    source_posting.company_name
  );
  NEW.company_slug := COALESCE(
    NULLIF(btrim(NEW.company_slug), ''),
    source_posting.company_slug
  );
  NEW.company_icon := COALESCE(NEW.company_icon, source_posting.company_icon);

  IF NULLIF(btrim(NEW.posting_title), '') IS NULL
     OR NULLIF(btrim(NEW.posting_source_url), '') IS NULL
     OR NEW.posting_first_seen_at IS NULL
     OR NEW.posting_is_active IS NULL
     OR NEW.company_id IS NULL
     OR NULLIF(btrim(NEW.company_name), '') IS NULL
     OR NULLIF(btrim(NEW.company_slug), '') IS NULL
  THEN
    RAISE EXCEPTION
      'Cannot save posting % with an incomplete required snapshot',
      NEW.job_posting_id;
  END IF;

  RETURN NEW;
END
$snapshot$;--> statement-breakpoint

CREATE TRIGGER saved_job_snapshot_from_mirror_before_insert
BEFORE INSERT ON public.saved_job
FOR EACH ROW
EXECUTE FUNCTION public.saved_job_snapshot_from_mirror();--> statement-breakpoint

UPDATE public.saved_job AS sj
SET
  posting_title = jp.titles[1],
  posting_source_url = jp.source_url,
  posting_first_seen_at = jp.first_seen_at,
  posting_is_active = jp.is_active,
  posting_salary_min = jp.salary_min,
  posting_salary_max = jp.salary_max,
  posting_salary_currency = jp.salary_currency,
  posting_salary_period = jp.salary_period,
  company_id = c.id,
  company_name = c.name,
  company_slug = c.slug,
  company_icon = c.icon
FROM public.job_posting AS jp
JOIN public.company AS c ON c.id = jp.company_id
WHERE sj.job_posting_id = jp.id;--> statement-breakpoint

DO $snapshot$
DECLARE
  saved_job_count bigint;
  complete_snapshot_count bigint;
BEGIN
  SELECT
    count(*),
    count(*) FILTER (
      WHERE NULLIF(btrim(posting_title), '') IS NOT NULL
        AND NULLIF(btrim(posting_source_url), '') IS NOT NULL
        AND posting_first_seen_at IS NOT NULL
        AND posting_is_active IS NOT NULL
        AND company_id IS NOT NULL
        AND NULLIF(btrim(company_name), '') IS NOT NULL
        AND NULLIF(btrim(company_slug), '') IS NOT NULL
    )
  INTO saved_job_count, complete_snapshot_count
  FROM public.saved_job;

  IF complete_snapshot_count <> saved_job_count THEN
    RAISE EXCEPTION
      'Saved-job expand failed: % of % required snapshots are complete',
      complete_snapshot_count,
      saved_job_count;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'public.saved_job'::regclass
      AND confrelid = 'public.job_posting'::regclass
      AND contype = 'f'
  ) THEN
    RAISE EXCEPTION
      'Saved-job expand failed: job_posting FK must remain until contract';
  END IF;
END
$snapshot$;
