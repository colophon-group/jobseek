-- Occupation domain table
CREATE TABLE occupation_domain (
  id   serial PRIMARY KEY,
  slug text   NOT NULL UNIQUE
);

-- Occupation domain localized names
CREATE TABLE occupation_domain_name (
  domain_id  integer NOT NULL REFERENCES occupation_domain(id) ON DELETE CASCADE,
  locale     text    NOT NULL,
  name       text    NOT NULL,
  is_display boolean NOT NULL DEFAULT true,
  PRIMARY KEY (domain_id, locale, name)
);
CREATE INDEX idx_domname_lower ON occupation_domain_name(lower(name), locale);
CREATE INDEX idx_domname_display ON occupation_domain_name(domain_id, locale) WHERE is_display;

-- Add domain_id FK to occupation
ALTER TABLE occupation ADD COLUMN domain_id integer REFERENCES occupation_domain(id);
CREATE INDEX idx_occupation_domain ON occupation(domain_id) WHERE domain_id IS NOT NULL;
