-- Running upgrade 0001 -> 0002

CREATE TABLE IF NOT EXISTS company (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            website TEXT,
            logo TEXT,
            icon TEXT,
            logo_type TEXT,
            industry SMALLINT,
            employee_count_range SMALLINT,
            founded_year SMALLINT,
            extras JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

ALTER TABLE job_posting
        ADD CONSTRAINT chk_location_arrays_length
        CHECK (
            location_ids IS NULL
            OR location_types IS NULL
            OR array_length(location_ids, 1) = array_length(location_types, 1)
        );

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';
