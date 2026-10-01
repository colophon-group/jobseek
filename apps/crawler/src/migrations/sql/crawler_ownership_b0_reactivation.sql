-- Immutable prior-Go-B0 reactivation approval/completion at reserved retirement R.
-- No ordinary projection, active owner, reversal completion or host readiness.
CREATE TABLE public.crawler_ownership_b0_reactivation (
    plan_sha256 text PRIMARY KEY CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
    ordinary_restoration_plan_sha256 text NOT NULL UNIQUE REFERENCES public.crawler_ownership_ordinary_restoration(plan_sha256),
    source_revision text NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    retirement_epoch bigint NOT NULL CHECK (retirement_epoch BETWEEN 2 AND 9999999999999),
    target_sha256 text NOT NULL REFERENCES public.crawler_ownership_b0_target(target_sha256),
    forward_plan_sha256 text NOT NULL CHECK (forward_plan_sha256 ~ '^[0-9a-f]{64}$'),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 67108864),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (plan_sha256=encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK ((payload::jsonb->>'version'='jobseek.crawler.cold-b0-reactivation/v1') IS TRUE),
    CHECK ((payload::jsonb->'request'->>'ordinary_restoration_plan_sha256'=ordinary_restoration_plan_sha256) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'source_revision'=source_revision) IS TRUE),
    CHECK ((payload::jsonb->>'retirement_epoch'=retirement_epoch::text) IS TRUE),
    CHECK ((payload::jsonb->'forward'->>'target_sha256'=target_sha256) IS TRUE),
    CHECK ((payload::jsonb->>'forward_plan_sha256'=forward_plan_sha256) IS TRUE),
    CHECK ((payload::jsonb->>'rollback_receipt_sha256'=encode(sha256(convert_to(payload::jsonb->>'rollback_receipt','UTF8')),'hex')) IS TRUE)
);
CREATE FUNCTION public.jobseek_crawler_ownership_b0_reactivation()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE ordinary public.crawler_ownership_ordinary_restoration%ROWTYPE;
    reversal public.crawler_ownership_reversal%ROWTYPE;
    restored public.crawler_ownership_b0_restoration%ROWTYPE;
    forward public.crawler_ownership_transition%ROWTYPE;
    target jsonb; current_epoch bigint; allocator_called boolean;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    SELECT * INTO ordinary FROM public.crawler_ownership_ordinary_restoration WHERE plan_sha256=NEW.ordinary_restoration_plan_sha256;
    IF NOT FOUND OR ordinary.source_revision <> NEW.source_revision OR ordinary.retirement_epoch <> NEW.retirement_epoch
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_ordinary_rejected'; END IF;
    SELECT * INTO reversal FROM public.crawler_ownership_reversal WHERE reversal_sha256=ordinary.reversal_sha256;
    IF NOT FOUND OR reversal.phase <> 'reserved' OR reversal.source_revision <> NEW.source_revision
        OR reversal.retirement_epoch IS DISTINCT FROM NEW.retirement_epoch
        OR COALESCE(reversal.payload::jsonb->>'rollback_b0_receipt_sha256','')=''
        OR (NEW.payload::jsonb->>'rollback_receipt_sha256') IS DISTINCT FROM (reversal.payload::jsonb->>'rollback_b0_receipt_sha256')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_reversal_rejected'; END IF;
    SELECT * INTO restored FROM public.crawler_ownership_b0_restoration WHERE plan_sha256=ordinary.b0_restoration_plan_sha256;
    IF NOT FOUND OR restored.phase <> 'fences-cleared' OR restored.reversal_sha256 <> reversal.reversal_sha256
        OR restored.source_revision <> NEW.source_revision OR restored.retirement_epoch <> NEW.retirement_epoch
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_restoration_rejected'; END IF;
    SELECT * INTO forward FROM public.crawler_ownership_transition WHERE intent_sha256=reversal.forward_intent_sha256;
    IF NOT FOUND OR forward.phase <> 'reversing' OR forward.source_revision <> NEW.source_revision
        OR forward.routing_epoch IS DISTINCT FROM reversal.source_epoch
        OR forward.reserved_plan_sha256 IS DISTINCT FROM reversal.source_plan_sha256
        OR (NEW.payload::jsonb->'forward'->'request'->>'intent_sha256') IS DISTINCT FROM reversal.forward_intent_sha256
        OR (NEW.payload::jsonb->'forward'->'request'->>'source_revision') IS DISTINCT FROM NEW.source_revision
        OR (NEW.payload::jsonb->'forward'->'request'->>'routing_epoch') IS DISTINCT FROM NEW.retirement_epoch::text
        OR (NEW.payload::jsonb->'forward'->'request'->>'ordinary_plan_sha256') IS DISTINCT FROM ordinary.plan_sha256
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_forward_rejected'; END IF;
    SELECT payload::jsonb INTO target FROM public.crawler_ownership_b0_target WHERE target_sha256=NEW.target_sha256;
    IF NOT FOUND OR (NEW.payload::jsonb->'forward'->'target') IS DISTINCT FROM target
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_target_rejected'; END IF;
    IF ordinary.fresh_ordinary_plan_sha256 IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan p
        WHERE p.plan_sha256=ordinary.fresh_ordinary_plan_sha256 AND p.state='staged'
        AND p.routing_epoch=NEW.retirement_epoch AND p.source_revision=ordinary.payload::jsonb->'request'->>'rollback_source_revision')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_fresh_owner_rejected'; END IF;
    SELECT last_value,is_called INTO current_epoch,allocator_called FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true OR current_epoch IS DISTINCT FROM NEW.retirement_epoch
        OR EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_epoch_rejected'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_b0_reactivation
