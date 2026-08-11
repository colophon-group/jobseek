"""Track Typesense payload drift separately from ID and active-state drift.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-11
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE cross_store_reconciliation_state
        ADD COLUMN cycle_payload_mismatch BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN last_payload_mismatch BIGINT NOT NULL DEFAULT 0
    """)
    op.execute("""
        ALTER TABLE cross_store_reconciliation_run
        ADD COLUMN payload_mismatch BIGINT NOT NULL DEFAULT 0
    """)
    # A cycle started by the ID/state-only reconciler cannot certify payload
    # parity for partitions it already advanced past. Restart only the derived
    # Typesense cursor at zero; last completed-cycle evidence remains intact.
    op.execute("""
        UPDATE cross_store_reconciliation_state SET
            next_partition = 0,
            cycle_id = NULL,
            cycle_started_at = NULL,
            cycle_runtime_seconds = 0,
            cycle_local_rows = 0,
            cycle_local_active = 0,
            cycle_remote_rows = 0,
            cycle_remote_active = 0,
            cycle_missing_remote = 0,
            cycle_state_mismatch = 0,
            cycle_payload_mismatch = 0,
            cycle_remote_only_active = 0,
            cycle_remote_only_inactive = 0,
            cycle_repaired = 0,
            updated_at = clock_timestamp()
        WHERE target = 'typesense'
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE cross_store_reconciliation_run
        DROP COLUMN IF EXISTS payload_mismatch
    """)
    op.execute("""
        ALTER TABLE cross_store_reconciliation_state
        DROP COLUMN IF EXISTS last_payload_mismatch,
        DROP COLUMN IF EXISTS cycle_payload_mismatch
    """)
