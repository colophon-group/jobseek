-- Rebuild search index to include description (HTML-stripped) at weight C.
-- Title stays at weight A, description at C, employment_type at D.
DROP INDEX IF EXISTS "idx_jp_search_vector";
CREATE INDEX "idx_jp_search_vector" ON "job_posting" USING gin ((
  setweight(to_tsvector('simple'::regconfig, coalesce(title, '')), 'A') ||
  setweight(to_tsvector('simple'::regconfig, regexp_replace(coalesce(description, ''), '<[^>]*>', ' ', 'g')), 'C') ||
  setweight(to_tsvector('simple'::regconfig, coalesce(employment_type, '')), 'D')
));
