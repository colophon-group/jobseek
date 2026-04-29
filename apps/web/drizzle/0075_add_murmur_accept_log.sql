-- Murmur webhook idempotency ledger (jobseek#2763).
--
-- The accept handler at POST /api/murmur/accept must dedupe deliveries on
-- `Idempotency-Key: <run_id>` per Murmur DESIGN.md §4.2. We don't add a
-- run_id column to the catalog tables themselves (`company`, `job_board`)
-- — those are the operator's truth and shouldn't carry per-run metadata.
-- Instead, this ledger sits next to them: one row per accepted run, with
-- a SHA-256 of the canonical-JSON body so re-fires can be classified as
-- "same body" (idempotent success) vs "different body" (warn + discard).
--
-- The PRIMARY KEY on `run_id` is the UNIQUE constraint the issue
-- requires. The catalog-write happens in the same transaction as the
-- ledger insert, so a crash between them leaves no half-applied state.
CREATE TABLE IF NOT EXISTS "murmur_accept_log" (
  "run_id" text PRIMARY KEY,
  "body_sha256" text NOT NULL,
  "applied_at" timestamptz NOT NULL DEFAULT now(),
  "company_id" uuid REFERENCES "company"("id") ON DELETE SET NULL,
  "board_count" integer NOT NULL DEFAULT 0,
  "target" text NOT NULL
);

CREATE INDEX IF NOT EXISTS "murmur_accept_log_applied_idx"
  ON "murmur_accept_log" ("applied_at");
