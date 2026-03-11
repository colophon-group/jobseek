-- Stored search_vector column, maintained by trigger.
-- Eliminates runtime regexp_replace + to_tsvector on every query.

ALTER TABLE job_posting ADD COLUMN search_vector tsvector;

-- Trigger function to keep search_vector in sync
CREATE OR REPLACE FUNCTION job_posting_search_vector_update() RETURNS trigger AS $$
BEGIN
  NEW.search_vector :=
    setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
    setweight(to_tsvector('simple', regexp_replace(coalesce(NEW.description, ''), '<[^>]*>', ' ', 'g')), 'C') ||
    setweight(to_tsvector('simple', coalesce(NEW.employment_type, '')), 'D');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_job_posting_search_vector
  BEFORE INSERT OR UPDATE OF title, description, employment_type ON job_posting
  FOR EACH ROW
  EXECUTE FUNCTION job_posting_search_vector_update();

-- Backfill existing rows
UPDATE job_posting SET search_vector =
  setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('simple', regexp_replace(coalesce(description, ''), '<[^>]*>', ' ', 'g')), 'C') ||
  setweight(to_tsvector('simple', coalesce(employment_type, '')), 'D');

-- Replace expensive functional index with simple column index
DROP INDEX IF EXISTS idx_jp_search_vector;
CREATE INDEX idx_jp_search_vector ON job_posting USING gin (search_vector);
