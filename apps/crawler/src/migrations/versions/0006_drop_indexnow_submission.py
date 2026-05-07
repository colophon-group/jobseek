"""Drop the indexnow_submission tracking table.

The IndexNow notifier was retired in #2821 (companies left the index
under noindex,follow + sitemap exclusion) and the residual web + key
+ key-route surface was retired in #2843, the same PR that adds this
migration. Once the writer is gone, the diff-tracking table is dead
storage; drop it.

Idempotent: ``DROP TABLE IF EXISTS`` and matching index drop, so
re-running on a database that never received 0003 (fresh dev / new
environment) is a no-op.

Revision ID: 0006
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_indexnow_submitted")
    op.execute("DROP TABLE IF EXISTS indexnow_submission")


def downgrade() -> None:
    # 0003's body, kept verbatim so a downgrade from 0006 lands on
    # the schema 0003 left behind. Out of practical scope (no caller
    # left to write to it), but Alembic chains expect a roundtrip.
    op.execute("""
        CREATE TABLE IF NOT EXISTS indexnow_submission (
            url                 TEXT PRIMARY KEY,
            content_hash        TEXT NOT NULL,
            last_submitted_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_indexnow_submitted "
        "ON indexnow_submission (last_submitted_at)"
    )
