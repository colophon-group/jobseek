ALTER TABLE "job_posting" ADD COLUMN "description_pending" text;
ALTER TABLE "job_posting" ADD COLUMN "r2_pending_meta" jsonb;

CREATE INDEX CONCURRENTLY idx_jp_r2_pending
  ON job_posting (id)
  WHERE description_pending IS NOT NULL OR r2_pending_meta IS NOT NULL;
