-- Search index using a functional GIN index (no stored column needed).
-- This avoids the storage overhead of a STORED generated column.
CREATE INDEX "idx_jp_search_vector" ON "job_posting" USING gin ((
  setweight(to_tsvector('simple'::regconfig, coalesce(title, '')), 'A') ||
  setweight(to_tsvector('simple'::regconfig, coalesce(employment_type, '')), 'D')
));
