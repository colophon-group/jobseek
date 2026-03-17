-- Localized company descriptions (English canonical stays in company.description)
CREATE TABLE IF NOT EXISTS company_description (
  company_id uuid    NOT NULL REFERENCES company(id) ON DELETE CASCADE,
  locale     text    NOT NULL,
  description text   NOT NULL,
  PRIMARY KEY (company_id, locale)
);
