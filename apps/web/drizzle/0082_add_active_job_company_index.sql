-- Watchlist summaries count active postings for every tracked company.
-- The existing company index still scans inactive history before applying
-- `is_active`, which makes the overview query time out for larger lists.
CREATE INDEX IF NOT EXISTS idx_jp_active_company
  ON job_posting (company_id)
  WHERE is_active = true;
