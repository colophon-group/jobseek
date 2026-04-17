"""Add resume_score table.

Revision ID: 0005
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS resume_score (
            posting_id    uuid PRIMARY KEY REFERENCES job_posting(id) ON DELETE CASCADE,
            resume_hash   text NOT NULL,
            overlap_score numeric(5,2) NOT NULL,
            explanation   text,
            scored_at     timestamptz DEFAULT now(),
            explained_at  timestamptz
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_resume_score_overlap "
        "ON resume_score(overlap_score DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS resume_score")
