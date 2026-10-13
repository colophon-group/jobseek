-- Running upgrade 0002 -> 0003

CREATE TABLE IF NOT EXISTS indexnow_submission (
            url                 TEXT PRIMARY KEY,
            content_hash        TEXT NOT NULL,
            last_submitted_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_indexnow_submitted ON indexnow_submission (last_submitted_at);

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';
