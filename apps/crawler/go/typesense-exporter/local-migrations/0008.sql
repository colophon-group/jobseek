-- Running upgrade 0007 -> 0008

COMMIT;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jp_seniority_active_content ON job_posting (seniority_id) WHERE is_active AND seniority_id IS NOT NULL AND description_r2_hash IS NOT NULL AND cardinality(titles) > 0 AND length(trim(titles[1])) > 0;

BEGIN;

UPDATE alembic_version SET version_num='0008' WHERE alembic_version.version_num = '0007';
