-- Application tracker: add pipeline columns to saved_job + interview table

ALTER TABLE "saved_job"
  ADD COLUMN "status" text NOT NULL DEFAULT 'saved',
  ADD COLUMN "status_changed_at" timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN "applied_at" timestamptz,
  ADD COLUMN "rejected_at" timestamptz,
  ADD COLUMN "offered_at" timestamptz,
  ADD COLUMN "salary_min_override" integer,
  ADD COLUMN "salary_max_override" integer,
  ADD COLUMN "salary_currency_override" text,
  ADD COLUMN "salary_period_override" text;

ALTER TABLE "saved_job"
  ADD CONSTRAINT "saved_job_status_check"
  CHECK (status IN ('saved', 'applied', 'interviewing', 'offered', 'rejected'));

CREATE TABLE "application_interview" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "saved_job_id" uuid NOT NULL REFERENCES "saved_job"("id") ON DELETE CASCADE,
  "round" smallint NOT NULL,
  "type" text NOT NULL,
  "scheduled_at" timestamptz,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT "application_interview_type_check"
    CHECK (type IN ('phone_screen','video_call','technical','coding','system_design','behavioral','onsite','panel','hiring_manager','other'))
);

CREATE INDEX "idx_sj_user_status" ON "saved_job" ("user_id", "status");
CREATE INDEX "idx_sj_user_status_changed" ON "saved_job" ("user_id", "status_changed_at");
CREATE INDEX "idx_ai_saved_job_round" ON "application_interview" ("saved_job_id", "round");
