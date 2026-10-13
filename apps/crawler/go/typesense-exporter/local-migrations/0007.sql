-- Running upgrade 0006 -> 0007

CREATE TABLE IF NOT EXISTS currency_rate (
          currency TEXT PRIMARY KEY,
          to_eur NUMERIC NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

UPDATE alembic_version SET version_num='0007' WHERE alembic_version.version_num = '0006';
