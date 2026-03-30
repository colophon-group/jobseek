"""Initial schema for local crawler Postgres.

Creates the core tables: job_board, job_posting, descriptions, exporter_state.

Revision ID: 0001
Create Date: 2026-03-27
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- job_board ----------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS job_board (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL,
            board_slug TEXT UNIQUE,
            crawler_type TEXT,
            board_url TEXT NOT NULL UNIQUE,
            check_interval_minutes INTEGER NOT NULL DEFAULT 60,
            next_check_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_checked_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            is_enabled BOOLEAN NOT NULL DEFAULT true,
            board_status TEXT NOT NULL DEFAULT 'active',
            throttle_key TEXT,
            lease_owner TEXT,
            leased_until TIMESTAMPTZ,
            empty_check_count INTEGER NOT NULL DEFAULT 0,
            last_non_empty_at TIMESTAMPTZ,
            gone_at TIMESTAMPTZ,
            metadata JSONB DEFAULT '{}',
            scrape_interval_hours INTEGER NOT NULL DEFAULT 24,
            monitor_needs_browser BOOLEAN NOT NULL DEFAULT false,
            scraper_needs_browser BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_jb_company ON job_board(company_id)")

    # -- job_posting --------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS job_posting (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL,
            board_id UUID REFERENCES job_board(id) ON DELETE SET NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            locales TEXT[] NOT NULL DEFAULT '{}',
            titles TEXT[] NOT NULL DEFAULT '{}',
            location_ids INTEGER[],
            location_types TEXT[],
            description_r2_hash BIGINT,
            employment_type TEXT,
            source_url TEXT NOT NULL UNIQUE,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ,
            next_scrape_at TIMESTAMPTZ,
            last_scraped_at TIMESTAMPTZ,
            leased_until TIMESTAMPTZ,
            scrape_failures INTEGER NOT NULL DEFAULT 0,
            missing_count INTEGER NOT NULL DEFAULT 0,
            salary_min INTEGER,
            salary_max INTEGER,
            salary_currency TEXT,
            salary_period TEXT,
            salary_eur INTEGER,
            experience_min INTEGER,
            experience_max INTEGER,
            occupation_id INTEGER,
            seniority_id INTEGER,
            technology_ids INTEGER[],
            enrichment JSONB,
            to_be_enriched BOOLEAN NOT NULL DEFAULT true,
            enrich_version INTEGER NOT NULL DEFAULT 0,
            last_enriched_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_jp_company ON job_posting(company_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_jp_board ON job_posting(board_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_jp_active ON job_posting(is_active) WHERE is_active = true"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_jp_updated ON job_posting(updated_at)")

    # -- descriptions -------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS descriptions (
            posting_id UUID NOT NULL,
            locale TEXT NOT NULL,
            html TEXT NOT NULL,
            hash BIGINT NOT NULL,
            r2_uploaded BOOLEAN DEFAULT false,
            updated_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (posting_id, locale)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_desc_locale ON descriptions(locale)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_desc_not_uploaded "
        "ON descriptions(r2_uploaded) WHERE r2_uploaded = false"
    )

    # -- exporter_state -----------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS exporter_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS exporter_state")
    op.execute("DROP TABLE IF EXISTS descriptions")
    op.execute("DROP TABLE IF EXISTS job_posting")
    op.execute("DROP TABLE IF EXISTS job_board")
