ALTER TABLE location ADD COLUMN slug text;
CREATE UNIQUE INDEX idx_loc_slug ON location(slug);
