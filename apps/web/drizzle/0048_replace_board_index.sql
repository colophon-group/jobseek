DROP INDEX IF EXISTS idx_jp_board;
CREATE INDEX idx_jp_board_url ON job_posting (board_id, source_url);
