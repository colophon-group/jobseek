"""Merge the regional Starbucks row into the canonical company identity.

PR #6315 added the mainland-China Beisen portal as a second company even
though it is a regional Starbucks hiring source.  Moving the board in the CSV
is not enough: company rows are retained in local Postgres when they disappear
from the registry, and existing postings keep their original company UUID.

This bounded migration rehomes every reference owned by the duplicate UUID,
stamps postings for CDC re-export, removes the region-specific descriptions,
and finally deletes the duplicate company.  Fresh databases and deployments
where the merge has already completed are no-ops.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-14
"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


_MERGE_STARBUCKS_COMPANY_IDENTITY = """
DO $migration$
DECLARE
    canonical_id uuid;
    duplicate_id uuid;
BEGIN
    SELECT id INTO canonical_id FROM company WHERE slug = 'starbucks';
    SELECT id INTO duplicate_id FROM company WHERE slug = 'starbucks-china';

    IF duplicate_id IS NULL THEN
        RETURN;
    END IF;
    IF canonical_id IS NULL THEN
        RAISE EXCEPTION
            'cannot merge starbucks-china: canonical starbucks company is missing';
    END IF;

    UPDATE job_board
    SET company_id = canonical_id,
        updated_at = now()
    WHERE company_id = duplicate_id;

    UPDATE job_posting
    SET company_id = canonical_id,
        updated_at = now()
    WHERE company_id = duplicate_id;

    IF to_regclass('public.murmur_accept_log') IS NOT NULL THEN
        EXECUTE
            'UPDATE murmur_accept_log SET company_id = $1 WHERE company_id = $2'
            USING canonical_id, duplicate_id;
    END IF;

    IF to_regclass('public.company_description') IS NOT NULL THEN
        EXECUTE
            'DELETE FROM company_description WHERE company_id = $1'
            USING duplicate_id;
    END IF;

    IF EXISTS (SELECT 1 FROM job_board WHERE company_id = duplicate_id)
       OR EXISTS (SELECT 1 FROM job_posting WHERE company_id = duplicate_id) THEN
        RAISE EXCEPTION
            'cannot delete starbucks-china: crawler references remain';
    END IF;

    DELETE FROM company WHERE id = duplicate_id;
    IF FOUND IS NOT TRUE THEN
        RAISE EXCEPTION 'starbucks-china disappeared during identity merge';
    END IF;
END
$migration$;
"""


def upgrade() -> None:
    op.execute(_MERGE_STARBUCKS_COMPANY_IDENTITY)


def downgrade() -> None:
    # Splitting one employer back into geography-specific identities would
    # require inventing a new UUID and guessing which later postings belong to
    # it.  A live source review is the only safe reversal.
    pass
