"""Add durable R2 upload retry scheduling.

Failed description uploads previously returned directly to the same pending
queue with no attempt state. Under an R2 5xx burst, two producers could claim
the same immediately eligible row repeatedly. The retry timestamp makes the
cooldown durable across processes and deploys; the partial ready index keeps
ordered claims cheap.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE descriptions "
        "ADD COLUMN IF NOT EXISTS r2_upload_failures INTEGER NOT NULL DEFAULT 0, "
        "ADD COLUMN IF NOT EXISTS r2_next_attempt_at TIMESTAMPTZ "
        "NOT NULL DEFAULT '-infinity'::timestamptz"
    )
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_desc_not_uploaded")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_desc_r2_ready "
            "ON descriptions (r2_next_attempt_at, posting_id, locale) "
            "WHERE r2_uploaded = false"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_desc_r2_ready")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_desc_not_uploaded "
            "ON descriptions (r2_uploaded) WHERE r2_uploaded = false"
        )
    op.execute(
        "ALTER TABLE descriptions "
        "DROP COLUMN IF EXISTS r2_next_attempt_at, "
        "DROP COLUMN IF EXISTS r2_upload_failures"
    )
