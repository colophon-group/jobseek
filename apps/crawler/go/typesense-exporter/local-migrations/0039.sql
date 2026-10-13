-- Running upgrade 0038 -> 0039

COMMIT;

DROP INDEX CONCURRENTLY IF EXISTS public.idx_jp_board_id_cursor;

CREATE INDEX CONCURRENTLY idx_jp_board_id_cursor ON public.job_posting (board_id, id);

BEGIN;

UPDATE alembic_version SET version_num='0039' WHERE alembic_version.version_num = '0038';
