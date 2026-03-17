ALTER TABLE technology ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE technology ADD COLUMN IF NOT EXISTS category text;
UPDATE technology SET name = slug WHERE name IS NULL;
