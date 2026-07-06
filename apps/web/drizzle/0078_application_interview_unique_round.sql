-- Enforce uniqueness of (saved_job_id, round) on application_interview
-- to close the duplicate-round race window between concurrent
-- `addInterview` calls and the `updateJobStatus` auto-create branch.
--
-- Backstory: #3114 / #3160 — both call sites in my-jobs.ts compute the
-- next round number with `SELECT max(round) + 1` and then `INSERT`. Two
-- concurrent callers both read `max=N`, both insert `round=N+1`, and the
-- application layer is the sole enforcer of round uniqueness. The
-- existing `idx_ai_saved_job_round` is a non-unique B-tree index, so the
-- database happily accepts duplicates. The deletion path
-- (`deleteInterview`) then renumbers ORDER BY round, leaving the two
-- duplicates permanently glued together.
--
-- Fix: promote the index to a uniqueness constraint. This converts the
-- race into a `unique_violation` Postgres error, which the application
-- layer now retries (`addInterview`) or absorbs as a no-op
-- (`updateJobStatus` auto-create, via ON CONFLICT DO NOTHING).
--
-- NOTE on duplicates: if any duplicates already exist (low-probability
-- but possible — both filed issues classify it as "low–medium"), this
-- migration will fail at the UNIQUE INDEX build step. We dedupe first by
-- keeping the lowest `created_at` per (saved_job_id, round) and renumber
-- the rest. The dedupe is a one-shot cleanup; ongoing protection comes
-- from the uniqueness constraint built afterwards.

BEGIN;

-- Dedupe existing rows: for each (saved_job_id, round) with duplicates,
-- keep the earliest (lowest created_at, then lowest id as a stable
-- tiebreak) and shift the rest to fresh round numbers at the tail.
WITH ranked AS (
  SELECT
    id,
    saved_job_id,
    round,
    row_number() OVER (
      PARTITION BY saved_job_id, round
      ORDER BY created_at ASC, id ASC
    ) AS rn
  FROM application_interview
),
max_per_job AS (
  SELECT saved_job_id, max(round) AS mx
  FROM application_interview
  GROUP BY saved_job_id
),
to_shift AS (
  SELECT
    r.id,
    r.saved_job_id,
    m.mx + row_number() OVER (
      PARTITION BY r.saved_job_id
      ORDER BY r.round ASC, r.id ASC
    ) AS new_round
  FROM ranked r
  JOIN max_per_job m ON m.saved_job_id = r.saved_job_id
  WHERE r.rn > 1
)
UPDATE application_interview ai
SET round = ts.new_round
FROM to_shift ts
WHERE ai.id = ts.id;

-- Drop the non-unique companion index — superseded by the unique index.
DROP INDEX IF EXISTS idx_ai_saved_job_round;

-- Promote to UNIQUE. Same column list, same name, so Drizzle's snapshot
-- stays aligned with the schema.ts uniqueIndex(...) declaration.
CREATE UNIQUE INDEX idx_ai_saved_job_round
  ON application_interview (saved_job_id, round);

COMMIT;
