-- Currency exchange rate table (ECB daily rates, refreshed weekly)
CREATE TABLE currency_rate (
  currency   text PRIMARY KEY,
  to_eur     numeric(10,6) NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Seed with approximate rates; refresh_currency_rates.py updates from ECB.
INSERT INTO currency_rate (currency, to_eur) VALUES
  ('EUR', 1.000000),
  ('USD', 0.870000), ('GBP', 1.170000), ('CHF', 1.040000), ('CAD', 0.670000),
  ('SEK', 0.089000), ('NOK', 0.086000), ('DKK', 0.134000),
  ('PLN', 0.233000), ('CZK', 0.040000), ('HUF', 0.002500), ('RON', 0.201000),
  ('AUD', 0.580000), ('NZD', 0.530000), ('JPY', 0.006100),
  ('ISK', 0.006800), ('TRY', 0.026000), ('BGN', 0.511000),
  ('BRL', 0.160000), ('CNY', 0.126000), ('HKD', 0.112000),
  ('IDR', 0.000055), ('INR', 0.010300), ('KRW', 0.000650),
  ('MXN', 0.048000), ('MYR', 0.200000), ('PHP', 0.016000),
  ('SGD', 0.680000), ('THB', 0.025000), ('ZAR', 0.050000),
  ('ILS', 0.250000);

-- Salary + experience columns on job_posting
ALTER TABLE job_posting ADD COLUMN salary_min      integer;
ALTER TABLE job_posting ADD COLUMN salary_max      integer;
ALTER TABLE job_posting ADD COLUMN salary_currency text;
ALTER TABLE job_posting ADD COLUMN salary_period   text;
ALTER TABLE job_posting ADD COLUMN salary_eur      integer;
ALTER TABLE job_posting ADD COLUMN experience_min  integer;
ALTER TABLE job_posting ADD COLUMN experience_max  integer;

-- salary_eur is pre-computed (salary_min * to_eur) for fast cross-currency filtering.
-- Refreshed daily by refresh_currency_rates.py when exchange rates change.
CREATE INDEX idx_jp_salary_eur ON job_posting (salary_eur)
  WHERE salary_eur IS NOT NULL;
CREATE INDEX idx_jp_experience_min ON job_posting (experience_min)
  WHERE experience_min IS NOT NULL;

-- Display currency preference
ALTER TABLE user_preferences ADD COLUMN display_currency text NOT NULL DEFAULT 'EUR';
