"""Index and bound orphaned R2 description-claim recovery.

The drain reaper selects ``r2_uploaded IS NULL`` rows by age.  The existing
pending-upload index only covers ``r2_uploaded = false``, so a stale-claim
sweep can otherwise scan the full descriptions table and exceed the
production statement timeout.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_desc_r2_claim_reaper"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # A canceled concurrent build leaves an invalid relation behind.
        # Dropping first makes a migration retry converge on the exact shape.
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        op.execute(
            "CREATE INDEX CONCURRENTLY "
            f"{INDEX_NAME} ON descriptions (updated_at, posting_id, locale) "
            "WHERE r2_uploaded IS NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
