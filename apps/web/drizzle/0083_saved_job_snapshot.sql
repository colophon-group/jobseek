-- Preserve application-tracker history independently of the crawler mirror.
-- Backfill first: dropping the FK before the copy would make a concurrent
-- posting purge capable of erasing the source fields we need to retain.
ALTER TABLE "saved_job" ADD COLUMN "posting_title" text;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "posting_source_url" text;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "posting_first_seen_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "posting_is_active" boolean DEFAULT true NOT NULL;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "posting_salary_min" integer;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "posting_salary_max" integer;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "posting_salary_currency" text;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "posting_salary_period" text;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "company_id" uuid;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "company_name" text;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "company_slug" text;--> statement-breakpoint
ALTER TABLE "saved_job" ADD COLUMN "company_icon" text;--> statement-breakpoint
UPDATE "saved_job" AS sj
SET
  "posting_title" = jp.titles[1],
  "posting_source_url" = jp.source_url,
  "posting_first_seen_at" = jp.first_seen_at,
  "posting_is_active" = jp.is_active,
  "posting_salary_min" = jp.salary_min,
  "posting_salary_max" = jp.salary_max,
  "posting_salary_currency" = jp.salary_currency,
  "posting_salary_period" = jp.salary_period,
  "company_id" = c.id,
  "company_name" = c.name,
  "company_slug" = c.slug,
  "company_icon" = c.icon
FROM "job_posting" AS jp
JOIN "company" AS c ON c.id = jp.company_id
WHERE sj.job_posting_id = jp.id;--> statement-breakpoint
ALTER TABLE "saved_job" DROP CONSTRAINT IF EXISTS "saved_job_job_posting_id_job_posting_id_fk";
