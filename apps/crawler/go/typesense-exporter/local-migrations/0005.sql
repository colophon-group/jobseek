-- Running upgrade 0004 -> 0005

COMMIT;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jp_company_active ON job_posting (company_id) WHERE is_active;

BEGIN;

UPDATE alembic_version SET version_num='0005' WHERE alembic_version.version_num = '0004';
