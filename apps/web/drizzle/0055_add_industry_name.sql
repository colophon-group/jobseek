-- Industry localization table (mirrors occupation_name / seniority_name pattern)
CREATE TABLE IF NOT EXISTS industry_name (
  industry_id smallint NOT NULL REFERENCES industry(id) ON DELETE CASCADE,
  locale      text     NOT NULL,
  name        text     NOT NULL,
  is_display  boolean  NOT NULL DEFAULT true,
  PRIMARY KEY (industry_id, locale, name)
);

CREATE INDEX IF NOT EXISTS idx_indname_lower ON industry_name(lower(name), locale);
CREATE INDEX IF NOT EXISTS idx_indname_display ON industry_name(industry_id, locale) WHERE is_display;
