-- Running upgrade 0028 -> 0029

COMMIT;

DROP INDEX CONCURRENTLY IF EXISTS idx_desc_r2_claim_reaper;

CREATE INDEX CONCURRENTLY idx_desc_r2_claim_reaper ON descriptions (updated_at, posting_id, locale) WHERE r2_uploaded IS NULL;

BEGIN;

UPDATE alembic_version SET version_num='0029' WHERE alembic_version.version_num = '0028';
