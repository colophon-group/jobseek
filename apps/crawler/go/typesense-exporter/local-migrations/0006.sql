-- Running upgrade 0005 -> 0006

ALTER TABLE job_posting
          ALTER COLUMN experience_min TYPE NUMERIC(3,1)
            USING experience_min::numeric(3,1),
          ALTER COLUMN experience_max TYPE NUMERIC(3,1)
            USING experience_max::numeric(3,1);

UPDATE alembic_version SET version_num='0006' WHERE alembic_version.version_num = '0005';
