-- Running upgrade 0013 -> 0014

COMMIT;

DROP INDEX CONCURRENTLY IF EXISTS idx_jp_active_first_seen;

CREATE INDEX CONCURRENTLY idx_jp_active_first_seen ON job_posting (first_seen_at DESC, id) WHERE is_active;

BEGIN;

UPDATE alembic_version SET version_num='0014' WHERE alembic_version.version_num = '0013';
