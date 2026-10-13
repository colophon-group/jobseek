-- Running upgrade 0008 -> 0009

COMMIT;

DROP INDEX CONCURRENTLY IF EXISTS idx_jp_seniority_active_content;

BEGIN;

UPDATE alembic_version SET version_num='0009' WHERE alembic_version.version_num = '0008';
