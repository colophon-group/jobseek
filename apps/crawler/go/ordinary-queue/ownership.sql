-- Durable ordinary ownership; projection loss never changes the DB owner.
CREATE TABLE public.ordinary_worker_ownership_plan (
    plan_sha256 text PRIMARY KEY CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
    routing_epoch bigint NOT NULL CHECK (routing_epoch BETWEEN 1 AND 9999999999999),
    source_revision text NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 16777216),
    state text NOT NULL DEFAULT 'staged' CHECK (state IN ('staged','active','retired')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (plan_sha256 = encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK (jsonb_typeof(payload::jsonb) = 'object'),
    CHECK ((payload::jsonb ->> 'version' = 'jobseek.ordinary.ownership/v1') IS TRUE),
    CHECK ((payload::jsonb ->> 'routing_epoch' = routing_epoch::text) IS TRUE),
    CHECK ((payload::jsonb ->> 'source_revision' = source_revision) IS TRUE),
    CHECK ((jsonb_typeof(payload::jsonb -> 'members') = 'array') IS TRUE),
    CHECK ((jsonb_array_length(payload::jsonb -> 'members') BETWEEN 1 AND 20000) IS TRUE)
);
CREATE UNIQUE INDEX ordinary_worker_one_active_plan
ON public.ordinary_worker_ownership_plan ((true)) WHERE state='active';

CREATE FUNCTION public.jobseek_ordinary_ownership_transition()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE
    current_epoch bigint;
    allocator_called boolean;
BEGIN
    -- Same lock order as ordinary claims/writes/reaping and epoch retirement.
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(7544422533504811009);
    IF TG_OP='DELETE' THEN
        IF OLD.state <> 'staged' THEN
            RAISE EXCEPTION 'ordinary_ownership_retained';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='INSERT' AND NEW.state <> 'staged' THEN
        RAISE EXCEPTION 'ordinary_ownership_stage_required';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF NEW.plan_sha256 IS DISTINCT FROM OLD.plan_sha256
            OR NEW.routing_epoch IS DISTINCT FROM OLD.routing_epoch
            OR NEW.source_revision IS DISTINCT FROM OLD.source_revision
            OR NEW.payload IS DISTINCT FROM OLD.payload
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR NOT ((OLD.state='staged' AND NEW.state='active')
                OR (OLD.state='active' AND NEW.state='retired'))
        THEN
            RAISE EXCEPTION 'ordinary_ownership_transition_rejected';
        END IF;
    END IF;
    IF TG_OP='INSERT' OR NEW.state='active' THEN
        SELECT last_value,is_called INTO current_epoch,allocator_called
        FROM public.lightpanda_b0_routing_epoch_seq;
        IF allocator_called IS DISTINCT FROM true
            OR current_epoch IS DISTINCT FROM NEW.routing_epoch
        THEN
            RAISE EXCEPTION 'ordinary_ownership_epoch_rejected';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ordinary_worker_ownership_transition
BEFORE INSERT OR UPDATE OR DELETE ON public.ordinary_worker_ownership_plan
FOR EACH ROW EXECUTE FUNCTION public.jobseek_ordinary_ownership_transition();
