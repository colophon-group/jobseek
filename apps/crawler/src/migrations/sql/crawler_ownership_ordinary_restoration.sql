-- Retain ordinary rollback preparation at the already reserved retirement R.
-- This table grants neither an active owner nor joint reversal completion.
CREATE TABLE public.crawler_ownership_ordinary_restoration (
    plan_sha256 text PRIMARY KEY CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
    reversal_sha256 text NOT NULL UNIQUE REFERENCES public.crawler_ownership_reversal(reversal_sha256),
    source_revision text NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    retirement_epoch bigint NOT NULL CHECK (retirement_epoch BETWEEN 2 AND 9999999999999),
    b0_restoration_plan_sha256 text NOT NULL REFERENCES public.crawler_ownership_b0_restoration(plan_sha256),
    fresh_ordinary_plan_sha256 text REFERENCES public.ordinary_worker_ownership_plan(plan_sha256),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 67108864),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (plan_sha256=encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK ((payload::jsonb->>'version'='jobseek.crawler.cold-ordinary-restoration/v1') IS TRUE),
    CHECK ((payload::jsonb->'request'->>'reversal_sha256'=reversal_sha256) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'source_revision'=source_revision) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'retirement_epoch'=retirement_epoch::text) IS TRUE),
    CHECK ((payload::jsonb->'request'->>'b0_restoration_plan_sha256'=b0_restoration_plan_sha256) IS TRUE),
    CHECK ((COALESCE(fresh_ordinary_plan_sha256,'')=payload::jsonb->>'fresh_ordinary_plan_sha256') IS TRUE)
);
CREATE FUNCTION public.jobseek_crawler_ownership_ordinary_restoration()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE reversal public.crawler_ownership_reversal%ROWTYPE;
    b0 public.crawler_ownership_b0_restoration%ROWTYPE;
    forward public.crawler_ownership_transition%ROWTYPE;
    current_epoch bigint; allocator_called boolean; rollback_source text;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    SELECT * INTO reversal FROM public.crawler_ownership_reversal WHERE reversal_sha256=NEW.reversal_sha256;
    IF NOT FOUND OR reversal.phase <> 'reserved' OR reversal.source_revision <> NEW.source_revision
        OR reversal.retirement_epoch IS DISTINCT FROM NEW.retirement_epoch
    THEN RAISE EXCEPTION 'crawler_ownership_ordinary_restoration_reversal_rejected'; END IF;
    SELECT * INTO b0 FROM public.crawler_ownership_b0_restoration WHERE plan_sha256=NEW.b0_restoration_plan_sha256;
    IF NOT FOUND OR b0.phase <> 'fences-cleared' OR b0.reversal_sha256 <> NEW.reversal_sha256
        OR b0.source_revision <> NEW.source_revision OR b0.retirement_epoch <> NEW.retirement_epoch
    THEN RAISE EXCEPTION 'crawler_ownership_ordinary_restoration_b0_rejected'; END IF;
    SELECT * INTO forward FROM public.crawler_ownership_transition WHERE intent_sha256=reversal.forward_intent_sha256;
    IF NOT FOUND OR forward.phase <> 'reversing'
        OR (NEW.payload::jsonb->>'rollback_release_sha256') IS DISTINCT FROM (reversal.payload::jsonb->>'rollback_release_sha256')
        OR (NEW.payload::jsonb->>'previous_ordinary_plan_sha256') IS DISTINCT FROM (reversal.payload::jsonb->>'rollback_ordinary_plan_sha256')
    THEN RAISE EXCEPTION 'crawler_ownership_ordinary_restoration_binding_rejected'; END IF;
    rollback_source := NEW.payload::jsonb->'request'->>'rollback_source_revision';
    IF COALESCE(reversal.payload::jsonb->>'rollback_ordinary_plan_sha256','')='' THEN
        IF (NEW.payload::jsonb->>'mode') IS DISTINCT FROM 'legacy' OR rollback_source IS DISTINCT FROM ''
            OR NEW.fresh_ordinary_plan_sha256 IS NOT NULL
            OR (NEW.payload::jsonb->'previous_owner') IS DISTINCT FROM 'null'::jsonb
            OR (NEW.payload::jsonb->'fresh_owner') IS DISTINCT FROM 'null'::jsonb
        THEN RAISE EXCEPTION 'crawler_ownership_ordinary_restoration_legacy_rejected'; END IF;
    ELSE
        IF (NEW.payload::jsonb->>'mode') IS DISTINCT FROM 'native'
            OR rollback_source IS NULL OR rollback_source !~ '^[0-9a-f]{40}$'
            OR NOT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan p
                WHERE p.plan_sha256=reversal.payload::jsonb->>'rollback_ordinary_plan_sha256'
                AND p.state='retired' AND p.source_revision=rollback_source AND p.routing_epoch=forward.previous_epoch
                AND p.payload::jsonb=NEW.payload::jsonb->'previous_owner')
            OR NOT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan p
                WHERE p.plan_sha256=NEW.fresh_ordinary_plan_sha256 AND p.state='staged'
                AND p.source_revision=rollback_source AND p.routing_epoch=NEW.retirement_epoch
                AND p.payload::jsonb=NEW.payload::jsonb->'fresh_owner')
        THEN RAISE EXCEPTION 'crawler_ownership_ordinary_restoration_native_rejected'; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(m->>'board_id',m->>'company_id',m->>'kind',m->>'worker',m->>'profile') ORDER BY ordinal)
            FROM jsonb_array_elements(NEW.payload::jsonb->'previous_owner'->'members') WITH ORDINALITY AS members(m,ordinal))
            IS DISTINCT FROM
            (SELECT jsonb_agg(jsonb_build_array(m->>'board_id',m->>'company_id',m->>'kind',m->>'worker',m->>'profile') ORDER BY ordinal)
            FROM jsonb_array_elements(NEW.payload::jsonb->'fresh_owner'->'members') WITH ORDINALITY AS members(m,ordinal))
        THEN RAISE EXCEPTION 'crawler_ownership_ordinary_restoration_cohort_rejected'; END IF;
    END IF;
    SELECT last_value,is_called INTO current_epoch,allocator_called FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true OR current_epoch IS DISTINCT FROM NEW.retirement_epoch
        OR EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
    THEN RAISE EXCEPTION 'crawler_ownership_ordinary_restoration_epoch_rejected'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_ordinary_restoration
BEFORE INSERT OR UPDATE OR DELETE ON public.crawler_ownership_ordinary_restoration
FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_ownership_ordinary_restoration();
