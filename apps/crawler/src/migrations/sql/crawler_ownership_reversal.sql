-- Durable retirement intent. Reservation alone never authorizes restoration.
CREATE TABLE public.crawler_ownership_reversal (
    reversal_sha256 text PRIMARY KEY CHECK (reversal_sha256 ~ '^[0-9a-f]{64}$'),
    reversal_id uuid NOT NULL UNIQUE,
    forward_intent_sha256 text NOT NULL UNIQUE REFERENCES public.crawler_ownership_transition(intent_sha256),
    source_revision text NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    source_epoch bigint NOT NULL CHECK (source_epoch BETWEEN 1 AND 9999999999998),
    source_plan_sha256 text NOT NULL REFERENCES public.ordinary_worker_ownership_plan(plan_sha256),
    source_phase text NOT NULL CHECK (source_phase IN ('reserved','publishing','published','active')),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 4096),
    phase text NOT NULL DEFAULT 'pending' CHECK (phase IN ('pending','reserved')),
    retirement_epoch bigint CHECK (retirement_epoch > source_epoch AND retirement_epoch <= 9999999999999),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (reversal_sha256 = encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK ((payload::jsonb ->> 'version' = 'jobseek.crawler.cold-reversal/v1') IS TRUE),
    CHECK ((payload::jsonb ->> 'reversal_id' = reversal_id::text) IS TRUE),
    CHECK ((payload::jsonb ->> 'forward_intent_sha256' = forward_intent_sha256) IS TRUE),
    CHECK ((payload::jsonb ->> 'source_revision' = source_revision) IS TRUE),
    CHECK ((payload::jsonb ->> 'source_epoch' = source_epoch::text) IS TRUE),
    CHECK ((payload::jsonb ->> 'source_plan_sha256' = source_plan_sha256) IS TRUE),
    CHECK ((payload::jsonb ->> 'source_phase' = source_phase) IS TRUE),
    CHECK ((phase='pending' AND retirement_epoch IS NULL)
        OR (phase='reserved' AND retirement_epoch IS NOT NULL))
);

