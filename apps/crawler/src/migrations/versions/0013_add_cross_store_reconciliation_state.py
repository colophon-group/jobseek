"""Persist deploy-independent cross-store reconciliation progress.

The exporter container is intentionally disposable, so reconciliation state
must live beside the authoritative posting data rather than in process memory.
The target rows hold the resumable partition cursor and the last completed
cycle's evidence. The run table makes interrupted one-shot containers visible
after their process and logs are gone.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE cross_store_reconciliation_state (
            target TEXT PRIMARY KEY
                CHECK (target IN ('supabase', 'typesense')),
            partition_count SMALLINT NOT NULL DEFAULT 256
                CHECK (partition_count = 256),
            next_partition SMALLINT NOT NULL DEFAULT 0
                CHECK (next_partition >= 0 AND next_partition < partition_count),
            bootstrap_complete BOOLEAN NOT NULL DEFAULT true,
            cycle_id UUID,
            cycle_started_at TIMESTAMPTZ,
            cycle_runtime_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
            cycle_local_rows BIGINT NOT NULL DEFAULT 0,
            cycle_local_active BIGINT NOT NULL DEFAULT 0,
            cycle_remote_rows BIGINT NOT NULL DEFAULT 0,
            cycle_remote_active BIGINT NOT NULL DEFAULT 0,
            cycle_missing_remote BIGINT NOT NULL DEFAULT 0,
            cycle_state_mismatch BIGINT NOT NULL DEFAULT 0,
            cycle_remote_only_active BIGINT NOT NULL DEFAULT 0,
            cycle_remote_only_inactive BIGINT NOT NULL DEFAULT 0,
            cycle_repaired BIGINT NOT NULL DEFAULT 0,
            last_started_at TIMESTAMPTZ,
            last_attempt_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
            last_local_rows BIGINT NOT NULL DEFAULT 0,
            last_local_active BIGINT NOT NULL DEFAULT 0,
            last_remote_rows BIGINT NOT NULL DEFAULT 0,
            last_remote_active BIGINT NOT NULL DEFAULT 0,
            last_missing_remote BIGINT NOT NULL DEFAULT 0,
            last_state_mismatch BIGINT NOT NULL DEFAULT 0,
            last_remote_only_active BIGINT NOT NULL DEFAULT 0,
            last_remote_only_inactive BIGINT NOT NULL DEFAULT 0,
            last_repaired BIGINT NOT NULL DEFAULT 0,
            last_unresolved BIGINT NOT NULL DEFAULT 0,
            last_outcome TEXT NOT NULL DEFAULT 'never'
                CHECK (last_outcome IN ('never', 'progress', 'clean', 'repaired', 'failed')),
            last_error_class TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        INSERT INTO cross_store_reconciliation_state (
            target,
            bootstrap_complete
        ) VALUES
            ('supabase', true),
            ('typesense', false)
    """)
    op.execute("""
        CREATE TABLE cross_store_reconciliation_run (
            run_id UUID PRIMARY KEY,
            mode TEXT NOT NULL CHECK (mode IN ('dry-run', 'repair')),
            target_scope TEXT NOT NULL
                CHECK (target_scope IN ('all', 'supabase', 'typesense')),
            started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            completed_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'success', 'failed', 'interrupted')),
            partitions_completed INTEGER NOT NULL DEFAULT 0,
            checked_local BIGINT NOT NULL DEFAULT 0,
            checked_remote BIGINT NOT NULL DEFAULT 0,
            detected BIGINT NOT NULL DEFAULT 0,
            repaired BIGINT NOT NULL DEFAULT 0,
            unresolved BIGINT NOT NULL DEFAULT 0,
            error_class TEXT
        )
    """)
    op.execute("""
        CREATE INDEX cross_store_reconciliation_run_started_idx
        ON cross_store_reconciliation_run (started_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cross_store_reconciliation_run")
    op.execute("DROP TABLE IF EXISTS cross_store_reconciliation_state")
