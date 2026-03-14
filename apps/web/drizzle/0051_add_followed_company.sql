CREATE TABLE IF NOT EXISTS "followed_company" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "user_id" text NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
  "company_id" uuid NOT NULL REFERENCES "company"("id") ON DELETE CASCADE,
  "followed_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS "idx_fc_user_company" ON "followed_company" ("user_id", "company_id");
CREATE INDEX IF NOT EXISTS "idx_fc_user_followed_at" ON "followed_company" ("user_id", "followed_at");
