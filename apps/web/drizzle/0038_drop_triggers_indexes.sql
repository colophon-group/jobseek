-- Phase 5: Drop old trigger + indexes (run AFTER dual-write verified)
DROP TRIGGER IF EXISTS trg_job_posting_search_vector ON job_posting;
DROP FUNCTION IF EXISTS job_posting_search_vector_update();
DROP INDEX IF EXISTS idx_jp_search_vector;
DROP INDEX IF EXISTS idx_jp_locations;
DROP INDEX IF EXISTS idx_jp_employment_type;
DROP INDEX IF EXISTS idx_jp_status_active;
DROP INDEX IF EXISTS idx_jp_last_seen_active;
DROP INDEX IF EXISTS idx_jp_language;
