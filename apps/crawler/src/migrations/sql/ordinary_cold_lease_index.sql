-- Cold admission checks must not scan millions of unleased postings while
-- holding every writer barrier. Legacy leases occupy only a small subset.
CREATE INDEX IF NOT EXISTS idx_job_posting_cold_lease
ON public.job_posting (leased_until) WHERE leased_until IS NOT NULL;
