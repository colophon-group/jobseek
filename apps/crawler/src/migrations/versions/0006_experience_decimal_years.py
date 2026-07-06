"""Store experience requirements as decimal years.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-06
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE job_posting
          ALTER COLUMN experience_min TYPE NUMERIC(3,1)
            USING experience_min::numeric(3,1),
          ALTER COLUMN experience_max TYPE NUMERIC(3,1)
            USING experience_max::numeric(3,1)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE job_posting
          ALTER COLUMN experience_min TYPE INTEGER
            USING round(experience_min)::integer,
          ALTER COLUMN experience_max TYPE INTEGER
            USING round(experience_max)::integer
        """
    )
