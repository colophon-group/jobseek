CREATE INDEX IF NOT EXISTS idx_occname_search ON occupation_name (lower(name) text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_senname_search ON seniority_name (lower(name) text_pattern_ops);