CREATE FUNCTION public.jobseek_crawler_ownership_reversal_transition()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE forward public.crawler_ownership_transition%ROWTYPE;
    current_epoch bigint; allocator_called boolean;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    SELECT * INTO forward FROM public.crawler_ownership_transition
    WHERE intent_sha256=NEW.forward_intent_sha256;
    IF NOT FOUND OR forward.source_revision IS DISTINCT FROM NEW.source_revision
        OR forward.routing_epoch IS DISTINCT FROM NEW.source_epoch
        OR forward.reserved_plan_sha256 IS DISTINCT FROM NEW.source_plan_sha256
        OR (forward.payload::jsonb->>'rollback_release_sha256') IS DISTINCT FROM
            (NEW.payload::jsonb->>'rollback_release_sha256')
        OR (forward.payload::jsonb->>'previous_ordinary_plan_sha256') IS DISTINCT FROM
            (NEW.payload::jsonb->>'rollback_ordinary_plan_sha256')
        OR (forward.payload::jsonb->>'previous_b0_receipt_sha256') IS DISTINCT FROM
            (NEW.payload::jsonb->>'rollback_b0_receipt_sha256')
    THEN RAISE EXCEPTION 'crawler_ownership_reversal_binding_rejected'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.phase <> 'pending' OR forward.phase IS DISTINCT FROM NEW.source_phase
        THEN RAISE EXCEPTION 'crawler_ownership_reversal_pending_required'; END IF;
    ELSE
        IF NEW.reversal_sha256 IS DISTINCT FROM OLD.reversal_sha256
            OR NEW.reversal_id IS DISTINCT FROM OLD.reversal_id
            OR NEW.forward_intent_sha256 IS DISTINCT FROM OLD.forward_intent_sha256
            OR NEW.source_revision IS DISTINCT FROM OLD.source_revision
            OR NEW.source_epoch IS DISTINCT FROM OLD.source_epoch
            OR NEW.source_plan_sha256 IS DISTINCT FROM OLD.source_plan_sha256
            OR NEW.source_phase IS DISTINCT FROM OLD.source_phase
            OR NEW.payload IS DISTINCT FROM OLD.payload
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR NOT (OLD.phase='pending' AND NEW.phase='reserved')
            OR forward.phase <> 'reversing'
        THEN RAISE EXCEPTION 'crawler_ownership_reversal_rejected'; END IF;
    END IF;
    SELECT last_value,is_called INTO current_epoch,allocator_called
    FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true
        OR (TG_OP='INSERT' AND current_epoch IS DISTINCT FROM NEW.source_epoch)
        OR (NEW.phase='reserved' AND current_epoch IS DISTINCT FROM NEW.retirement_epoch)
    THEN RAISE EXCEPTION 'crawler_ownership_reversal_epoch_rejected'; END IF;
    IF NEW.phase='reserved' AND EXISTS (
        SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
    THEN RAISE EXCEPTION 'crawler_ownership_reversal_owner_rejected'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_reversal_transition
BEFORE INSERT OR UPDATE OR DELETE ON public.crawler_ownership_reversal
FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_ownership_reversal_transition();

CREATE OR REPLACE FUNCTION public.jobseek_crawler_ownership_journal_transition()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE current_epoch bigint; allocator_called boolean;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.phase <> 'pending' THEN RAISE EXCEPTION 'crawler_ownership_pending_required'; END IF;
    ELSE
        IF NEW.intent_sha256 IS DISTINCT FROM OLD.intent_sha256
            OR NEW.transition_id IS DISTINCT FROM OLD.transition_id
            OR NEW.source_revision IS DISTINCT FROM OLD.source_revision
            OR NEW.previous_epoch IS DISTINCT FROM OLD.previous_epoch
            OR NEW.prepared_plan_sha256 IS DISTINCT FROM OLD.prepared_plan_sha256
            OR NEW.payload IS DISTINCT FROM OLD.payload
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR (OLD.phase <> 'pending' AND (
                NEW.routing_epoch IS DISTINCT FROM OLD.routing_epoch
                OR NEW.reserved_plan_sha256 IS DISTINCT FROM OLD.reserved_plan_sha256))
            OR NOT ((OLD.phase='pending' AND NEW.phase='reserved')
                OR (OLD.phase='reserved' AND NEW.phase='publishing')
                OR (OLD.phase='publishing' AND NEW.phase='published')
                OR (OLD.phase='published' AND NEW.phase='active')
                OR (OLD.phase IN ('reserved','publishing','published','active') AND NEW.phase='reversing')
                OR (OLD.phase='reversing' AND NEW.phase='reversed')
                OR (OLD.phase='active' AND NEW.phase='superseded'))
        THEN RAISE EXCEPTION 'crawler_ownership_journal_rejected'; END IF;
    END IF;
    IF TG_OP='INSERT' OR NEW.phase IN ('reserved','publishing','published','active') THEN
        SELECT last_value,is_called INTO current_epoch,allocator_called
        FROM public.lightpanda_b0_routing_epoch_seq;
        IF allocator_called IS DISTINCT FROM true
            OR (TG_OP='INSERT' AND current_epoch IS DISTINCT FROM NEW.previous_epoch)
            OR (TG_OP<>'INSERT' AND current_epoch IS DISTINCT FROM NEW.routing_epoch)
        THEN RAISE EXCEPTION 'crawler_ownership_journal_epoch_rejected'; END IF;
    END IF;
    IF NEW.phase IN ('reserved','publishing','published','active') AND NOT EXISTS (
        SELECT 1 FROM public.ordinary_worker_ownership_plan
        WHERE plan_sha256=NEW.reserved_plan_sha256 AND source_revision=NEW.source_revision
          AND routing_epoch=NEW.routing_epoch
          AND state=CASE WHEN NEW.phase='active' THEN 'active' ELSE 'staged' END)
    THEN RAISE EXCEPTION 'crawler_ownership_reserved_plan_rejected'; END IF;
    IF NEW.phase IN ('publishing','published','active') AND NOT EXISTS (SELECT 1 FROM public.crawler_ownership_b0_target WHERE target_sha256=NEW.payload::jsonb->>'target_b0_manifest_sha256') THEN RAISE EXCEPTION 'crawler_ownership_target_required'; END IF;
    IF NEW.phase='reversing' AND NOT EXISTS (SELECT 1 FROM public.crawler_ownership_reversal WHERE forward_intent_sha256=NEW.intent_sha256 AND source_revision=NEW.source_revision AND source_epoch=NEW.routing_epoch AND source_plan_sha256=NEW.reserved_plan_sha256 AND phase IN ('pending','reserved')) THEN RAISE EXCEPTION 'crawler_ownership_reversal_required'; END IF;
    -- Retirement is not restoration. A later verified restoration protocol must
    -- add its complete state before any reversal can release ordinary claims.
    IF NEW.phase='reversed' AND NOT EXISTS (SELECT 1 FROM public.crawler_ownership_reversal WHERE forward_intent_sha256=NEW.intent_sha256 AND phase='complete') THEN RAISE EXCEPTION 'crawler_ownership_reversal_incomplete'; END IF;
    RETURN NEW;
END;
$$;
