"""Serialize Lightpanda B0 epoch retirement with Go fence writes.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-12
"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

# One database-global lock serializes the non-transactional sequence high-water
# with every Go fence write. The numeric key is a durable protocol constant.
ROUTING_EPOCH_ADVISORY_LOCK_ID = 7_544_422_533_504_811_009

_INSTALL_SERIALIZED_GO_HIGH_WATER_TRIGGER = r"""
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
    -- Held until the surrounding fence transaction ends. An exclusive
    -- allocator therefore orders either wholly before or wholly after this
    -- write; it cannot advance between the check and commit.
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(7544422533504811009);
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
$$
"""

_RESTORE_UNSERIALIZED_GO_HIGH_WATER_TRIGGER = r"""
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
$$
"""


def upgrade() -> None:
    op.execute(_INSTALL_SERIALIZED_GO_HIGH_WATER_TRIGGER)


def downgrade() -> None:
    op.execute(_RESTORE_UNSERIALIZED_GO_HIGH_WATER_TRIGGER)
