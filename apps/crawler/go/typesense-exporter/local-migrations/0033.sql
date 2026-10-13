-- Running upgrade 0032 -> 0033

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
$$;

UPDATE alembic_version SET version_num='0033' WHERE alembic_version.version_num = '0032';
