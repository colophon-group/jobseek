-- Running upgrade 0018 -> 0019

COMMIT;

DROP INDEX CONCURRENTLY IF EXISTS idx_jp_board_active_last_seen;

CREATE INDEX CONCURRENTLY idx_jp_board_active_last_seen ON job_posting (board_id, last_seen_at) WHERE is_active;

BEGIN;

UPDATE alembic_version SET version_num='0019' WHERE alembic_version.version_num = '0018';
