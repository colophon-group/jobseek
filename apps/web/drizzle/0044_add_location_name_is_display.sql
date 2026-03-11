-- Add is_display flag to location_name for preferred display name selection.
-- Populated by the GeoNames seed script from isPreferredName/isShortName flags.
ALTER TABLE location_name ADD COLUMN IF NOT EXISTS is_display boolean NOT NULL DEFAULT false;
