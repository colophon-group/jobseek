-- Full approved forward manifest is immutable history before Redis transfer.
CREATE TABLE public.crawler_ownership_b0_forward (
    plan_sha256 text PRIMARY KEY CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
    intent_sha256 text NOT NULL UNIQUE REFERENCES public.crawler_ownership_transition(intent_sha256),
    source_revision text NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    routing_epoch bigint NOT NULL CHECK (routing_epoch BETWEEN 2 AND 9999999999999),
    ordinary_plan_sha256 text NOT NULL REFERENCES public.ordinary_worker_ownership_plan(plan_sha256),
    target_sha256 text NOT NULL REFERENCES public.crawler_ownership_b0_target(target_sha256),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 33554432),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (plan_sha256=encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK ((payload::jsonb->>'version'='jobseek.crawler.cold-b0-forward/v1') IS TRUE),
    CHECK ((payload::jsonb->'request'->>'intent_sha256'=intent_sha256) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'source_revision'=source_revision) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'routing_epoch'=routing_epoch::text) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'ordinary_plan_sha256'=ordinary_plan_sha256) IS TRUE),
    CHECK ((payload::jsonb->>'target_sha256'=target_sha256) IS TRUE)
);
CREATE FUNCTION public.jobseek_crawler_ownership_b0_forward_retention()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE forward public.crawler_ownership_transition%ROWTYPE;
    target jsonb; current_epoch bigint; allocator_called boolean;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    SELECT * INTO forward FROM public.crawler_ownership_transition WHERE intent_sha256=NEW.intent_sha256;
    IF NOT FOUND OR forward.phase <> 'reserved' OR forward.source_revision <> NEW.source_revision
        OR forward.routing_epoch IS DISTINCT FROM NEW.routing_epoch
        OR forward.reserved_plan_sha256 IS DISTINCT FROM NEW.ordinary_plan_sha256
        OR forward.payload::jsonb->>'target_b0_manifest_sha256' IS DISTINCT FROM NEW.target_sha256
        OR NOT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan
            WHERE plan_sha256=NEW.ordinary_plan_sha256 AND state='staged'
            AND source_revision=NEW.source_revision AND routing_epoch=NEW.routing_epoch)
    THEN RAISE EXCEPTION 'crawler_ownership_b0_forward_binding_rejected'; END IF;
    SELECT payload::jsonb INTO target FROM public.crawler_ownership_b0_target WHERE target_sha256=NEW.target_sha256;
    IF NOT FOUND OR (NEW.payload::jsonb->'target') IS DISTINCT FROM target
    THEN RAISE EXCEPTION 'crawler_ownership_b0_forward_target_rejected'; END IF;
    SELECT last_value,is_called INTO current_epoch,allocator_called FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true OR current_epoch IS DISTINCT FROM NEW.routing_epoch
        OR EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_forward_epoch_rejected'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_b0_forward_retention
BEFORE INSERT OR UPDATE OR DELETE ON public.crawler_ownership_b0_forward
FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_ownership_b0_forward_retention();
