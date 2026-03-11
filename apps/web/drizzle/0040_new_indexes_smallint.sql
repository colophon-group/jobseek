-- Phase 5: Smallint conversion + new search index (runs after column drops)
ALTER TABLE job_posting ALTER COLUMN scrape_failures TYPE smallint;
ALTER TABLE job_posting ALTER COLUMN missing_count TYPE smallint;

CREATE INDEX idx_jp_search_vector ON job_posting USING gin ((
  setweight(to_tsvector('simple', coalesce(titles[1], '')), 'A') ||
  setweight(to_tsvector('simple', coalesce(employment_type, '')), 'D')
));

DROP INDEX IF EXISTS idx_jp_next_scrape;
CREATE INDEX idx_jp_next_scrape ON job_posting (next_scrape_at)
  WHERE is_active = true AND next_scrape_at IS NOT NULL;
