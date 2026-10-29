-- Running upgrade 0040 -> 0041

ALTER TABLE public.ordinary_worker_write_fence ADD COLUMN stable_config_sha256 TEXT;

UPDATE alembic_version SET version_num='0041' WHERE alembic_version.version_num = '0040';
