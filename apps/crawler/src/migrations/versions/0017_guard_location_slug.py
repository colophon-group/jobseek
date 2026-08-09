"""Prevent new blank location slugs before the canonical-source repair.

The live local taxonomy predates Alembic and currently contains historical
blank slugs.  ``NOT VALID`` rejects future invalid writes without scanning or
blocking deployment on those existing rows.  The protected repair operation
proves parity, fills the rows, and validates this exact constraint atomically.

Revision ID: 0017
Create Date: 2026-08-04
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


_ADD_NONBLANK_LOCATION_SLUG_GUARD = """
DO $migration$
BEGIN
    IF to_regclass('public.location') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM pg_constraint
           WHERE conrelid = to_regclass('public.location')
             AND conname = 'chk_location_slug_nonblank'
       ) THEN
        ALTER TABLE public.location
            ADD CONSTRAINT chk_location_slug_nonblank
            CHECK (slug IS NOT NULL AND btrim(slug) <> '') NOT VALID;
    END IF;
END
$migration$;
"""


def upgrade() -> None:
    op.execute(_ADD_NONBLANK_LOCATION_SLUG_GUARD)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE IF EXISTS public.location DROP CONSTRAINT IF EXISTS chk_location_slug_nonblank"
    )
