-- Industry reference table
CREATE TABLE IF NOT EXISTS industry (
  id smallint PRIMARY KEY,
  name text NOT NULL UNIQUE
);

-- New columns on company
ALTER TABLE company ADD COLUMN IF NOT EXISTS industry smallint REFERENCES industry(id);
ALTER TABLE company ADD COLUMN IF NOT EXISTS employee_count_range smallint;
ALTER TABLE company ADD COLUMN IF NOT EXISTS founded_year smallint;
ALTER TABLE company ADD COLUMN IF NOT EXISTS hq_location_id int REFERENCES location(id);
ALTER TABLE company ADD COLUMN IF NOT EXISTS extras jsonb DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_company_industry ON company (industry) WHERE industry IS NOT NULL;
