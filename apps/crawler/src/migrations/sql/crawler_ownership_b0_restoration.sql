-- Immutable approved B0 restoration manifest, before the first Redis mutation.
CREATE TABLE public.crawler_ownership_b0_restoration (
    plan_sha256 text PRIMARY KEY CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
    reversal_sha256 text NOT NULL UNIQUE REFERENCES public.crawler_ownership_reversal(reversal_sha256),
    source_revision text NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    retirement_epoch bigint NOT NULL CHECK (retirement_epoch BETWEEN 2 AND 9999999999999),
    target_sha256 text NOT NULL REFERENCES public.crawler_ownership_b0_target(target_sha256),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 33554432),
    phase text NOT NULL DEFAULT 'prepared' CHECK (phase IN ('prepared','redis-restored','fences-cleared')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (plan_sha256=encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK ((payload::jsonb->>'version'='jobseek.crawler.cold-b0-rollback/v1') IS TRUE),
    CHECK ((payload::jsonb->'request'->>'reversal_sha256'=reversal_sha256) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'source_revision'=source_revision) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'retirement_epoch'=retirement_epoch::text) IS TRUE),
    CHECK ((payload::jsonb->>'target_sha256'=target_sha256) IS TRUE)
);

CREATE FUNCTION public.jobseek_crawler_ownership_b0_restoration_transition()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE reversal public.crawler_ownership_reversal%ROWTYPE;
    forward public.crawler_ownership_transition%ROWTYPE;
    target jsonb; current_epoch bigint; allocator_called boolean; source_epoch bigint;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    SELECT * INTO reversal FROM public.crawler_ownership_reversal WHERE reversal_sha256=NEW.reversal_sha256;
    IF NOT FOUND OR reversal.phase <> 'reserved' OR reversal.source_revision <> NEW.source_revision
        OR reversal.retirement_epoch IS DISTINCT FROM NEW.retirement_epoch
    THEN RAISE EXCEPTION 'crawler_ownership_b0_restoration_binding_rejected'; END IF;
    SELECT * INTO forward FROM public.crawler_ownership_transition WHERE intent_sha256=reversal.forward_intent_sha256;
    IF NOT FOUND OR forward.phase <> 'reversing'
        OR (forward.payload::jsonb->>'target_b0_manifest_sha256') IS DISTINCT FROM NEW.target_sha256
    THEN RAISE EXCEPTION 'crawler_ownership_b0_restoration_binding_rejected'; END IF;
    SELECT payload::jsonb INTO target FROM public.crawler_ownership_b0_target WHERE target_sha256=NEW.target_sha256;
    IF NOT FOUND OR (NEW.payload::jsonb->>'namespace') IS DISTINCT FROM (target->>'namespace')
        OR (NEW.payload::jsonb->>'shard_id') IS DISTINCT FROM (target->>'shard_id')
        OR (NEW.payload::jsonb->>'cohort') IS DISTINCT FROM (target->>'cohort')
        OR COALESCE(NEW.payload::jsonb->'request'->>'source_receipt_sha256','') !~ '^[0-9a-f]{64}$'
    THEN RAISE EXCEPTION 'crawler_ownership_b0_restoration_target_rejected'; END IF;
    source_epoch := (NEW.payload::jsonb->'request'->>'b0_source_epoch')::bigint;
    IF source_epoch IS NULL OR NOT (source_epoch=reversal.source_epoch OR
        (source_epoch=forward.previous_epoch AND reversal.source_phase IN ('reserved','publishing')))
    THEN RAISE EXCEPTION 'crawler_ownership_b0_restoration_source_rejected'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.phase <> 'prepared' THEN RAISE EXCEPTION 'crawler_ownership_b0_restoration_pending_required'; END IF;
    ELSE
        IF NEW.plan_sha256 IS DISTINCT FROM OLD.plan_sha256
            OR NEW.reversal_sha256 IS DISTINCT FROM OLD.reversal_sha256
            OR NEW.source_revision IS DISTINCT FROM OLD.source_revision
            OR NEW.retirement_epoch IS DISTINCT FROM OLD.retirement_epoch
            OR NEW.target_sha256 IS DISTINCT FROM OLD.target_sha256
            OR NEW.payload IS DISTINCT FROM OLD.payload
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR NOT ((OLD.phase='prepared' AND NEW.phase='redis-restored') OR
                (OLD.phase='redis-restored' AND NEW.phase='fences-cleared'))
        THEN RAISE EXCEPTION 'crawler_ownership_b0_restoration_rejected'; END IF;
    END IF;
    SELECT last_value,is_called INTO current_epoch,allocator_called FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true OR current_epoch IS DISTINCT FROM NEW.retirement_epoch
        OR EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_restoration_epoch_rejected'; END IF;
    IF NEW.phase='fences-cleared' AND EXISTS(SELECT 1 FROM public.lightpanda_b0_write_fence
        WHERE engine_owner='go' AND shard_id=target->>'shard_id' AND routing_epoch=source_epoch)
    THEN RAISE EXCEPTION 'crawler_ownership_b0_restoration_fences_remain'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_b0_restoration_transition
BEFORE INSERT OR UPDATE OR DELETE ON public.crawler_ownership_b0_restoration
FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_ownership_b0_restoration_transition();
