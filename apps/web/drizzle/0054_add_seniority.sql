-- Seniority taxonomy tables (mirrors occupation pattern)
CREATE TABLE IF NOT EXISTS seniority (
  id   serial PRIMARY KEY,
  slug text   NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS seniority_name (
  seniority_id integer NOT NULL REFERENCES seniority(id) ON DELETE CASCADE,
  locale       text    NOT NULL,
  name         text    NOT NULL,
  is_display   boolean NOT NULL DEFAULT true,
  PRIMARY KEY (seniority_id, locale, name)
);

CREATE INDEX IF NOT EXISTS idx_senname_lower ON seniority_name(lower(name), locale);
CREATE INDEX IF NOT EXISTS idx_senname_display ON seniority_name(seniority_id, locale) WHERE is_display;

ALTER TABLE job_posting ADD COLUMN IF NOT EXISTS seniority_id integer REFERENCES seniority(id);
CREATE INDEX IF NOT EXISTS idx_jp_seniority ON job_posting(seniority_id) WHERE seniority_id IS NOT NULL;
