-- Joint ordinary/B0 transition history. A journal is not host quiescence proof.
CREATE TABLE public.crawler_ownership_transition (
    intent_sha256 text PRIMARY KEY CHECK (intent_sha256 ~ '^[0-9a-f]{64}$'),
    transition_id uuid NOT NULL UNIQUE,
    source_revision text NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    previous_epoch bigint NOT NULL CHECK (previous_epoch BETWEEN 1 AND 9999999999999),
    prepared_plan_sha256 text NOT NULL REFERENCES public.ordinary_worker_ownership_plan(plan_sha256),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 4096),
    phase text NOT NULL DEFAULT 'pending' CHECK (phase IN ('pending','reserved','published','active','reversing','reversed','superseded')),
    routing_epoch bigint CHECK (routing_epoch > previous_epoch AND routing_epoch <= 9999999999999),
    reserved_plan_sha256 text REFERENCES public.ordinary_worker_ownership_plan(plan_sha256),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (intent_sha256 = encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK ((payload::jsonb ->> 'version' = 'jobseek.crawler.cold-transition/v1') IS TRUE),
    CHECK ((payload::jsonb ->> 'transition_id' = transition_id::text) IS TRUE),
    CHECK ((payload::jsonb ->> 'source_revision' = source_revision) IS TRUE),
    CHECK ((payload::jsonb ->> 'previous_epoch' = previous_epoch::text) IS TRUE),
    CHECK ((payload::jsonb ->> 'prepared_plan_sha256' = prepared_plan_sha256) IS TRUE),
    CHECK ((phase='pending' AND routing_epoch IS NULL AND reserved_plan_sha256 IS NULL)
        OR (phase<>'pending' AND routing_epoch IS NOT NULL AND reserved_plan_sha256 IS NOT NULL))
);
CREATE UNIQUE INDEX crawler_one_open_ownership_transition
ON public.crawler_ownership_transition ((true)) WHERE phase NOT IN ('reversed','superseded');

CREATE FUNCTION public.jobseek_crawler_ownership_journal_transition()
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
                OR (OLD.phase='reserved' AND NEW.phase='published')
                OR (OLD.phase='published' AND NEW.phase='active')
                OR (OLD.phase IN ('reserved','published','active') AND NEW.phase='reversing')
                OR (OLD.phase='reversing' AND NEW.phase='reversed')
                OR (OLD.phase='active' AND NEW.phase='superseded'))
        THEN RAISE EXCEPTION 'crawler_ownership_journal_rejected'; END IF;
    END IF;
    IF TG_OP='INSERT' OR NEW.phase='reserved' THEN
        SELECT last_value,is_called INTO current_epoch,allocator_called
        FROM public.lightpanda_b0_routing_epoch_seq;
        IF allocator_called IS DISTINCT FROM true
            OR (TG_OP='INSERT' AND current_epoch IS DISTINCT FROM NEW.previous_epoch)
            OR (NEW.phase='reserved' AND current_epoch IS DISTINCT FROM NEW.routing_epoch)
        THEN RAISE EXCEPTION 'crawler_ownership_journal_epoch_rejected'; END IF;
    END IF;
    IF NEW.phase='reserved' AND NOT EXISTS (
        SELECT 1 FROM public.ordinary_worker_ownership_plan
        WHERE plan_sha256=NEW.reserved_plan_sha256 AND source_revision=NEW.source_revision
          AND routing_epoch=NEW.routing_epoch AND state='staged')
    THEN RAISE EXCEPTION 'crawler_ownership_reserved_plan_rejected'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_journal_transition
BEFORE INSERT OR UPDATE OR DELETE ON public.crawler_ownership_transition
FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_ownership_journal_transition();
