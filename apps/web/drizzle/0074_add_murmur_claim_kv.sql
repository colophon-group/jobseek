-- Per-claim KV table for jobseek's named-config state (jobseek#2757).
CREATE TABLE IF NOT EXISTS "murmur_claim_kv" (
  "claim_token" text NOT NULL,
  "name" text NOT NULL,
  "value" jsonb NOT NULL,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("claim_token", "name")
);
CREATE INDEX IF NOT EXISTS "murmur_claim_kv_token_idx"
  ON "murmur_claim_kv" ("claim_token");
