-- Running upgrade 0009 -> 0010

ALTER TABLE descriptions ADD COLUMN IF NOT EXISTS r2_upload_failures INTEGER NOT NULL DEFAULT 0, ADD COLUMN IF NOT EXISTS r2_next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity'::timestamptz;

COMMIT;

DROP INDEX CONCURRENTLY IF EXISTS idx_desc_not_uploaded;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_desc_r2_ready ON descriptions (r2_next_attempt_at, posting_id, locale) WHERE r2_uploaded = false;

BEGIN;

UPDATE alembic_version SET version_num='0010' WHERE alembic_version.version_num = '0009';
