"""Add the durable Lightpanda B0 routing-epoch allocator.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-11
"""

from __future__ import annotations

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_CREATE_SEQUENCE = """
CREATE SEQUENCE public.lightpanda_b0_routing_epoch_seq
    AS bigint
    START WITH 2
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 9999999999999
    NO CYCLE
    CACHE 1;
REVOKE ALL ON SEQUENCE public.lightpanda_b0_routing_epoch_seq FROM PUBLIC
"""

_REFUSE_USED_SEQUENCE_DOWNGRADE = """
DO $rollback$
BEGIN
    IF (SELECT is_called FROM public.lightpanda_b0_routing_epoch_seq) THEN
        RAISE EXCEPTION
            'Lightpanda B0 routing epoch allocator rollback refused: sequence has been used';
    END IF;
END
$rollback$
"""

_INSTALL_GO_HIGH_WATER_TRIGGER = r"""
CREATE OR REPLACE FUNCTION public.jobseek_lightpanda_b0_enforce_current_routing_epoch()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    current_epoch bigint;
    allocator_called boolean;
BEGIN
    IF NEW.engine_owner IS DISTINCT FROM 'go' THEN
        RETURN NEW;
    END IF;
    SELECT last_value, is_called
    INTO current_epoch, allocator_called
    FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true
        OR current_epoch IS DISTINCT FROM NEW.routing_epoch
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'routing_epoch_not_current';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER lightpanda_b0_write_fence_current_epoch
BEFORE INSERT OR UPDATE ON public.lightpanda_b0_write_fence
FOR EACH ROW
EXECUTE FUNCTION public.jobseek_lightpanda_b0_enforce_current_routing_epoch()
"""

_REMOVE_GO_HIGH_WATER_TRIGGER = """
DROP TRIGGER lightpanda_b0_write_fence_current_epoch
    ON public.lightpanda_b0_write_fence;
DROP FUNCTION public.jobseek_lightpanda_b0_enforce_current_routing_epoch()
"""


def upgrade() -> None:
    # Epoch 1 is the only incarnation admitted before this migration. Starting
    # at 2 makes the first dynamically reserved incarnation strictly newer.
    op.execute(_CREATE_SEQUENCE)
    op.execute(_INSTALL_GO_HIGH_WATER_TRIGGER)


def downgrade() -> None:
    # Dropping a used allocator would let a later re-upgrade restart at 2 and
    # violate the never-reused incarnation contract.
    op.execute(_REFUSE_USED_SEQUENCE_DOWNGRADE)
    op.execute(_REMOVE_GO_HIGH_WATER_TRIGGER)
    op.execute("DROP SEQUENCE public.lightpanda_b0_routing_epoch_seq")
