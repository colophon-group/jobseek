"""Add company table to local Postgres.

Previously company data only existed in Supabase, but the exporter
needs it locally for denormalization (Typesense docs).  This makes
local Postgres the source of truth for company metadata.

Also adds chk_location_arrays_length to catch mismatched arrays early
(matching the Supabase constraint that was blocking exports).

Revision ID: 0002
Create Date: 2026-04-07
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
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
        )
    """)

    # Match the Supabase constraint that was blocking exports
    op.execute("""
        ALTER TABLE job_posting
        ADD CONSTRAINT chk_location_arrays_length
        CHECK (
            location_ids IS NULL
            OR location_types IS NULL
            OR array_length(location_ids, 1) = array_length(location_types, 1)
        )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE job_posting DROP CONSTRAINT IF EXISTS chk_location_arrays_length")
    op.execute("DROP TABLE IF EXISTS company")