BEFORE INSERT OR UPDATE OR DELETE ON public.crawler_ownership_b0_reactivation
FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_ownership_b0_reactivation();

CREATE TABLE public.crawler_ownership_b0_reactivation_completion (
    plan_sha256 text PRIMARY KEY REFERENCES public.crawler_ownership_b0_reactivation(plan_sha256),
    source_revision text NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    receipt_sha256 text NOT NULL UNIQUE CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 33554432),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (receipt_sha256=encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK ((payload::jsonb->>'version'='jobseek.crawler.cold-b0-reactivation-application/v1') IS TRUE),
    CHECK ((payload::jsonb->>'plan_sha256'=plan_sha256) IS TRUE),
    CHECK ((payload::jsonb->'receipt'->'request'->>'source_revision'=source_revision) IS TRUE)
);
CREATE FUNCTION public.jobseek_crawler_ownership_b0_reactivation_completion()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE approved public.crawler_ownership_b0_reactivation%ROWTYPE;
    ordinary public.crawler_ownership_ordinary_restoration%ROWTYPE;
    reversal public.crawler_ownership_reversal%ROWTYPE;
    current_epoch bigint; allocator_called boolean;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    SELECT * INTO approved FROM public.crawler_ownership_b0_reactivation WHERE plan_sha256=NEW.plan_sha256;
    IF NOT FOUND OR approved.source_revision <> NEW.source_revision
        OR (NEW.payload::jsonb->'receipt'->>'plan_sha256') IS DISTINCT FROM approved.forward_plan_sha256
        OR (NEW.payload::jsonb->'receipt'->'request') IS DISTINCT FROM (approved.payload::jsonb->'forward'->'request')
        OR (NEW.payload::jsonb->'receipt'->>'target_sha256') IS DISTINCT FROM approved.target_sha256
        OR (NEW.payload::jsonb->'receipt'->'projected_lifetime_occupancy') IS DISTINCT FROM (approved.payload::jsonb->'forward'->'projected_lifetime_occupancy')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_completion_binding_rejected'; END IF;
    SELECT * INTO ordinary FROM public.crawler_ownership_ordinary_restoration WHERE plan_sha256=approved.ordinary_restoration_plan_sha256;
    SELECT * INTO reversal FROM public.crawler_ownership_reversal WHERE reversal_sha256=ordinary.reversal_sha256;
    IF NOT FOUND OR reversal.phase <> 'reserved' OR reversal.source_revision <> NEW.source_revision
        OR reversal.retirement_epoch IS DISTINCT FROM approved.retirement_epoch
        OR NOT EXISTS(SELECT 1 FROM public.crawler_ownership_transition WHERE intent_sha256=reversal.forward_intent_sha256 AND phase='reversing')
        OR NOT EXISTS(SELECT 1 FROM public.crawler_ownership_b0_restoration WHERE plan_sha256=ordinary.b0_restoration_plan_sha256 AND phase='fences-cleared')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_completion_context_rejected'; END IF;
    IF ordinary.fresh_ordinary_plan_sha256 IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan p
        WHERE p.plan_sha256=ordinary.fresh_ordinary_plan_sha256 AND p.state='staged'
        AND p.routing_epoch=approved.retirement_epoch AND p.source_revision=ordinary.payload::jsonb->'request'->>'rollback_source_revision')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_completion_owner_rejected'; END IF;
    SELECT last_value,is_called INTO current_epoch,allocator_called FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true OR current_epoch IS DISTINCT FROM approved.retirement_epoch
        OR EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
    THEN RAISE EXCEPTION 'crawler_ownership_b0_reactivation_completion_epoch_rejected'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_b0_reactivation_completion
BEFORE INSERT OR UPDATE OR DELETE ON public.crawler_ownership_b0_reactivation_completion
FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_ownership_b0_reactivation_completion();
