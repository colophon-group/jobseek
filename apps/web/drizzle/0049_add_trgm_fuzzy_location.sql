CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX idx_locname_trgm
  ON location_name USING GIN (lower(name) gin_trgm_ops);
