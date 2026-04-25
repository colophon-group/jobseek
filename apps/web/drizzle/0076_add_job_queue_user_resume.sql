CREATE TABLE "job_queue" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "user_id" text NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
  "posting_id" uuid NOT NULL REFERENCES "job_posting"("id") ON DELETE CASCADE,
  "added_at" timestamp with time zone DEFAULT now() NOT NULL,
  "overlap_score" real,
  "matched_keywords" text[],
  "missing_keywords" text[],
  "fit_explanation" text,
  "analyzed_at" timestamp with time zone
);

CREATE UNIQUE INDEX "idx_jq_user_posting" ON "job_queue" ("user_id", "posting_id");
CREATE INDEX "idx_jq_user_added" ON "job_queue" ("user_id", "added_at");

CREATE TABLE "user_resume" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "user_id" text UNIQUE NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
  "filename" text NOT NULL,
  "keywords" text[] NOT NULL DEFAULT '{}',
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
