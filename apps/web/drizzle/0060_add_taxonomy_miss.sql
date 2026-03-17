-- Taxonomy miss tracking table
CREATE TABLE taxonomy_miss (
  id            serial      PRIMARY KEY,
  taxonomy      text        NOT NULL,
  raw_value     text        NOT NULL,
  sample_value  text        NOT NULL,
  hit_count     integer     NOT NULL DEFAULT 1,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  status        text        NOT NULL DEFAULT 'pending',
  resolved_to   text,
  UNIQUE (taxonomy, raw_value)
);

CREATE INDEX idx_tm_pending ON taxonomy_miss (taxonomy, hit_count DESC)
  WHERE status = 'pending';

-- Technology table
CREATE TABLE technology (
  id   serial PRIMARY KEY,
  slug text   NOT NULL UNIQUE
);

-- Technology IDs on job_posting
ALTER TABLE job_posting ADD COLUMN technology_ids integer[];
CREATE INDEX idx_jp_technology_ids ON job_posting USING gin (technology_ids)
  WHERE technology_ids IS NOT NULL;
