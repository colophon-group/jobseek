"""Track IndexNow submissions with a content-hash diff table.

The crawler notifies IndexNow (Bing, Yandex, Seznam, Naver, Microsoft
Yep) when company page content actually changes. The table stores one
row per submitted URL with a content hash of the fields that affect
bot-visible HTML; the notifier submits only URLs whose current hash
differs from the last-submitted hash.

Google does not participate in IndexNow — see docs/context.

Revision ID: 0003
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_indexnow_submitted")
    op.execute("DROP TABLE IF EXISTS indexnow_submission")
