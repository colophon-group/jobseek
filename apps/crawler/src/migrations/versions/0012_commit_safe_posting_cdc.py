"""Make job_posting CDC timestamps commit-order safe.

PostgreSQL ``now()`` is the transaction-start timestamp, not the commit
timestamp. A writer could therefore stamp a row behind the exporter's cursor,
remain invisible to the exporter's statement snapshot, then commit after the
cursor advanced. Install a shared-writer/exclusive-exporter advisory barrier
and stamp exported field changes with ``clock_timestamp()`` only after the
writer holds the shared side.

Revision ID: 0012
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Keep this value in lockstep with ``CDC_WRITER_BARRIER_ID`` in
# ``src/export_cursor_fence.py``. Migrations remain self-contained so a future
# runtime refactor cannot change the meaning of an already-applied revision.
_CDC_WRITER_BARRIER_ID = 18_933_879_273_374_539

# These are the mutable fields propagated by PostingSchema's downstream
# upsert. Identity columns are immutable after insert. ``last_seen_at`` and
# scheduling/lease/enrichment bookkeeping are intentionally not CDC signals.
_EXPORTED_MUTABLE_COLUMNS = (
    "is_active",
    "titles",
    "locales",
    "location_ids",
    "location_types",
    "employment_type",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_eur",
    "experience_min",
    "experience_max",
    "occupation_id",
    "seniority_id",
    "technology_ids",
    "description_r2_hash",
)
_TRIGGER_UPDATE_COLUMNS = (*_EXPORTED_MUTABLE_COLUMNS, "updated_at")


def upgrade() -> None:
    columns = ", ".join(_TRIGGER_UPDATE_COLUMNS)
    changed = ", ".join(_EXPORTED_MUTABLE_COLUMNS)

    op.execute(f"""
        CREATE OR REPLACE FUNCTION jobseek_job_posting_cdc_writer_lock()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock_shared({_CDC_WRITER_BARRIER_ID});
            RETURN NULL;
        END;
        $$
    """)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION jobseek_job_posting_cdc_stamp()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                NEW.updated_at := clock_timestamp();
            ELSIF NEW.updated_at IS DISTINCT FROM OLD.updated_at
                  OR ROW(NEW.{changed.replace(", ", ", NEW.")})
                     IS DISTINCT FROM ROW(OLD.{changed.replace(", ", ", OLD.")})
            THEN
                NEW.updated_at := clock_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$
    """)

    op.execute("DROP TRIGGER IF EXISTS job_posting_cdc_lock_insert ON job_posting")
    op.execute("DROP TRIGGER IF EXISTS job_posting_cdc_lock_update ON job_posting")
    op.execute("DROP TRIGGER IF EXISTS job_posting_cdc_stamp_insert ON job_posting")
    op.execute("DROP TRIGGER IF EXISTS job_posting_cdc_stamp_update ON job_posting")
    op.execute("""
        CREATE TRIGGER job_posting_cdc_lock_insert
        BEFORE INSERT ON job_posting
        FOR EACH STATEMENT
        EXECUTE FUNCTION jobseek_job_posting_cdc_writer_lock()
    """)
    op.execute(f"""
        CREATE TRIGGER job_posting_cdc_lock_update
        BEFORE UPDATE OF {columns} ON job_posting
        FOR EACH STATEMENT
        EXECUTE FUNCTION jobseek_job_posting_cdc_writer_lock()
    """)
    op.execute("""
        CREATE TRIGGER job_posting_cdc_stamp_insert
        BEFORE INSERT ON job_posting
        FOR EACH ROW
        EXECUTE FUNCTION jobseek_job_posting_cdc_stamp()
    """)
    op.execute(f"""
        CREATE TRIGGER job_posting_cdc_stamp_update
        BEFORE UPDATE OF {columns} ON job_posting
        FOR EACH ROW
        EXECUTE FUNCTION jobseek_job_posting_cdc_stamp()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS job_posting_cdc_stamp_update ON job_posting")
    op.execute("DROP TRIGGER IF EXISTS job_posting_cdc_stamp_insert ON job_posting")
    op.execute("DROP TRIGGER IF EXISTS job_posting_cdc_lock_update ON job_posting")
    op.execute("DROP TRIGGER IF EXISTS job_posting_cdc_lock_insert ON job_posting")
    op.execute("DROP FUNCTION IF EXISTS jobseek_job_posting_cdc_stamp()")
    op.execute("DROP FUNCTION IF EXISTS jobseek_job_posting_cdc_writer_lock()")
