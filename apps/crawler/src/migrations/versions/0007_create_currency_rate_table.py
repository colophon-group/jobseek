"""Create currency_rate lookup table when absent.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-06
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS currency_rate (
          currency TEXT PRIMARY KEY,
          to_eur NUMERIC NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    # Production already had this lookup before local migrations tracked it.
    # Avoid a destructive DROP on downgrade; operators can drop manually.
    pass
