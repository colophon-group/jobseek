ALTER TABLE occupation ADD COLUMN parent_id integer REFERENCES occupation(id);
CREATE INDEX idx_occupation_parent ON occupation(parent_id) WHERE parent_id IS NOT NULL;
