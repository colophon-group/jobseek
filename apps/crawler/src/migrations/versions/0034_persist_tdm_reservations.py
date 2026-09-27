"""Persist mining reservations without delisting search results.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("job_board", "job_posting"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN tdm_reserved boolean NOT NULL DEFAULT false")
        op.execute(f"ALTER TABLE {table} ADD COLUMN tdm_reservation jsonb")
    op.execute("""
        CREATE FUNCTION inherit_board_tdm_reservation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE reserved boolean;
        BEGIN
            -- Serialize inserts/reassignments with a board reservation so a
            -- concurrent new posting cannot escape the board-wide update.
            SELECT tdm_reserved INTO reserved FROM job_board
            WHERE id = NEW.board_id FOR SHARE;
            NEW.tdm_reserved := NEW.tdm_reserved OR COALESCE(reserved, false);
            RETURN NEW;
        END $$;
        CREATE TRIGGER posting_inherit_tdm_reservation
        BEFORE INSERT OR UPDATE OF board_id, tdm_reserved ON job_posting
        FOR EACH ROW EXECUTE FUNCTION inherit_board_tdm_reservation();

        CREATE FUNCTION propagate_board_tdm_reservation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.tdm_reserved AND NOT OLD.tdm_reserved THEN
                UPDATE job_posting SET tdm_reserved = true,
                    updated_at = clock_timestamp()
                WHERE board_id = NEW.id AND NOT tdm_reserved;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER board_propagate_tdm_reservation
        AFTER UPDATE OF tdm_reserved ON job_board
        FOR EACH ROW EXECUTE FUNCTION propagate_board_tdm_reservation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER board_propagate_tdm_reservation ON job_board")
    op.execute("DROP FUNCTION propagate_board_tdm_reservation()")
    op.execute("DROP TRIGGER posting_inherit_tdm_reservation ON job_posting")
    op.execute("DROP FUNCTION inherit_board_tdm_reservation()")
    for table in ("job_posting", "job_board"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN tdm_reserved, DROP COLUMN tdm_reservation")
