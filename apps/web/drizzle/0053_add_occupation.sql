CREATE TABLE occupation (
  id   serial PRIMARY KEY,
  slug text   NOT NULL UNIQUE
);

CREATE TABLE occupation_name (
  occupation_id integer NOT NULL REFERENCES occupation(id) ON DELETE CASCADE,
  locale        text    NOT NULL,
  name          text    NOT NULL,
  is_display    boolean NOT NULL DEFAULT true,
  PRIMARY KEY (occupation_id, locale, name)
);
CREATE INDEX idx_occname_lower ON occupation_name(lower(name), locale);
CREATE INDEX idx_occname_display ON occupation_name(occupation_id, locale) WHERE is_display;

ALTER TABLE job_posting ADD COLUMN occupation_id integer REFERENCES occupation(id);
CREATE INDEX idx_jp_occupation ON job_posting(occupation_id) WHERE occupation_id IS NOT NULL;
