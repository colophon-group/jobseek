-- Running upgrade 0031 -> 0032

CREATE SEQUENCE public.lightpanda_b0_routing_epoch_seq
    AS bigint
    START WITH 2
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 9999999999999
    NO CYCLE
    CACHE 1;
REVOKE ALL ON SEQUENCE public.lightpanda_b0_routing_epoch_seq FROM PUBLIC;

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
EXECUTE FUNCTION public.jobseek_lightpanda_b0_enforce_current_routing_epoch();

UPDATE alembic_version SET version_num='0032' WHERE alembic_version.version_num = '0031';
