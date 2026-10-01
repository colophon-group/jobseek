ALTER TABLE public.crawler_ownership_transition DROP CONSTRAINT crawler_ownership_transition_phase_check;
ALTER TABLE public.crawler_ownership_transition ADD CONSTRAINT crawler_ownership_transition_phase_check
CHECK (phase IN ('pending','reserved','publishing','published','active','reversing','reversed','superseded'));

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
    RETURN NEW;
END;
$$;
