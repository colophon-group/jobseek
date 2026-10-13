-- Running upgrade 0037 -> 0038

-- Cold admission checks must not scan millions of unleased postings while
-- holding every writer barrier. Legacy leases occupy only a small subset.
CREATE INDEX IF NOT EXISTS idx_job_posting_cold_lease
ON public.job_posting (leased_until) WHERE leased_until IS NOT NULL;;

UPDATE alembic_version SET version_num='0038' WHERE alembic_version.version_num = '0037';
