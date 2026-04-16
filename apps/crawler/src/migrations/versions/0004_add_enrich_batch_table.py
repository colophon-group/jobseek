"""Add enrich_batch table required by batch.py _persist_results.

Revision ID: 0004
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS enrich_batch (
            id                  text PRIMARY KEY,
            provider            text NOT NULL,
            model               text NOT NULL,
            status              text NOT NULL,
            item_count          integer NOT NULL,
            posting_ids         uuid[] NOT NULL,
            estimated_cost_usd  numeric(10,4),
            input_tokens        integer DEFAULT 0,
            output_tokens       integer DEFAULT 0,
            submitted_at        timestamptz DEFAULT now(),
            completed_at        timestamptz
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_eb_status "
        "ON enrich_batch(status) WHERE status = 'submitted'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS enrich_batch")
