-- Location normalization: structured location tables seeded from GeoNames

CREATE TYPE location_type AS ENUM ('macro', 'country', 'region', 'city');

CREATE TABLE location (
  id          integer PRIMARY KEY,
  parent_id   integer REFERENCES location(id),
  type        location_type NOT NULL,
  population  integer,
  lat         real,
  lng         real
);
CREATE INDEX idx_loc_parent ON location(parent_id);
CREATE INDEX idx_loc_type   ON location(type);

CREATE TABLE location_name (
  location_id integer NOT NULL REFERENCES location(id) ON DELETE CASCADE,
  locale      text    NOT NULL,
  name        text    NOT NULL,
  PRIMARY KEY (location_id, locale)
);
CREATE INDEX idx_locname_lower ON location_name(lower(name), locale);

-- Junction table: macro regions → member countries (many-to-many)
CREATE TABLE location_macro_member (
  macro_id   integer NOT NULL REFERENCES location(id) ON DELETE CASCADE,
  country_id integer NOT NULL REFERENCES location(id) ON DELETE CASCADE,
  PRIMARY KEY (macro_id, country_id)
);

-- location_ids and location_types columns already added in 0031_add_r2_migration_columns.sql
-- Just create the GIN index here
CREATE INDEX IF NOT EXISTS idx_jp_location_ids ON job_posting USING GIN(location_ids);

-- Validate location_types values
ALTER TABLE job_posting ADD CONSTRAINT chk_location_types
  CHECK (location_types <@ ARRAY['onsite', 'remote', 'hybrid']::text[]);

-- Validate parallel array lengths match
ALTER TABLE job_posting ADD CONSTRAINT chk_location_arrays_length
  CHECK (
    (location_ids IS NULL AND location_types IS NULL)
    OR array_length(location_ids, 1) = array_length(location_types, 1)
  );
