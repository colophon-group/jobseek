-- Exact completion receipt commits only after acknowledged SAVE and full
-- source/target/canonical readback. Neither manifest nor receipt is rewritten.
CREATE TABLE public.crawler_ownership_b0_forward_completion (
    plan_sha256 text PRIMARY KEY REFERENCES public.crawler_ownership_b0_forward(plan_sha256),
    source_revision text NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    receipt_sha256 text NOT NULL UNIQUE CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 33554432),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (receipt_sha256=encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK ((payload::jsonb->>'version'='jobseek.crawler.cold-b0-forward-application/v1') IS TRUE),
    CHECK ((payload::jsonb->>'plan_sha256'=plan_sha256) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'source_revision'=source_revision) IS TRUE)
);
CREATE FUNCTION public.jobseek_crawler_ownership_b0_forward_completion()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE approved public.crawler_ownership_b0_forward%ROWTYPE;
    journal public.crawler_ownership_transition%ROWTYPE;
    current_epoch bigint; allocator_called boolean;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    SELECT * INTO approved FROM public.crawler_ownership_b0_forward WHERE plan_sha256=NEW.plan_sha256;
    IF NOT FOUND OR approved.source_revision <> NEW.source_revision
        OR (NEW.payload::jsonb->'request') IS DISTINCT FROM (approved.payload::jsonb->'request')
        OR (NEW.payload::jsonb->>'target_sha256') IS DISTINCT FROM approved.target_sha256
        OR (NEW.payload::jsonb->'projected_lifetime_occupancy') IS DISTINCT FROM (approved.payload::jsonb->'projected_lifetime_occupancy')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_forward_completion_binding_rejected'; END IF;
    SELECT * INTO journal FROM public.crawler_ownership_transition WHERE intent_sha256=approved.intent_sha256;
    IF NOT FOUND OR journal.phase <> 'reserved' OR journal.source_revision <> NEW.source_revision
        OR journal.routing_epoch IS DISTINCT FROM approved.routing_epoch
        OR journal.reserved_plan_sha256 IS DISTINCT FROM approved.ordinary_plan_sha256
        OR NOT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan
            WHERE plan_sha256=approved.ordinary_plan_sha256 AND state='staged'
            AND source_revision=approved.source_revision AND routing_epoch=approved.routing_epoch)
    THEN RAISE EXCEPTION 'crawler_ownership_b0_forward_completion_journal_rejected'; END IF;
    SELECT last_value,is_called INTO current_epoch,allocator_called FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true OR current_epoch IS DISTINCT FROM approved.routing_epoch
        OR EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_forward_completion_epoch_rejected'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_b0_forward_completion
BEFORE INSERT OR UPDATE OR DELETE ON public.crawler_ownership_b0_forward_completion
FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_ownership_b0_forward_completion();
