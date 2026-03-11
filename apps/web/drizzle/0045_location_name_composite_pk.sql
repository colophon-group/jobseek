-- Fix location_name PK to be (location_id, locale, name) and add display index
ALTER TABLE location_name DROP CONSTRAINT IF EXISTS location_name_pkey;
ALTER TABLE location_name ADD PRIMARY KEY (location_id, locale, name);

-- Fast display name lookups
CREATE INDEX IF NOT EXISTS idx_locname_display
  ON location_name(location_id, locale) WHERE is_display = true;
