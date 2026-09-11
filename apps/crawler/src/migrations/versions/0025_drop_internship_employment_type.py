"""Move internship from employment type to seniority.

Existing rows with the legacy employment type receive the ``intern`` seniority,
and every legacy ``employment_type = 'internship'`` value is cleared. Updating
``updated_at`` publishes the repair through the normal CDC exporter into
Typesense.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-11
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


_MIGRATE_INTERNSHIP_TO_SENIORITY = """
DO $$
DECLARE
    intern_seniority_id integer;
BEGIN
    -- Taxonomy tables are populated by sync rather than Alembic on a brand-new
    -- database, so an empty installation has nothing to migrate yet.
    IF to_regclass('public.seniority') IS NULL THEN
        RETURN;
    END IF;

    SELECT id
    INTO intern_seniority_id
    FROM public.seniority
    WHERE slug = 'intern';

    IF intern_seniority_id IS NULL THEN
        RETURN;
    END IF;

    UPDATE public.job_posting
    SET seniority_id = intern_seniority_id,
        employment_type = NULL,
        updated_at = now()
    WHERE employment_type = 'internship';
END
$$
"""


def upgrade() -> None:
    op.execute(_MIGRATE_INTERNSHIP_TO_SENIORITY)


def downgrade() -> None:
    # The original employment type cannot be distinguished from jobs that
    # already had Intern seniority, so reconstructing it would corrupt data.
    pass
